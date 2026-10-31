//! Memory lifecycle: which notes agents actually use.
//!
//! `record_use` counts one use per note when a note is handed to an agent or
//! to the CLI. The UI reads notes too, but that does not count.

use crate::access::{push_visibility, Actor};
use chrono::{DateTime, Utc};
use postgres::types::ToSql;
use postgres::GenericClient;
use serde::Serialize;
use std::collections::HashMap;

/// Cosine at or above which two notes count as the same thing said twice,
/// for the baseline model. The active model's profile decides in practice,
/// see [`default_min_similarity`].
pub const MIN_SIMILARITY: f64 = 0.92;

/// Consolidation threshold of the active embedding model; the baseline
/// constant when no backend is available.
pub fn default_min_similarity(backend: Option<&crate::embedding::EmbeddingBackend>) -> f64 {
    backend.map_or(MIN_SIMILARITY, |b| b.profile.consolidation_min_similarity)
}

/// Lowest cosine a caller may ask for. Below it nearly every pair of notes
/// links up and the groups stop meaning anything.
pub const MIN_SIMILARITY_FLOOR: f64 = 0.5;

/// Accept a caller-chosen threshold only between the floor and 1.0.
pub fn check_min_similarity(v: f64) -> Result<f64, String> {
    if (MIN_SIMILARITY_FLOOR..=1.0).contains(&v) {
        Ok(v)
    } else {
        Err(format!(
            "min_similarity must be between {MIN_SIMILARITY_FLOOR} and 1"
        ))
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct GroupNote {
    pub id: i64,
    pub content: String,
    pub category: String,
    pub project_name: Option<String>,
    pub valid_from: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize)]
pub struct CandidateGroup {
    pub notes: Vec<GroupNote>,
    pub min_similarity: f64,
    pub proposal: String,
}

/// Why a merge was refused. Nothing is written in any of these cases.
#[derive(Debug)]
pub enum ConsolidateError {
    /// Fewer than two distinct notes.
    TooFew,
    EmptyContent,
    InvalidCategory(String),
    NotFound(i64),
    AlreadySuperseded(i64),
    MixedProjects,
    /// Shared and private notes, or private notes of two owners.
    MixedVisibility,
    Db(anyhow::Error),
}

impl std::fmt::Display for ConsolidateError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::TooFew => write!(f, "a merge needs at least two notes"),
            Self::EmptyContent => write!(f, "content is required"),
            Self::InvalidCategory(c) => write!(f, "unknown category: {c}"),
            Self::NotFound(id) => write!(f, "memory note {id} not found"),
            Self::AlreadySuperseded(id) => write!(f, "memory note {id} is already superseded"),
            Self::MixedProjects => write!(f, "the notes belong to different projects"),
            Self::MixedVisibility => write!(f, "shared and private notes cannot be merged"),
            Self::Db(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for ConsolidateError {}

impl From<postgres::Error> for ConsolidateError {
    fn from(e: postgres::Error) -> Self {
        Self::Db(anyhow::anyhow!(e))
    }
}

impl From<anyhow::Error> for ConsolidateError {
    fn from(e: anyhow::Error) -> Self {
        Self::Db(e)
    }
}

fn find(parent: &mut HashMap<i64, i64>, x: i64) -> i64 {
    let p = parent[&x];
    if p == x {
        return x;
    }
    let root = find(parent, p);
    parent.insert(x, root);
    root
}

/// Groups of currently valid notes that say nearly the same thing: same
/// project (or both without one), same visibility (private ones of the same
/// owner), both with a vector for `model`, cosine at least `min_similarity`.
/// A group is a connected component of such pairs. Only notes `actor` sees
/// take part. Largest groups first, then by smallest id. Nothing is changed.
pub fn consolidation_candidates<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    model: &str,
    column: &'static str,
    min_similarity: f64,
) -> anyhow::Result<Vec<CandidateGroup>> {
    let predicate = crate::temporal::as_of_predicate("$1", "$2");
    let now = Some(Utc::now());
    let known_at: Option<DateTime<Utc>> = None;
    let mut params: Vec<&(dyn ToSql + Sync)> = vec![&now, &known_at, &model, &min_similarity];
    let seen = push_visibility(actor, "mc", &mut params);
    let rows = client.query(
        &format!(
            "WITH cur AS (
                 SELECT mc.id, mc.project_name, mc.visibility, mc.owner_id, e.{column} AS v
                 FROM memory_chunks mc
                 JOIN embeddings e ON e.source_type = 'memory_chunk'
                  AND e.source_id = mc.id AND e.model = $3
                 WHERE COALESCE(mc.status, 'active') NOT IN ('forgotten', 'superseded', 'merged')
                   AND mc.superseded_by IS NULL AND e.{column} IS NOT NULL AND {predicate}
                   AND {seen})
             SELECT a.id, b.id, (1 - (a.v <=> b.v))::float8 AS sim
             FROM cur a JOIN cur b
               ON a.id < b.id AND a.project_name IS NOT DISTINCT FROM b.project_name
              AND a.visibility = b.visibility
              AND (a.visibility = 'shared' OR a.owner_id = b.owner_id)
             WHERE 1 - (a.v <=> b.v) >= $4"
        ),
        &params,
    )?;
    let mut parent: HashMap<i64, i64> = HashMap::new();
    let mut pairs: Vec<(i64, i64, f64)> = Vec::new();
    for r in &rows {
        let (a, b, sim): (i64, i64, f64) = (r.get(0), r.get(1), r.get(2));
        parent.entry(a).or_insert(a);
        parent.entry(b).or_insert(b);
        let (ra, rb) = (find(&mut parent, a), find(&mut parent, b));
        if ra != rb {
            parent.insert(ra, rb);
        }
        pairs.push((a, b, sim));
    }
    if pairs.is_empty() {
        return Ok(Vec::new());
    }
    let ids: Vec<i64> = parent.keys().copied().collect();
    let mut notes: HashMap<i64, GroupNote> = HashMap::new();
    let mut params: Vec<&(dyn ToSql + Sync)> = vec![&ids];
    let seen = push_visibility(actor, "mc", &mut params);
    for r in client.query(
        &format!(
            "SELECT id, content, category::text, project_name,
                    COALESCE(valid_from, source_at, created_at)
             FROM memory_chunks mc WHERE id = ANY($1) AND {seen}"
        ),
        &params,
    )? {
        let id: i64 = r.get(0);
        notes.insert(
            id,
            GroupNote {
                id,
                content: r.get(1),
                category: r.get(2),
                project_name: r.get(3),
                valid_from: r.get(4),
            },
        );
    }
    let mut min_sim: HashMap<i64, f64> = HashMap::new();
    for (a, _, sim) in &pairs {
        let root = find(&mut parent, *a);
        let e = min_sim.entry(root).or_insert(*sim);
        *e = e.min(*sim);
    }
    let mut by_root: HashMap<i64, Vec<GroupNote>> = HashMap::new();
    for id in &ids {
        let root = find(&mut parent, *id);
        if let Some(n) = notes.remove(id) {
            by_root.entry(root).or_default().push(n);
        }
    }
    let mut groups: Vec<CandidateGroup> = by_root
        .into_iter()
        .filter(|(_, g)| g.len() > 1)
        .map(|(root, mut g)| {
            g.sort_by_key(|n| n.id);
            let proposal = longest_text(g.iter().map(|n| n.content.as_str())).to_string();
            CandidateGroup {
                notes: g,
                min_similarity: min_sim[&root],
                proposal,
            }
        })
        .collect();
    groups.sort_by_key(|g| (std::cmp::Reverse(g.notes.len()), g.notes[0].id));
    Ok(groups)
}

/// The longest text; on a tie the first one.
pub fn longest_text<'a>(texts: impl Iterator<Item = &'a str>) -> &'a str {
    let mut best = "";
    let mut best_len = 0;
    for t in texts {
        let len = t.chars().count();
        if len > best_len {
            best = t;
            best_len = len;
        }
    }
    best
}

/// Merge `ids` into one new note holding `content`. The sources are closed
/// like a supersede (they stay in History, a forgotten one stays forgotten);
/// no source changes its text. One transaction: any refusal writes nothing.
/// A note `actor` does not see counts as missing. The sources share one
/// visibility; merged private notes stay private to their owner, merged
/// shared notes give a shared note of `actor`. Returns the id of the new note.
pub fn consolidate<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    ids: &[i64],
    content: &str,
    category: Option<&str>,
) -> Result<i64, ConsolidateError> {
    let mut ids = ids.to_vec();
    ids.sort_unstable();
    ids.dedup();
    if ids.len() < 2 {
        return Err(ConsolidateError::TooFew);
    }
    let content = content.trim();
    if content.is_empty() {
        return Err(ConsolidateError::EmptyContent);
    }
    let category = category.map(str::trim);
    if let Some(c) = category {
        if !crate::server::MEMORY_CATEGORIES.contains(&c) {
            return Err(ConsolidateError::InvalidCategory(c.to_string()));
        }
    }
    let mut tx = client.transaction()?;
    let mut params: Vec<&(dyn ToSql + Sync)> = vec![&ids];
    let seen = push_visibility(actor, "mc", &mut params);
    let rows = tx.query(
        &format!(
            "SELECT id, category::text, project_name, COALESCE(status, 'active'), superseded_by,
                    confidence::float8, length(content), visibility, owner_id
             FROM memory_chunks mc WHERE id = ANY($1) AND {seen} ORDER BY id FOR UPDATE"
        ),
        &params,
    )?;
    if let Some(missing) = ids
        .iter()
        .find(|id| !rows.iter().any(|r| r.get::<_, i64>(0) == **id))
    {
        return Err(ConsolidateError::NotFound(*missing));
    }
    let mut project: Option<Option<String>> = None;
    let mut visibility: Option<(String, Option<i64>)> = None;
    let mut confidence = f64::MIN;
    // category -> (count, longest content length among its notes, lowest id)
    let mut cats: HashMap<String, (usize, i32, i64)> = HashMap::new();
    for r in &rows {
        let id: i64 = r.get(0);
        let status: String = r.get(3);
        let superseded_by: Option<i64> = r.get(4);
        if superseded_by.is_some() || status == "superseded" || status == "merged" {
            return Err(ConsolidateError::AlreadySuperseded(id));
        }
        let p: Option<String> = r.get(2);
        match &project {
            None => project = Some(p),
            Some(first) if *first != p => return Err(ConsolidateError::MixedProjects),
            Some(_) => {}
        }
        let v: String = r.get(7);
        // Shared notes of several owners merge; private ones only with their owner's.
        let owner: Option<i64> = if v == "private" { r.get(8) } else { None };
        match &visibility {
            None => visibility = Some((v, owner)),
            Some((first, first_owner)) if (first.as_str(), *first_owner) != (v.as_str(), owner) => {
                return Err(ConsolidateError::MixedVisibility)
            }
            Some(_) => {}
        }
        confidence = confidence.max(r.get::<_, Option<f64>>(5).unwrap_or(0.0));
        let len: i32 = r.get::<_, Option<i32>>(6).unwrap_or(0);
        let e = cats.entry(r.get(1)).or_insert((0, len, id));
        e.0 += 1;
        e.1 = e.1.max(len);
    }
    let category = match category {
        Some(c) => c.to_string(),
        None => cats
            .into_iter()
            .max_by_key(|(_, (n, len, id))| (*n, *len, std::cmp::Reverse(*id)))
            .map(|(c, _)| c)
            .expect("at least two notes"),
    };
    let project = project.flatten();
    let (visibility, owner) = match visibility.expect("at least two notes") {
        (v, Some(owner)) => (v, Some(owner)),
        (v, None) => (v, actor.user_id),
    };
    let category_type = crate::extract_memory::category_column_type(&mut tx)?;
    let new_id: i64 = tx
        .query_one(
            &format!(
                "INSERT INTO memory_chunks
                     (source_type, content, category, project_name, confidence, status,
                      merged_from, valid_from, owner_id, visibility)
                 VALUES ('consolidation', $1, $2::text::{category_type}, $3, $4::float8,
                         'active', $5, now(), $6, $7)
                 RETURNING id"
            ),
            &[
                &content,
                &category,
                &project,
                &confidence,
                &ids,
                &owner,
                &visibility,
            ],
        )?
        .get(0);
    tx.execute(
        "UPDATE memory_chunks
         SET status = CASE WHEN status = 'forgotten' THEN status ELSE 'superseded' END,
             superseded_by = $2, superseded_at = now(), updated_at = now(),
             valid_to = LEAST(valid_to, now())
         WHERE id = ANY($1)",
        &[&ids, &new_id],
    )?;
    // Stars are UI marks, not memory: the actor's stars on the sources become
    // one star on the new note. Other accounts' stars stay; a job (no
    // account) moves only the stars without an account, i.e. local mode's.
    let refs: Vec<String> = ids.iter().map(|i| i.to_string()).collect();
    let new_ref = new_id.to_string();
    let mut star: Vec<&(dyn ToSql + Sync)> = vec![&refs, &new_ref];
    let mine = crate::favorites::push_mine(actor, "f", &mut star);
    tx.execute(
        &format!(
            "INSERT INTO favorites (kind, ref, user_id)
             SELECT 'memory', $2, $3 WHERE EXISTS
                 (SELECT 1 FROM favorites f WHERE f.kind = 'memory' AND f.ref = ANY($1) AND {mine})
             ON CONFLICT DO NOTHING"
        ),
        &star,
    )?;
    let mut star: Vec<&(dyn ToSql + Sync)> = vec![&refs];
    let mine = crate::favorites::push_mine(actor, "f", &mut star);
    tx.execute(
        &format!("DELETE FROM favorites f WHERE f.kind = 'memory' AND f.ref = ANY($1) AND {mine}"),
        &star,
    )?;
    tx.commit()?;
    Ok(new_id)
}

/// Count one use for each note in `ids`. A repeated id counts once. Only
/// `access_count` and `last_accessed` change. Returns the rows updated.
/// Row locks are taken in id order, as `consolidate` does, so two writers
/// touching overlapping notes cannot deadlock.
const RECORD_USE_SQL: &str = "UPDATE memory_chunks mc \
         SET access_count = COALESCE(mc.access_count, 0) + 1, last_accessed = now() \
         FROM (SELECT id FROM memory_chunks WHERE id = ANY($1) ORDER BY id FOR UPDATE) s \
         WHERE mc.id = s.id";

pub fn record_use<C: GenericClient>(client: &mut C, ids: &[i64]) -> anyhow::Result<u64> {
    let mut ids = ids.to_vec();
    ids.sort_unstable();
    ids.dedup();
    if ids.is_empty() {
        return Ok(0);
    }
    Ok(client.execute(RECORD_USE_SQL, &[&ids])?)
}

/// How strong a note is: it fades with the days since its last use and
/// holds longer the more it was used. Pinned notes and decisions fade four
/// times slower. Between 0.05 and 1.0.
///
/// `half_life = 30 * (1 + ln(1 + access_count))` days, times 4 if pinned or
/// a decision; `strength = 0.05 + 0.95 * 2^(-days / half_life)`.
pub fn strength(days_since_use: f64, access_count: i64, pinned_or_decision: bool) -> f64 {
    let mut half_life = 30.0 * (1.0 + (1.0 + access_count.max(0) as f64).ln());
    if pinned_or_decision {
        half_life *= 4.0;
    }
    let days = days_since_use.max(0.0);
    (0.05 + 0.95 * (-days / half_life).exp2()).clamp(0.05, 1.0)
}

/// A read path must not fail because of a counter: report and go on.
pub fn record_use_or_log<C: GenericClient>(client: &mut C, ids: &[i64]) {
    if let Err(e) = record_use(client, ids) {
        eprintln!("could not count note use: {e:#}");
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn consolidation_threshold_follows_the_profile() {
        let backend = |model: &str| {
            let profile = crate::embedding::profile_for(model, 768).unwrap();
            crate::embedding::EmbeddingBackend {
                name: "ollama".into(),
                model: model.into(),
                base_url: "http://127.0.0.1:11434".into(),
                column: profile.column,
                dim: profile.dim,
                profile,
            }
        };
        let granite = backend("granite-embedding-multilingual:278m");
        assert_eq!(default_min_similarity(Some(&granite)), 0.93);
        let base = backend("nomic-embed-text");
        assert_eq!(default_min_similarity(Some(&base)), 0.92);
        assert_eq!(default_min_similarity(None), MIN_SIMILARITY);
    }

    use super::*;

    fn test_db() -> Option<postgres::Client> {
        use postgres::NoTls;
        let host = std::env::var("PGHOST").unwrap_or_else(|_| "localhost".into());
        let user = std::env::var("PGUSER")
            .unwrap_or_else(|_| std::env::var("USERNAME").unwrap_or_else(|_| "postgres".into()));
        let dbname = std::env::var("PGDATABASE").unwrap_or_else(|_| "throughline".into());
        let mut cfg = postgres::Config::new();
        cfg.host(&host)
            .port(
                std::env::var("PGPORT")
                    .ok()
                    .and_then(|p| p.parse().ok())
                    .unwrap_or(5432),
            )
            .user(&user)
            .dbname(&dbname);
        if let Ok(pw) = std::env::var("PGPASSWORD") {
            cfg.password(pw);
        }
        cfg.connect(NoTls).ok()
    }

    fn seed_note<C: GenericClient>(client: &mut C, content: &str) -> i64 {
        let category_type =
            crate::extract_memory::category_column_type(client).expect("category_column_type");
        client
            .query_one(
                &format!(
                    "INSERT INTO memory_chunks (source_type, content, category, confidence, status)
                     VALUES ('manual', $1, 'decision'::text::{category_type}, 1.0, 'active')
                     RETURNING id"
                ),
                &[&content],
            )
            .expect("seed note")
            .get(0)
    }

    fn count<C: GenericClient>(client: &mut C, id: i64) -> (i32, bool) {
        let row = client
            .query_one(
                "SELECT COALESCE(access_count, 0), last_accessed IS NOT NULL \
                 FROM memory_chunks WHERE id = $1",
                &[&id],
            )
            .expect("row");
        (row.get(0), row.get(1))
    }

    #[test]
    fn strength_golden_values() {
        assert!((strength(0.0, 0, false) - 1.0).abs() < 1e-12);
        assert!((strength(30.0, 0, false) - 0.525).abs() < 1e-12);
        // 3 uses: half-life 30 * (1 + ln 4)
        let hl = 30.0 * (1.0 + 4.0_f64.ln());
        assert!((strength(hl, 3, false) - 0.525).abs() < 1e-12);
        // pinned: half-life 120
        assert!((strength(120.0, 0, true) - 0.525).abs() < 1e-12);
    }

    #[test]
    fn strength_halves_after_one_half_life() {
        let s = strength(30.0, 0, false);
        assert!((s - (0.05 + 0.95 * 0.5)).abs() < 1e-9, "{s}");
    }

    #[test]
    fn strength_reinforced_by_use() {
        assert!(strength(30.0, 10, false) > strength(30.0, 0, false));
        assert!(strength(30.0, 10, false) > strength(30.0, 1, false));
    }

    #[test]
    fn strength_floor() {
        assert!((strength(10_000.0, 0, false) - 0.05).abs() < 1e-9);
    }

    #[test]
    fn strength_pinned_decays_slower() {
        assert!(strength(60.0, 0, true) > strength(60.0, 0, false));
    }

    #[test]
    fn strength_is_capped() {
        assert_eq!(strength(-5.0, 0, false), 1.0);
        assert!(strength(0.0, 100, true) <= 1.0);
    }

    #[test]
    fn record_use_counts_each_id_once() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping record_use_counts_each_id_once: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let a = seed_note(&mut tx, &format!("use count note A {tag}"));
        let b = seed_note(&mut tx, &format!("use count note B {tag}"));
        let c = seed_note(&mut tx, &format!("use count note C {tag}"));
        assert_eq!(count(&mut tx, a), (0, false));
        assert_eq!(record_use(&mut tx, &[a, a, b]).expect("record"), 2);
        assert_eq!(count(&mut tx, a), (1, true));
        assert_eq!(count(&mut tx, b), (1, true));
        assert_eq!(count(&mut tx, c), (0, false));
        assert_eq!(record_use(&mut tx, &[a]).expect("record"), 1);
        assert_eq!(count(&mut tx, a).0, 2);
        assert_eq!(record_use(&mut tx, &[]).expect("empty"), 0);
        assert_eq!(count(&mut tx, a).0, 2);
    }

    #[test]
    fn record_use_leaves_other_columns() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping record_use_leaves_other_columns: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let content = format!("use count untouched {tag}");
        let a = seed_note(&mut tx, &content);
        tx.execute(
            "UPDATE memory_chunks SET updated_at = '2026-01-01T00:00:00Z' WHERE id = $1",
            &[&a],
        )
        .expect("pin updated_at");
        let before = tx
            .query_one(
                "SELECT updated_at, content, status, confidence::float8 \
                 FROM memory_chunks WHERE id = $1",
                &[&a],
            )
            .expect("before");
        let before: (chrono::DateTime<chrono::Utc>, String, String, f64) =
            (before.get(0), before.get(1), before.get(2), before.get(3));
        assert_eq!(record_use(&mut tx, &[a]).expect("record"), 1);
        let after = tx
            .query_one(
                "SELECT updated_at, content, status, confidence::float8 \
                 FROM memory_chunks WHERE id = $1",
                &[&a],
            )
            .expect("after");
        let after: (chrono::DateTime<chrono::Utc>, String, String, f64) =
            (after.get(0), after.get(1), after.get(2), after.get(3));
        assert_eq!(before, after);
        assert_eq!(after.1, content);
        assert_eq!(count(&mut tx, a), (1, true));
    }

    // ---- consolidation ----

    fn seed_full<C: GenericClient>(
        client: &mut C,
        content: &str,
        category: &str,
        project: Option<&str>,
        confidence: f64,
    ) -> i64 {
        let category_type =
            crate::extract_memory::category_column_type(client).expect("category_column_type");
        client
            .query_one(
                &format!(
                    "INSERT INTO memory_chunks (source_type, content, category, project_name, confidence, status)
                     VALUES ('manual', $1, $2::text::{category_type}, $3, $4::float8, 'active')
                     RETURNING id"
                ),
                &[&content, &category, &project, &confidence],
            )
            .expect("seed note")
            .get(0)
    }

    /// 768 dimensions, the first two carry the angle.
    fn vec2(x: f32, y: f32) -> Vec<f32> {
        let mut v = vec![0.0_f32; 768];
        v[0] = x;
        v[1] = y;
        v
    }

    fn embed<C: GenericClient>(client: &mut C, id: i64, model: &str, v: &[f32]) {
        client
            .execute(
                "INSERT INTO embeddings (source_type, source_id, model, embedding_768) \
                 VALUES ('memory_chunk', $1, $2, $3::text::vector)",
                &[&id, &model, &crate::generate_embeddings::vector_literal(v)],
            )
            .expect("embed");
    }

    fn star<C: GenericClient>(client: &mut C, id: i64) {
        client
            .execute(
                "INSERT INTO favorites (kind, ref) VALUES ('memory', $1::bigint::text)",
                &[&id],
            )
            .expect("star");
    }

    fn stars<C: GenericClient>(client: &mut C, id: i64) -> i64 {
        client
            .query_one(
                "SELECT count(*) FROM favorites WHERE kind = 'memory' AND ref = $1::bigint::text",
                &[&id],
            )
            .expect("count stars")
            .get(0)
    }

    #[test]
    fn consolidate_changes_sources_only_in_allowed_columns() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping consolidate_changes_sources_only_in_allowed_columns: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let a = seed_full(&mut tx, &format!("same thing {tag}"), "decision", None, 0.6);
        let b = seed_full(
            &mut tx,
            &format!("same thing, said longer {tag}"),
            "pattern",
            None,
            0.9,
        );
        let snap = "SELECT (to_jsonb(mc) - 'status' - 'superseded_by' - 'superseded_at' \
                    - 'valid_to' - 'updated_at')::text FROM memory_chunks mc WHERE id = $1";
        let before: Vec<String> = [a, b]
            .iter()
            .map(|id| tx.query_one(snap, &[id]).expect("before").get(0))
            .collect();
        let new_id = consolidate(
            &mut tx,
            &Actor::local(),
            &[a, b],
            &format!("same thing, said longer {tag}"),
            None,
        )
        .expect("consolidate");
        for (id, was) in [a, b].iter().zip(before) {
            let now: String = tx.query_one(snap, &[id]).expect("after").get(0);
            assert_eq!(now, was, "note {id} changed outside the allowed columns");
            let row = tx
                .query_one(
                    "SELECT status, superseded_by, valid_to IS NOT NULL FROM memory_chunks WHERE id = $1",
                    &[id],
                )
                .expect("row");
            assert_eq!(row.get::<_, String>(0), "superseded");
            assert_eq!(row.get::<_, Option<i64>>(1), Some(new_id));
            assert!(row.get::<_, bool>(2));
        }
        let n = tx
            .query_one(
                "SELECT source_type, merged_from, category::text, confidence::float8, status, project_name \
                 FROM memory_chunks WHERE id = $1",
                &[&new_id],
            )
            .expect("new note");
        assert_eq!(n.get::<_, String>(0), "consolidation");
        assert_eq!(n.get::<_, Vec<i64>>(1), vec![a, b]);
        // tie on frequency, the longest note decides: pattern
        assert_eq!(n.get::<_, String>(2), "pattern");
        assert!((n.get::<_, f64>(3) - 0.9).abs() < 1e-6);
        assert_eq!(n.get::<_, String>(4), "active");
        assert_eq!(n.get::<_, Option<String>>(5), None);
    }

    #[test]
    fn consolidate_is_all_or_nothing() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping consolidate_is_all_or_nothing: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let a = seed_full(&mut tx, &format!("aon A {tag}"), "decision", None, 1.0);
        let b = seed_full(&mut tx, &format!("aon B {tag}"), "decision", None, 1.0);
        let c = seed_full(&mut tx, &format!("aon C {tag}"), "decision", None, 1.0);
        tx.execute(
            "UPDATE memory_chunks SET status = 'superseded', superseded_by = $2 WHERE id = $1",
            &[&c, &a],
        )
        .expect("supersede c");
        let text = format!("aon merged {tag}");
        let err = consolidate(&mut tx, &Actor::local(), &[a, b, c], &text, None).unwrap_err();
        assert!(
            matches!(err, ConsolidateError::AlreadySuperseded(id) if id == c),
            "{err:?}"
        );
        let err =
            consolidate(&mut tx, &Actor::local(), &[a, b, i64::MAX], &text, None).unwrap_err();
        assert!(
            matches!(err, ConsolidateError::NotFound(id) if id == i64::MAX),
            "{err:?}"
        );
        let err = consolidate(&mut tx, &Actor::local(), &[a], &text, None).unwrap_err();
        assert!(matches!(err, ConsolidateError::TooFew), "{err:?}");
        let err = consolidate(&mut tx, &Actor::local(), &[a, b], "  ", None).unwrap_err();
        assert!(matches!(err, ConsolidateError::EmptyContent), "{err:?}");
        let err =
            consolidate(&mut tx, &Actor::local(), &[a, b], &text, Some("nonsense")).unwrap_err();
        assert!(
            matches!(err, ConsolidateError::InvalidCategory(_)),
            "{err:?}"
        );
        for id in [a, b] {
            let row = tx
                .query_one(
                    "SELECT status, superseded_by, valid_to FROM memory_chunks WHERE id = $1",
                    &[&id],
                )
                .expect("row");
            assert_eq!(row.get::<_, String>(0), "active", "note {id}");
            assert_eq!(row.get::<_, Option<i64>>(1), None);
            assert!(row.get::<_, Option<DateTime<Utc>>>(2).is_none());
        }
        let made: i64 = tx
            .query_one(
                "SELECT count(*) FROM memory_chunks WHERE content = $1",
                &[&text],
            )
            .expect("count")
            .get(0);
        assert_eq!(made, 0, "a failed merge must not leave a new note");
    }

    #[test]
    fn consolidate_moves_stars_once() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping consolidate_moves_stars_once: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let a = seed_full(&mut tx, &format!("star A {tag}"), "decision", None, 1.0);
        let b = seed_full(&mut tx, &format!("star B {tag}"), "decision", None, 1.0);
        let c = seed_full(&mut tx, &format!("star C {tag}"), "decision", None, 1.0);
        star(&mut tx, a);
        star(&mut tx, b);
        let new_id = consolidate(
            &mut tx,
            &Actor::local(),
            &[a, b, c],
            &format!("star merged {tag}"),
            None,
        )
        .expect("consolidate");
        assert_eq!(stars(&mut tx, new_id), 1);
        assert_eq!(stars(&mut tx, a), 0);
        assert_eq!(stars(&mut tx, b), 0);
        // no star before, no star after
        let x = seed_full(&mut tx, &format!("nostar X {tag}"), "decision", None, 1.0);
        let y = seed_full(&mut tx, &format!("nostar Y {tag}"), "decision", None, 1.0);
        let m = consolidate(
            &mut tx,
            &Actor::local(),
            &[x, y],
            &format!("nostar merged {tag}"),
            None,
        )
        .expect("consolidate");
        assert_eq!(stars(&mut tx, m), 0);
    }

    #[test]
    fn consolidate_rejects_mixed_projects() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping consolidate_rejects_mixed_projects: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let p1 = format!("proj-a-{tag}");
        let p2 = format!("proj-b-{tag}");
        let a = seed_full(&mut tx, &format!("mix A {tag}"), "decision", Some(&p1), 1.0);
        let b = seed_full(&mut tx, &format!("mix B {tag}"), "decision", Some(&p2), 1.0);
        let c = seed_full(&mut tx, &format!("mix C {tag}"), "decision", None, 1.0);
        let err = consolidate(&mut tx, &Actor::local(), &[a, b], "merged", None).unwrap_err();
        assert!(matches!(err, ConsolidateError::MixedProjects), "{err:?}");
        let err = consolidate(&mut tx, &Actor::local(), &[a, c], "merged", None).unwrap_err();
        assert!(matches!(err, ConsolidateError::MixedProjects), "{err:?}");
        let status: String = tx
            .query_one("SELECT status FROM memory_chunks WHERE id = $1", &[&a])
            .expect("row")
            .get(0);
        assert_eq!(status, "active");
    }

    #[test]
    fn candidates_group_similar_notes() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping candidates_group_similar_notes: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let model = format!("test-model-{tag}");
        let project = format!("proj-{tag}");
        let a = seed_full(
            &mut tx,
            &format!("short {tag}"),
            "decision",
            Some(&project),
            1.0,
        );
        let b = seed_full(
            &mut tx,
            &format!("the longer version of the same note {tag}"),
            "decision",
            Some(&project),
            1.0,
        );
        let c = seed_full(
            &mut tx,
            &format!("unrelated {tag}"),
            "decision",
            Some(&project),
            1.0,
        );
        // cosine(a, b) = 0.95, cosine(a, c) = 0.5, cosine(b, c) about 0.2 (below the 0.4 bar)
        let s = (1.0_f32 - 0.95 * 0.95).sqrt();
        embed(&mut tx, a, &model, &vec2(1.0, 0.0));
        embed(&mut tx, b, &model, &vec2(0.95, s));
        embed(&mut tx, c, &model, &vec2(0.5, -(0.75_f32).sqrt()));
        let groups = consolidation_candidates(
            &mut tx,
            &Actor::local(),
            &model,
            "embedding_768",
            MIN_SIMILARITY,
        )
        .expect("candidates");
        assert_eq!(groups.len(), 1, "{groups:?}");
        let g = &groups[0];
        assert_eq!(g.notes.iter().map(|n| n.id).collect::<Vec<_>>(), vec![a, b]);
        assert!(
            (g.min_similarity - 0.95).abs() < 1e-3,
            "{}",
            g.min_similarity
        );
        assert_eq!(
            g.proposal,
            format!("the longer version of the same note {tag}")
        );
        // a lower bar pulls the third note in only if it is similar enough
        let loose =
            consolidation_candidates(&mut tx, &Actor::local(), &model, "embedding_768", 0.4)
                .expect("candidates");
        assert_eq!(loose.len(), 1);
        assert_eq!(loose[0].notes.len(), 3);
        assert!(
            (loose[0].min_similarity - 0.5).abs() < 1e-3,
            "{}",
            loose[0].min_similarity
        );
    }

    #[test]
    fn candidates_skip_notes_without_vectors() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping candidates_skip_notes_without_vectors: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let model = format!("test-model-{tag}");
        let a = seed_full(
            &mut tx,
            &format!("with vector {tag}"),
            "decision",
            None,
            1.0,
        );
        let _b = seed_full(
            &mut tx,
            &format!("without vector {tag}"),
            "decision",
            None,
            1.0,
        );
        let c = seed_full(
            &mut tx,
            &format!("other model {tag}"),
            "decision",
            None,
            1.0,
        );
        embed(&mut tx, a, &model, &vec2(1.0, 0.0));
        embed(&mut tx, c, "some-other-model", &vec2(1.0, 0.0));
        let groups = consolidation_candidates(
            &mut tx,
            &Actor::local(),
            &model,
            "embedding_768",
            MIN_SIMILARITY,
        )
        .expect("candidates");
        assert!(groups.is_empty(), "{groups:?}");
    }

    #[test]
    fn consolidate_keeps_a_forgotten_source_forgotten() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping consolidate_keeps_a_forgotten_source_forgotten: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let a = seed_full(&mut tx, &format!("forgot A {tag}"), "decision", None, 1.0);
        let b = seed_full(&mut tx, &format!("forgot B {tag}"), "decision", None, 1.0);
        tx.execute(
            "UPDATE memory_chunks SET status = 'forgotten' WHERE id = $1",
            &[&b],
        )
        .expect("forget");
        let new_id = consolidate(
            &mut tx,
            &Actor::local(),
            &[a, b],
            &format!("forgot merged {tag}"),
            None,
        )
        .expect("consolidate");
        let row = tx
            .query_one(
                "SELECT status, valid_to IS NOT NULL, superseded_by FROM memory_chunks WHERE id = $1",
                &[&b],
            )
            .expect("row");
        assert_eq!(row.get::<_, String>(0), "forgotten");
        assert!(row.get::<_, bool>(1));
        assert_eq!(row.get::<_, Option<i64>>(2), Some(new_id));
    }

    #[test]
    fn min_similarity_below_the_floor_is_rejected() {
        assert!(check_min_similarity(0.49).is_err());
        assert!(check_min_similarity(0.0).is_err());
        assert!(check_min_similarity(1.01).is_err());
        assert!(check_min_similarity(f64::NAN).is_err());
        assert_eq!(check_min_similarity(0.5), Ok(0.5));
        assert_eq!(check_min_similarity(0.92), Ok(0.92));
        assert_eq!(check_min_similarity(1.0), Ok(1.0));
    }

    #[test]
    fn consolidate_accepts_a_source_with_null_status() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping consolidate_accepts_a_source_with_null_status: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let a = seed_full(&mut tx, &format!("null A {tag}"), "decision", None, 1.0);
        let b = seed_full(&mut tx, &format!("null B {tag}"), "decision", None, 1.0);
        tx.execute(
            "UPDATE memory_chunks SET status = NULL WHERE id = $1",
            &[&b],
        )
        .expect("null status");
        let new_id = consolidate(
            &mut tx,
            &Actor::local(),
            &[a, b],
            &format!("null merged {tag}"),
            None,
        )
        .expect("consolidate");
        let row = tx
            .query_one(
                "SELECT status, superseded_by FROM memory_chunks WHERE id = $1",
                &[&b],
            )
            .expect("row");
        assert_eq!(row.get::<_, String>(0), "superseded");
        assert_eq!(row.get::<_, Option<i64>>(1), Some(new_id));
    }

    #[test]
    fn record_use_locks_rows_in_id_order() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping record_use_locks_rows_in_id_order: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let a = seed_full(&mut tx, &format!("lock A {tag}"), "decision", None, 1.0);
        let b = seed_full(&mut tx, &format!("lock B {tag}"), "decision", None, 1.0);
        let c = seed_full(&mut tx, &format!("lock C {tag}"), "decision", None, 1.0);
        // Unsorted input with a repeat: still one use each.
        assert_eq!(record_use(&mut tx, &[c, a, b, a]).expect("use"), 3);
        for id in [a, b, c] {
            assert_eq!(count(&mut tx, id).0, 1);
        }
        // The statement must take its row locks in id order (deadlock safety
        // against consolidate, which locks ORDER BY id).
        let sql = RECORD_USE_SQL.to_lowercase();
        assert!(sql.contains("order by id for update"), "{sql}");
    }

    /// Three notes saying the same thing (cosine 1): a shared one, two of
    /// A's private ones. Returns (shared, private, private2, model).
    fn same_three<C: GenericClient>(tx: &mut C, a: &Actor, tag: &str) -> (i64, i64, i64, String) {
        use crate::access::team_test::note;
        let model = format!("test-model-{tag}");
        let project = format!("vis-{tag}");
        let p = Some(project.as_str());
        let shared = note(
            tx,
            None,
            "shared",
            &format!("shared twin {tag}"),
            "decision",
            p,
        );
        let private = note(
            tx,
            a.user_id,
            "private",
            &format!("secret twin {tag}"),
            "decision",
            p,
        );
        let private2 = note(
            tx,
            a.user_id,
            "private",
            &format!("secret twin two {tag}"),
            "decision",
            p,
        );
        for id in [shared, private, private2] {
            embed(tx, id, &model, &vec2(1.0, 0.0));
        }
        (shared, private, private2, model)
    }

    #[test]
    fn consolidation_candidates_hide() {
        use crate::access::team_test::{assert_hidden, two_accounts};
        let Some(mut client) = test_db() else {
            eprintln!("skipping consolidation_candidates_hide: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let (a, b) = two_accounts(&mut tx);
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let (shared, private, private2, model) = same_three(&mut tx, &a, &tag);
        let other = seed_full(
            &mut tx,
            &format!("shared twin again {tag}"),
            "decision",
            None,
            1.0,
        );
        tx.execute(
            "UPDATE memory_chunks SET project_name = $2 WHERE id = $1",
            &[&other, &format!("vis-{tag}")],
        )
        .expect("project");
        embed(&mut tx, other, &model, &vec2(1.0, 0.0));
        let ids = |groups: &[CandidateGroup]| -> Vec<Vec<i64>> {
            groups
                .iter()
                .map(|g| g.notes.iter().map(|n| n.id).collect())
                .collect()
        };
        let mine = consolidation_candidates(&mut tx, &a, &model, "embedding_768", 0.9)
            .expect("candidates");
        assert_eq!(
            ids(&mine),
            vec![vec![shared, other], vec![private, private2]]
        );
        let theirs = consolidation_candidates(&mut tx, &b, &model, "embedding_768", 0.9)
            .expect("candidates");
        assert_eq!(ids(&theirs), vec![vec![shared, other]]);
        assert_hidden(
            &serde_json::to_string(&theirs).unwrap(),
            "secret twin",
            "the groups",
        );
        // B cannot merge or even name A's notes.
        let err = consolidate(&mut tx, &b, &[shared, private], "x", None).unwrap_err();
        assert!(
            matches!(err, ConsolidateError::NotFound(id) if id == private),
            "{err:?}"
        );
    }

    #[test]
    fn consolidation_never_crosses_visibility() {
        use crate::access::team_test::two_accounts;
        let Some(mut client) = test_db() else {
            eprintln!("skipping consolidation_never_crosses_visibility: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let (a, _) = two_accounts(&mut tx);
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let (shared, private, private2, model) = same_three(&mut tx, &a, &tag);
        // Seen by everyone (local mode) the three are still two groups.
        let groups =
            consolidation_candidates(&mut tx, &Actor::local(), &model, "embedding_768", 0.9)
                .expect("candidates");
        assert!(groups.iter().all(|g| g.notes.len() == 2), "{groups:?}");
        assert!(groups
            .iter()
            .all(|g| !(g.notes.iter().any(|n| n.id == shared)
                && g.notes.iter().any(|n| n.id == private))));
        let err = consolidate(&mut tx, &a, &[shared, private], "x", None).unwrap_err();
        assert!(matches!(err, ConsolidateError::MixedVisibility), "{err:?}");
        // A's own private notes merge into a private note of hers.
        let merged =
            consolidate(&mut tx, &a, &[private, private2], "one secret twin", None).expect("merge");
        let row = tx
            .query_one(
                "SELECT visibility, owner_id FROM memory_chunks WHERE id = $1",
                &[&merged],
            )
            .expect("row");
        assert_eq!(row.get::<_, String>(0), "private");
        assert_eq!(row.get::<_, Option<i64>>(1), a.user_id);
        // The dedup job sees shared notes only: a private twin is never merged.
        let dup = format!("dup twin {tag}");
        let s1 = crate::access::team_test::note(&mut tx, None, "shared", &dup, "decision", None);
        let p1 =
            crate::access::team_test::note(&mut tx, a.user_id, "private", &dup, "decision", None);
        let s2 = crate::access::team_test::note(&mut tx, None, "shared", &dup, "decision", None);
        crate::reflect_memory::dedup(&mut tx, &Actor::shared_only(), false).expect("dedup");
        let status = |tx: &mut postgres::Transaction<'_>, id: i64| -> String {
            tx.query_one("SELECT status FROM memory_chunks WHERE id = $1", &[&id])
                .expect("row")
                .get(0)
        };
        assert_eq!(status(&mut tx, s1), "superseded");
        assert_eq!(status(&mut tx, s2), "superseded");
        assert_eq!(status(&mut tx, p1), "active", "the private twin stays");
    }
}
