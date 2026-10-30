//! Recall of memory notes: candidate selection and a weighted ranking.
//!
//! Candidates and scores are separate steps so that other modes and an
//! evaluation can rank the same candidates in a different way.

use crate::generate_embeddings::vector_literal;
use crate::temporal;
use anyhow::{bail, Result};
use chrono::{DateTime, Utc};
use postgres::types::ToSql;
use postgres::GenericClient;
use serde::Serialize;
use std::collections::HashMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RecallMode {
    Current,
    AsOf,
    Associative,
    Grounded,
}

impl RecallMode {
    pub fn as_str(self) -> &'static str {
        match self {
            RecallMode::Current => "current",
            RecallMode::AsOf => "as_of",
            RecallMode::Associative => "associative",
            RecallMode::Grounded => "grounded",
        }
    }
}

impl std::str::FromStr for RecallMode {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, String> {
        match s {
            "current" => Ok(RecallMode::Current),
            "as_of" => Ok(RecallMode::AsOf),
            "associative" => Ok(RecallMode::Associative),
            "grounded" => Ok(RecallMode::Grounded),
            other => Err(format!("unknown recall mode: {other}")),
        }
    }
}

pub const DEFAULT_LIMIT: usize = 10;
const MAX_LIMIT: usize = 50;

pub struct RecallQuery<'a> {
    pub text: &'a str,
    pub project: Option<&'a str>,
    pub mode: RecallMode,
    pub valid_at: Option<DateTime<Utc>>,
    pub known_at: Option<DateTime<Utc>>,
    /// 1..=50, default 10.
    pub limit: usize,
}

pub struct QueryEmbedding {
    pub model: String,
    pub column: &'static str,
    pub vector: Vec<f32>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Why {
    pub cosine: Option<f64>,
    pub lexical: f64,
    pub recency: f64,
    pub reliability: f64,
    pub strength: f64,
    pub provenance: f64,
    pub spread: Option<f64>,
    pub grounding: Option<f64>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Evidence {
    pub message_id: i64,
    pub excerpt: String,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize)]
pub struct RecallHit {
    pub id: i64,
    pub content: String,
    pub category: String,
    pub project_name: Option<String>,
    pub status: String,
    pub valid_from: DateTime<Utc>,
    pub valid_to: Option<DateTime<Utc>>,
    pub source_type: String,
    pub conversation_id: Option<i64>,
    pub session_title: Option<String>,
    pub superseded_by: Option<i64>,
    pub supersedes: Option<i64>,
    pub score: f64,
    pub why: Why,
    pub relation: Option<String>,
    pub evidence: Option<Evidence>,
}

#[derive(Debug, Serialize)]
pub struct RecallResult {
    pub mode: String,
    pub hits: Vec<RecallHit>,
    pub degraded: Option<String>,
}

pub struct Weights {
    pub cosine: f64,
    pub lexical: f64,
    pub recency: f64,
    pub reliability: f64,
    pub strength: f64,
    pub provenance: f64,
}

pub const WEIGHTS: Weights = Weights {
    cosine: 0.45,
    lexical: 0.20,
    recency: 0.10,
    reliability: 0.10,
    strength: 0.10,
    provenance: 0.05,
};

const STOPWORDS: &[&str] = &[
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "und", "oder", "ist", "sind",
    "war", "wie", "was", "wer", "wo", "wann", "warum", "mit", "für", "von", "auf", "aus", "bei",
    "nach", "über", "unter", "nicht", "auch", "noch", "schon", "nur", "the", "and", "for", "with",
    "from", "that", "this", "what", "when", "where", "which", "who", "why", "how", "are", "was",
    "were", "not", "but", "can", "you", "our", "your", "has", "have", "had", "into", "about",
];

/// Lower-case runs of letters and digits, at least 3 characters, no stopwords.
pub fn query_words(text: &str) -> Vec<String> {
    text.to_lowercase()
        .split(|c: char| !c.is_alphanumeric())
        .filter(|w| w.chars().count() >= 3 && !STOPWORDS.contains(w))
        .map(str::to_string)
        .collect()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum CandidateFilter {
    Current,
    AsOf,
    /// Notes by id for the associative mode: everything but forgotten notes.
    Ids,
}

/// A note that passed the filter, before scoring. `tags`, `source_id` and
/// `merged_from` are for the modes that follow.
#[allow(dead_code)]
#[derive(Debug, Clone)]
pub(crate) struct Candidate {
    pub id: i64,
    pub content: String,
    pub category: String,
    pub project_name: Option<String>,
    pub status: String,
    pub valid_from: DateTime<Utc>,
    pub valid_to: Option<DateTime<Utc>>,
    pub source_type: String,
    pub conversation_id: Option<i64>,
    pub session_title: Option<String>,
    pub superseded_by: Option<i64>,
    pub supersedes: Option<i64>,
    pub confidence: f64,
    pub cosine: Option<f64>,
    pub tags: Vec<String>,
    pub source_id: Option<i64>,
    pub merged_from: Vec<i64>,
}

/// All notes that pass `filter` and the project filter, with their cosine to
/// the query vector (NULL without vector or without embedding for the note).
// vereinfacht: bewertet alle Kandidaten in Rust, ausbauen wenn mehr als 50.000 Notizen
pub(crate) fn candidates<C: GenericClient>(
    client: &mut C,
    filter: CandidateFilter,
    q: &RecallQuery,
    emb: Option<&QueryEmbedding>,
) -> Result<Vec<Candidate>> {
    load_candidates(client, filter, q, emb, &[])
}

/// `candidates`, plus the ids for `CandidateFilter::Ids` (one query for all).
fn load_candidates<C: GenericClient>(
    client: &mut C,
    filter: CandidateFilter,
    q: &RecallQuery,
    emb: Option<&QueryEmbedding>,
    ids: &[i64],
) -> Result<Vec<Candidate>> {
    // $1 = valid_at, $2 = known_at, $3 = project, then model and vector, then ids.
    let id_param = if emb.is_some() { 6 } else { 4 };
    let id_state = format!("mc.id = ANY(${id_param}) AND");
    let (valid_at, known_at, state) = match filter {
        CandidateFilter::Current => (
            Some(Utc::now()),
            None,
            "COALESCE(mc.status,'active') NOT IN ('forgotten','superseded','merged') \
             AND mc.superseded_by IS NULL AND",
        ),
        CandidateFilter::AsOf => (q.valid_at, q.known_at, ""),
        // Without both dates the predicate only drops forgotten notes.
        CandidateFilter::Ids => (None, None, id_state.as_str()),
    };
    let predicate = temporal::as_of_predicate("$1", "$2");
    let project = q.project.map(str::to_string);
    let literal = emb.map(|e| vector_literal(&e.vector));
    let mut params: Vec<&(dyn ToSql + Sync)> = vec![&valid_at, &known_at, &project];
    let (cosine, join) = match emb {
        Some(e) => {
            params.push(&e.model);
            params.push(literal.as_ref().expect("literal exists with embedding"));
            (
                format!(
                    "CASE WHEN e.source_id IS NULL THEN NULL \
                     ELSE GREATEST(0, LEAST(1, 1 - (e.{} <=> $5::text::vector)))::float8 END",
                    e.column
                ),
                "LEFT JOIN embeddings e ON e.source_type = 'memory_chunk' \
                 AND e.source_id = mc.id AND e.model = $4"
                    .to_string(),
            )
        }
        None => ("NULL::float8".to_string(), String::new()),
    };
    if filter == CandidateFilter::Ids {
        params.push(&ids);
    }
    let sql = format!(
        r#"
        SELECT mc.id, mc.content, mc.category::text, mc.project_name,
               COALESCE(mc.status, 'active'),
               COALESCE(mc.valid_from, mc.source_at, mc.created_at), mc.valid_to,
               mc.source_type, c.id, c.summary, mc.superseded_by,
               (SELECT min(p.id) FROM memory_chunks p WHERE p.superseded_by = mc.id),
               COALESCE(mc.confidence::float8, 0.5),
               {cosine},
               COALESCE(mc.tags, '{{}}'), mc.source_id, COALESCE(mc.merged_from, '{{}}')
        FROM memory_chunks mc
        LEFT JOIN LATERAL (
            WITH RECURSIVE up(id, source_type, source_id, depth) AS (
                SELECT mc.id, mc.source_type, mc.source_id, 0
                UNION ALL
                SELECT p.id, p.source_type, p.source_id, up.depth + 1
                FROM memory_chunks p JOIN up ON p.superseded_by = up.id
                WHERE up.depth < 50)
            SELECT source_type, source_id FROM up ORDER BY depth DESC LIMIT 1
        ) root ON true
        LEFT JOIN conversations c ON root.source_type = 'conversation' AND c.id = root.source_id
        {join}
        WHERE {state} {predicate}
          AND ($3::text IS NULL OR mc.project_name = $3 OR mc.project_name IS NULL)
        "#
    );
    let rows = client.query(&sql, &params)?;
    Ok(rows
        .iter()
        .map(|r| Candidate {
            id: r.get(0),
            content: r.get(1),
            category: r.get(2),
            project_name: r.get(3),
            status: r.get(4),
            valid_from: r.get(5),
            valid_to: r.get(6),
            source_type: r.get(7),
            conversation_id: r.get(8),
            session_title: r.get(9),
            superseded_by: r.get(10),
            supersedes: r.get(11),
            confidence: r.get(12),
            cosine: r.get(13),
            tags: r.get(14),
            source_id: r.get(15),
            merged_from: r.get(16),
        })
        .collect())
}

fn provenance(source_type: &str) -> f64 {
    match source_type {
        "ui_pin" => 1.0,
        "manual" => 0.8,
        "mcp_write" | "consolidation" => 0.7,
        "conversation" => 0.6,
        _ => 0.5,
    }
}

/// Weighted score of one candidate and the parts it is made of. Without an
/// embedding the cosine share drops out and the rest is scaled up to 1.
pub(crate) fn score(
    c: &Candidate,
    words: &[String],
    has_embedding: bool,
    now: DateTime<Utc>,
) -> (f64, Why) {
    let w = &WEIGHTS;
    let lexical = if words.is_empty() {
        0.0
    } else {
        let content = c.content.to_lowercase();
        words
            .iter()
            .filter(|x| content.contains(x.as_str()))
            .count() as f64
            / words.len() as f64
    };
    let days = ((now - c.valid_from).num_milliseconds() as f64 / 86_400_000.0).max(0.0);
    let why = Why {
        cosine: has_embedding.then(|| c.cosine.unwrap_or(0.0)),
        lexical,
        recency: (-days / 90.0).exp(),
        reliability: c.confidence.clamp(0.0, 1.0),
        strength: 0.5, // Baustein 4
        provenance: provenance(&c.source_type),
        spread: None,
        grounding: None,
    };
    let rest = w.lexical * why.lexical
        + w.recency * why.recency
        + w.reliability * why.reliability
        + w.strength * why.strength
        + w.provenance * why.provenance;
    let total = match why.cosine {
        Some(cos) => w.cosine * cos + rest,
        None => rest / (1.0 - w.cosine),
    };
    (total, why)
}

/// Spread factor per step and the number of steps in associative mode.
const SPREAD_FACTOR: f64 = 0.8;
const MAX_HOPS: usize = 2;

/// Notes one edge away from `frontier`: (id, relation, frontier id it comes
/// from), in the order of the edge table, so the first row per id wins a tie.
fn neighbour_edges<C: GenericClient>(
    client: &mut C,
    frontier: &[i64],
) -> Result<Vec<(i64, &'static str, i64)>> {
    let rows = client.query(
        "SELECT id, prio, via FROM (
            SELECT mc.id, 1 AS prio, mc.superseded_by AS via
              FROM memory_chunks mc WHERE mc.superseded_by = ANY($1)
            UNION ALL
            SELECT mc.id, 1, f.id FROM memory_chunks f
              JOIN memory_chunks mc ON mc.id = f.superseded_by WHERE f.id = ANY($1)
            UNION ALL
            SELECT mc.id, 2, f.id FROM memory_chunks f
              JOIN memory_chunks mc ON mc.source_type = 'conversation'
               AND mc.source_id = f.source_id AND mc.id <> f.id
             WHERE f.id = ANY($1) AND f.source_type = 'conversation'
            UNION ALL
            SELECT mc.id, 3, f.id FROM memory_chunks f
              JOIN memory_chunks mc ON mc.id <> f.id
               AND EXISTS (SELECT 1 FROM unnest(COALESCE(f.tags, '{}')) t
                           WHERE left(t, 5) = 'file:' AND t = ANY(COALESCE(mc.tags, '{}')))
             WHERE f.id = ANY($1)
            UNION ALL
            SELECT mc.id, 4, f.id FROM memory_chunks f
              JOIN memory_chunks mc ON mc.id <> f.id
               AND (f.id = ANY(COALESCE(mc.merged_from, '{}'))
                    OR mc.id = ANY(COALESCE(f.merged_from, '{}')))
             WHERE f.id = ANY($1)
         ) x WHERE id <> ALL($1) ORDER BY prio, id, via",
        &[&frontier],
    )?;
    Ok(rows
        .iter()
        .map(|r| {
            let rel = match r.get::<_, i32>(1) {
                1 => "chain",
                2 => "same_conversation",
                3 => "same_file",
                _ => "merged",
            };
            (r.get(0), rel, r.get(2))
        })
        .collect())
}

/// Adds the neighbours of the seeds in `scored`, at most `MAX_HOPS` steps
/// away. A neighbour scores `max(own score, seed score * 0.8^steps)`, with the
/// spread in `why.spread`; reached twice, the higher score stays. Neighbours
/// may be replaced or merged notes (relation "superseded"), never forgotten
/// ones, and keep the project filter of the query. One query loads the notes
/// of a step.
fn associate<C: GenericClient>(
    client: &mut C,
    q: &RecallQuery,
    emb: Option<&QueryEmbedding>,
    words: &[String],
    now: DateTime<Utc>,
    scored: &mut Vec<(f64, Why, Candidate)>,
    relations: &mut HashMap<i64, String>,
) -> Result<()> {
    let seeds: Vec<i64> = scored.iter().map(|s| s.2.id).collect();
    // spread value each frontier note passes on
    let mut frontier: HashMap<i64, f64> = scored.iter().map(|s| (s.2.id, s.0)).collect();
    let mut found: HashMap<i64, (f64, Why, Candidate)> = HashMap::new();
    for _ in 0..MAX_HOPS {
        if frontier.is_empty() {
            break;
        }
        let ids: Vec<i64> = frontier.keys().copied().collect();
        // id -> (spread, relation)
        let mut reached: HashMap<i64, (f64, &'static str)> = HashMap::new();
        for (id, rel, via) in neighbour_edges(client, &ids)? {
            if seeds.contains(&id) {
                continue;
            }
            let spread = SPREAD_FACTOR * frontier[&via];
            if reached.get(&id).is_none_or(|(best, _)| spread > *best) {
                reached.insert(id, (spread, rel));
            }
        }
        let wanted: Vec<i64> = reached.keys().copied().collect();
        let mut next = HashMap::new();
        if !wanted.is_empty() {
            for c in load_candidates(client, CandidateFilter::Ids, q, emb, &wanted)? {
                let (spread, rel) = reached[&c.id];
                let (own, mut why) = score(&c, words, emb.is_some(), now);
                why.spread = Some(spread);
                let total = own.max(spread);
                let replaced = c.superseded_by.is_some()
                    || matches!(c.status.as_str(), "superseded" | "merged");
                next.insert(c.id, spread);
                if found.get(&c.id).is_none_or(|(best, _, _)| total > *best) {
                    relations.insert(c.id, if replaced { "superseded" } else { rel }.to_string());
                    found.insert(c.id, (total, why, c));
                }
            }
        }
        frontier = next;
    }
    scored.extend(found.into_values());
    scored.sort_by(|a, b| b.0.total_cmp(&a.0).then(a.2.id.cmp(&b.2.id)));
    Ok(())
}

pub fn recall<C: GenericClient>(
    client: &mut C,
    q: &RecallQuery,
    emb: Option<&QueryEmbedding>,
) -> Result<RecallResult> {
    let filter = match q.mode {
        RecallMode::Current => CandidateFilter::Current,
        RecallMode::AsOf => {
            if q.valid_at.is_none() && q.known_at.is_none() {
                bail!("as_of needs valid_at or known_at");
            }
            CandidateFilter::AsOf
        }
        RecallMode::Associative => CandidateFilter::Current,
        other => bail!("recall mode {} is not available yet", other.as_str()),
    };
    let now = Utc::now();
    let words = query_words(q.text);
    let mut scored: Vec<(f64, Why, Candidate)> = candidates(client, filter, q, emb)?
        .into_iter()
        .map(|c| {
            let (s, why) = score(&c, &words, emb.is_some(), now);
            (s, why, c)
        })
        .collect();
    if q.mode == RecallMode::AsOf {
        // Only notes that match the question; the order is the timeline.
        scored.retain(|(_, why, _)| why.lexical > 0.0 || why.cosine.is_some_and(|c| c >= 0.5));
        scored.sort_by(|a, b| {
            a.2.valid_from
                .cmp(&b.2.valid_from)
                .then(a.2.id.cmp(&b.2.id))
        });
    } else {
        scored.sort_by(|a, b| b.0.total_cmp(&a.0).then(a.2.id.cmp(&b.2.id)));
    }
    let limit = q.limit.clamp(1, MAX_LIMIT);
    scored.truncate(limit);
    let mut relations: HashMap<i64, String> = HashMap::new();
    if q.mode == RecallMode::Associative {
        // The seeds already fill `limit`: allow twice as many, so the
        // neighbours that rank high enough are visible.
        associate(client, q, emb, &words, now, &mut scored, &mut relations)?;
        scored.truncate((2 * limit).min(2 * MAX_LIMIT));
    }
    let hits = scored
        .into_iter()
        .map(|(score, why, c)| RecallHit {
            relation: relations.remove(&c.id),
            id: c.id,
            content: c.content,
            category: c.category,
            project_name: c.project_name,
            status: c.status,
            valid_from: c.valid_from,
            valid_to: c.valid_to,
            source_type: c.source_type,
            conversation_id: c.conversation_id,
            session_title: c.session_title,
            superseded_by: c.superseded_by,
            supersedes: c.supersedes,
            score,
            why,
            evidence: None,
        })
        .collect();
    Ok(RecallResult {
        mode: q.mode.as_str().to_string(),
        hits,
        degraded: emb.is_none().then(|| "no_embeddings".to_string()),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::extract_memory::category_column_type;
    use chrono::Duration;
    use postgres::NoTls;

    fn test_db() -> Option<postgres::Client> {
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

    /// A 768-dimensional vector with the given leading components.
    fn vec768(head: &[f32]) -> Vec<f32> {
        let mut v = vec![0.0; 768];
        v[..head.len()].copy_from_slice(head);
        v
    }

    struct Seed<'a> {
        content: &'a str,
        project: Option<&'a str>,
        source_type: &'a str,
        source_id: Option<i64>,
        status: &'a str,
        confidence: f64,
        valid_from: DateTime<Utc>,
        expires_at: Option<DateTime<Utc>>,
    }

    impl<'a> Seed<'a> {
        fn new(content: &'a str, valid_from: DateTime<Utc>) -> Self {
            Seed {
                content,
                project: None,
                source_type: "manual",
                source_id: None,
                status: "active",
                confidence: 0.8,
                valid_from,
                expires_at: None,
            }
        }
    }

    fn seed<C: GenericClient>(client: &mut C, s: &Seed) -> i64 {
        let ct = category_column_type(client).unwrap();
        client
            .query_one(
                &format!(
                    "INSERT INTO memory_chunks (source_type, source_id, content, category, \
                     project_name, confidence, tags, status, valid_from, expires_at) \
                     VALUES ($1, $2, $3, 'insight'::text::{ct}, $4, $5::float8, '{{}}', $6, $7, $8) \
                     RETURNING id"
                ),
                &[
                    &s.source_type,
                    &s.source_id,
                    &s.content,
                    &s.project,
                    &s.confidence,
                    &s.status,
                    &s.valid_from,
                    &s.expires_at,
                ],
            )
            .unwrap()
            .get(0)
    }

    fn embed<C: GenericClient>(client: &mut C, id: i64, model: &str, v: &[f32]) {
        client
            .execute(
                "INSERT INTO embeddings (source_type, source_id, model, embedding_768) \
                 VALUES ('memory_chunk', $1, $2, $3::text::vector)",
                &[&id, &model, &vector_literal(v)],
            )
            .unwrap();
    }

    fn query<'a>(text: &'a str, project: Option<&'a str>) -> RecallQuery<'a> {
        RecallQuery {
            text,
            project,
            mode: RecallMode::Current,
            valid_at: None,
            known_at: None,
            limit: 50,
        }
    }

    fn model_name() -> String {
        format!("test-embed-{}", uuid::Uuid::new_v4())
    }

    fn query_embedding(model: &str) -> QueryEmbedding {
        QueryEmbedding {
            model: model.to_string(),
            column: "embedding_768",
            vector: vec768(&[1.0]),
        }
    }

    fn long_ago() -> DateTime<Utc> {
        Utc::now() - Duration::days(30)
    }

    fn position(r: &RecallResult, id: i64) -> Option<usize> {
        r.hits.iter().position(|h| h.id == id)
    }

    #[test]
    fn recall_current_ranks_by_weighted_score() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_current_ranks_by_weighted_score: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let model = model_name();
        let content_a = format!("kafka retention {tag} near");
        let content_b = format!("kafka retention {tag} far");
        let mut sa = Seed::new(&content_a, long_ago());
        sa.project = Some(&project);
        let mut sb = Seed::new(&content_b, long_ago());
        sb.project = Some(&project);
        sb.source_type = "ui_pin";
        let a = seed(&mut tx, &sa);
        let b = seed(&mut tx, &sb);
        embed(&mut tx, a, &model, &vec768(&[0.8, 0.6]));
        embed(&mut tx, b, &model, &vec768(&[0.6, 0.8]));
        let emb = query_embedding(&model);
        let r = recall(
            &mut tx,
            &query("kafka retention", Some(&project)),
            Some(&emb),
        )
        .unwrap();
        assert_eq!(r.degraded, None);
        let (ha, hb) = (
            &r.hits[position(&r, a).unwrap()],
            &r.hits[position(&r, b).unwrap()],
        );
        assert!(position(&r, a) < position(&r, b));
        assert!(ha.why.cosine.unwrap() > hb.why.cosine.unwrap());
        assert!((ha.why.cosine.unwrap() - 0.8).abs() < 1e-6);
        assert!((hb.why.cosine.unwrap() - 0.6).abs() < 1e-6);
        for h in [ha, hb] {
            let w = &WEIGHTS;
            let sum = w.cosine * h.why.cosine.unwrap()
                + w.lexical * h.why.lexical
                + w.recency * h.why.recency
                + w.reliability * h.why.reliability
                + w.strength * h.why.strength
                + w.provenance * h.why.provenance;
            assert!((h.score - sum).abs() < 1e-9, "{} vs {}", h.score, sum);
            assert_eq!(h.why.lexical, 1.0);
        }
        assert_eq!(ha.why.provenance, 0.8);
        assert_eq!(hb.why.provenance, 1.0);
        assert!((ha.why.reliability - 0.8).abs() < 1e-6);
    }

    #[test]
    fn recall_current_excludes_replaced_forgotten_and_expired() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_current_excludes_replaced_forgotten_and_expired: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let contents: Vec<String> = ["ok", "replaced", "merged", "forgotten", "expired", "new"]
            .iter()
            .map(|n| format!("{n} {tag}"))
            .collect();
        let mk = |i: usize, status: &'static str, exp: Option<DateTime<Utc>>| {
            let mut s = Seed::new(&contents[i], long_ago());
            s.project = Some(&project);
            s.status = status;
            s.expires_at = exp;
            s
        };
        let ok = seed(&mut tx, &mk(0, "active", None));
        let replaced = seed(&mut tx, &mk(1, "superseded", None));
        let merged = seed(&mut tx, &mk(2, "merged", None));
        let forgotten = seed(&mut tx, &mk(3, "forgotten", None));
        let expired = seed(
            &mut tx,
            &mk(4, "active", Some(Utc::now() - Duration::days(1))),
        );
        let new = seed(&mut tx, &mk(5, "active", None));
        tx.execute(
            "UPDATE memory_chunks SET superseded_by = $2 WHERE id = $1",
            &[&replaced, &new],
        )
        .unwrap();
        let r = recall(&mut tx, &query(&tag, Some(&project)), None).unwrap();
        let ids: Vec<i64> = r.hits.iter().map(|h| h.id).collect();
        assert!(ids.contains(&ok) && ids.contains(&new));
        for gone in [replaced, merged, forgotten, expired] {
            assert!(!ids.contains(&gone), "note {gone} must not be recalled");
        }
    }

    #[test]
    fn recall_respects_project() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_respects_project: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let (px, py) = (format!("px-{tag}"), format!("py-{tag}"));
        let (cx, cy, cn) = (
            format!("x {tag}"),
            format!("y {tag}"),
            format!("none {tag}"),
        );
        let mut sx = Seed::new(&cx, long_ago());
        sx.project = Some(&px);
        let mut sy = Seed::new(&cy, long_ago());
        sy.project = Some(&py);
        let x = seed(&mut tx, &sx);
        let y = seed(&mut tx, &sy);
        let none = seed(&mut tx, &Seed::new(&cn, long_ago()));
        let r = recall(&mut tx, &query(&tag, Some(&px)), None).unwrap();
        assert!(position(&r, x).is_some());
        assert!(position(&r, none).is_some());
        assert!(position(&r, y).is_none());
    }

    #[test]
    fn recall_without_embedding_degrades() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_without_embedding_degrades: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let content = format!("deploy checklist {tag}");
        let mut s = Seed::new(&content, long_ago());
        s.project = Some(&project);
        let id = seed(&mut tx, &s);
        let r = recall(&mut tx, &query("deploy checklist", Some(&project)), None).unwrap();
        assert_eq!(r.degraded.as_deref(), Some("no_embeddings"));
        let h = &r.hits[position(&r, id).unwrap()];
        assert_eq!(h.why.cosine, None);
        let w = &WEIGHTS;
        let sum = (w.lexical * h.why.lexical
            + w.recency * h.why.recency
            + w.reliability * h.why.reliability
            + w.strength * h.why.strength
            + w.provenance * h.why.provenance)
            / (1.0 - w.cosine);
        assert!((h.score - sum).abs() < 1e-9);
    }

    #[test]
    fn recall_includes_notes_without_vectors() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_includes_notes_without_vectors: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let model = model_name();
        let content = format!("no vector {tag}");
        let mut s = Seed::new(&content, long_ago());
        s.project = Some(&project);
        let id = seed(&mut tx, &s);
        let emb = query_embedding(&model);
        let r = recall(&mut tx, &query(&tag, Some(&project)), Some(&emb)).unwrap();
        assert_eq!(r.degraded, None);
        assert_eq!(r.hits[position(&r, id).unwrap()].why.cosine, Some(0.0));
    }

    #[test]
    fn recall_handles_empty_and_stopword_queries() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping recall_handles_empty_and_stopword_queries: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let content = format!("der die das {tag}");
        let mut s = Seed::new(&content, long_ago());
        s.project = Some(&project);
        let id = seed(&mut tx, &s);
        for text in ["", "der die das"] {
            let r = recall(&mut tx, &query(text, Some(&project)), None).unwrap();
            assert!(position(&r, id).is_some());
            assert!(r.hits.iter().all(|h| h.why.lexical == 0.0));
        }
        assert!(query_words("Der die DAS").is_empty());
        assert_eq!(
            query_words("Wie läuft die Übergabe-Prüfung 2026?"),
            vec!["läuft", "übergabe", "prüfung", "2026"]
        );
    }

    #[test]
    fn recall_reports_the_source_session() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_reports_the_source_session: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let title = format!("Session {tag}");
        let conv: i64 = tx
            .query_one(
                "INSERT INTO conversations (session_id, project_path, started_at, summary) \
                 VALUES ($1, '/tmp/recall-test', now(), $2) RETURNING id",
                &[&uuid::Uuid::new_v4(), &title],
            )
            .unwrap()
            .get(0);
        let (ca, cb) = (format!("old {tag}"), format!("new {tag}"));
        let mut sa = Seed::new(&ca, long_ago());
        sa.project = Some(&project);
        sa.source_type = "conversation";
        sa.source_id = Some(conv);
        sa.status = "superseded";
        let mut sb = Seed::new(&cb, long_ago());
        sb.project = Some(&project);
        sb.source_type = "ui_pin";
        let a = seed(&mut tx, &sa);
        let b = seed(&mut tx, &sb);
        tx.execute(
            "UPDATE memory_chunks SET superseded_by = $2 WHERE id = $1",
            &[&a, &b],
        )
        .unwrap();
        let r = recall(&mut tx, &query(&tag, Some(&project)), None).unwrap();
        assert!(position(&r, a).is_none());
        let h = &r.hits[position(&r, b).unwrap()];
        assert_eq!(h.conversation_id, Some(conv));
        assert_eq!(h.session_title.as_deref(), Some(title.as_str()));
        assert_eq!(h.supersedes, Some(a));
        assert_eq!(h.source_type, "ui_pin");
    }

    fn at(y: i32, m: u32, d: u32) -> DateTime<Utc> {
        chrono::TimeZone::with_ymd_and_hms(&Utc, y, m, d, 0, 0, 0).unwrap()
    }

    #[test]
    fn recall_as_of_follows_the_chain() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_as_of_follows_the_chain: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let content_a = format!("retention is seven days {tag}");
        let content_b = format!("retention is thirty days {tag}");
        let mut sa = Seed::new(&content_a, at(2026, 1, 1));
        sa.project = Some(&project);
        let a = seed(&mut tx, &sa);
        let (_, b) = crate::server::supersede_memory(
            &mut tx,
            a,
            &crate::server::Successor {
                content: &content_b,
                category: None,
                source_type: "ui_pin",
                kind: crate::server::SupersedeKind::Update,
                valid_from: Some(at(2026, 3, 1)),
                valid_from_is_date: false,
                confidence: 1.0,
                tags: vec![],
                source_at: None,
            },
        )
        .unwrap();
        let mut q = query(&tag, Some(&project));
        q.mode = RecallMode::AsOf;
        q.valid_at = Some(at(2026, 2, 15));
        let r = recall(&mut tx, &q, None).unwrap();
        assert_eq!(r.mode, "as_of");
        assert_eq!(r.hits.len(), 1);
        assert_eq!(r.hits[0].id, a);
        assert_eq!(r.hits[0].superseded_by, Some(b));
        q.valid_at = Some(at(2026, 3, 15));
        let r = recall(&mut tx, &q, None).unwrap();
        assert_eq!(r.hits.len(), 1);
        assert_eq!(r.hits[0].id, b);
        assert_eq!(r.hits[0].supersedes, Some(a));
    }

    #[test]
    fn recall_as_of_sorts_by_valid_from_and_drops_non_matches() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_as_of_sorts_by_valid_from_and_drops_non_matches: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let (c1, c2, c3) = (
            format!("zebra {tag} late"),
            format!("zebra {tag} early"),
            "unrelated note".to_string(),
        );
        let mut s1 = Seed::new(&c1, at(2026, 2, 1));
        s1.project = Some(&project);
        s1.source_type = "ui_pin";
        let mut s2 = Seed::new(&c2, at(2026, 1, 1));
        s2.project = Some(&project);
        let mut s3 = Seed::new(&c3, at(2026, 1, 15));
        s3.project = Some(&project);
        let (i1, i2, i3) = (seed(&mut tx, &s1), seed(&mut tx, &s2), seed(&mut tx, &s3));
        let text = format!("zebra {tag}");
        let mut q = query(&text, Some(&project));
        q.mode = RecallMode::AsOf;
        q.known_at = Some(Utc::now());
        q.valid_at = Some(at(2026, 6, 1));
        let r = recall(&mut tx, &q, None).unwrap();
        let ids: Vec<i64> = r.hits.iter().map(|h| h.id).collect();
        assert_eq!(ids, vec![i2, i1]);
        assert!(!ids.contains(&i3));
    }

    #[test]
    fn recall_as_of_requires_a_date() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_as_of_requires_a_date: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let mut q = query("anything", None);
        q.mode = RecallMode::AsOf;
        let err = recall(&mut tx, &q, None).unwrap_err();
        assert!(err.to_string().contains("as_of needs valid_at or known_at"));
    }

    fn replace<C: GenericClient>(client: &mut C, old: i64, new: i64) {
        client
            .execute(
                "UPDATE memory_chunks SET superseded_by = $2, status = 'superseded' WHERE id = $1",
                &[&old, &new],
            )
            .unwrap();
    }

    fn associative(text: &str, project: &str, limit: usize) -> RecallQuery<'static> {
        // the text is leaked: a few bytes per test run
        let text: &'static str = Box::leak(text.to_string().into_boxed_str());
        let project: &'static str = Box::leak(project.to_string().into_boxed_str());
        let mut q = query(text, Some(project));
        q.mode = RecallMode::Associative;
        q.limit = limit;
        q
    }

    #[test]
    fn recall_associative_adds_neighbours() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_associative_adds_neighbours: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let (cs, cn) = (format!("quokka {tag} seed"), "nothing alike".to_string());
        let conv = 987_000_000 + i64::from(uuid::Uuid::new_v4().as_u128() as u32 % 1_000_000);
        let mut ss = Seed::new(&cs, long_ago());
        ss.project = Some(&project);
        ss.source_type = "conversation";
        ss.source_id = Some(conv);
        let mut sn = Seed::new(&cn, long_ago());
        sn.project = Some(&project);
        sn.source_type = "conversation";
        sn.source_id = Some(conv);
        let (s, n) = (seed(&mut tx, &ss), seed(&mut tx, &sn));
        let r = recall(
            &mut tx,
            &associative(&format!("quokka {tag}"), &project, 1),
            None,
        )
        .unwrap();
        assert_eq!(r.mode, "associative");
        let hs = &r.hits[position(&r, s).unwrap()];
        let hn = &r.hits[position(&r, n).unwrap()];
        assert_eq!(hs.relation, None);
        assert_eq!(hs.why.spread, None);
        assert_eq!(hn.relation.as_deref(), Some("same_conversation"));
        assert!((hn.why.spread.unwrap() - hs.score * 0.8).abs() < 1e-9);
        assert!(hn.score >= hn.why.spread.unwrap());
    }

    #[test]
    fn recall_associative_marks_replaced_neighbours() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping recall_associative_marks_replaced_neighbours: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let (cs, cp) = (format!("okapi {tag} new"), "old wording".to_string());
        let mut ss = Seed::new(&cs, long_ago());
        ss.project = Some(&project);
        let mut sp = Seed::new(&cp, long_ago());
        sp.project = Some(&project);
        let (s, p) = (seed(&mut tx, &ss), seed(&mut tx, &sp));
        replace(&mut tx, p, s);
        let r = recall(
            &mut tx,
            &associative(&format!("okapi {tag}"), &project, 5),
            None,
        )
        .unwrap();
        assert_eq!(r.hits[position(&r, s).unwrap()].relation, None);
        let hp = &r.hits[position(&r, p).unwrap()];
        assert_eq!(hp.relation.as_deref(), Some("superseded"));
        assert_eq!(hp.superseded_by, Some(s));
        assert!(hp.why.spread.is_some());
    }

    #[test]
    fn recall_associative_stops_after_two_hops() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_associative_stops_after_two_hops: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let contents = [
            format!("tapir {tag} head"),
            "v1".into(),
            "v2".into(),
            "v3".into(),
        ];
        let ids: Vec<i64> = contents
            .iter()
            .map(|c| {
                let mut sd = Seed::new(c, long_ago());
                sd.project = Some(&project);
                seed(&mut tx, &sd)
            })
            .collect();
        for w in ids.windows(2) {
            replace(&mut tx, w[1], w[0]);
        }
        let r = recall(
            &mut tx,
            &associative(&format!("tapir {tag}"), &project, 5),
            None,
        )
        .unwrap();
        let spread = |id: i64| r.hits[position(&r, id).unwrap()].why.spread.unwrap();
        let head = &r.hits[position(&r, ids[0]).unwrap()];
        assert!((spread(ids[1]) - head.score * 0.8).abs() < 1e-9);
        assert!((spread(ids[2]) - head.score * 0.64).abs() < 1e-9);
        assert_eq!(position(&r, ids[3]), None);
    }

    #[test]
    fn recall_associative_follows_files_and_merges() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping recall_associative_follows_files_and_merges: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let file = format!("file:src/{tag}.rs");
        let cs = format!("axolotl {tag} head");
        let mut ss = Seed::new(&cs, long_ago());
        ss.project = Some(&project);
        let (s, f, m) = (
            seed(&mut tx, &ss),
            seed(&mut tx, &Seed::new("same file", long_ago())),
            seed(&mut tx, &Seed::new("merged away", long_ago())),
        );
        tx.execute(
            "UPDATE memory_chunks SET tags = ARRAY[$2]::text[] WHERE id = ANY($1)",
            &[&vec![s, f], &file],
        )
        .unwrap();
        tx.execute(
            "UPDATE memory_chunks SET merged_from = ARRAY[$2]::bigint[] WHERE id = $1",
            &[&s, &m],
        )
        .unwrap();
        tx.execute(
            "UPDATE memory_chunks SET status = 'merged' WHERE id = $1",
            &[&m],
        )
        .unwrap();
        let rel = |r: &RecallResult, id: i64| r.hits[position(r, id).unwrap()].relation.clone();
        let q = associative(&format!("axolotl {tag}"), &project, 1);
        // limit 1 allows two hits: look at one edge at a time
        tx.execute("UPDATE memory_chunks SET tags = '{}' WHERE id = $1", &[&f])
            .unwrap();
        let r2 = recall(&mut tx, &q, None).unwrap();
        assert_eq!(rel(&r2, m).as_deref(), Some("superseded"));
        tx.execute(
            "UPDATE memory_chunks SET merged_from = '{}' WHERE id = $1",
            &[&s],
        )
        .unwrap();
        tx.execute(
            "UPDATE memory_chunks SET tags = ARRAY[$2]::text[] WHERE id = $1",
            &[&f, &file],
        )
        .unwrap();
        let r3 = recall(&mut tx, &q, None).unwrap();
        assert_eq!(rel(&r3, f).as_deref(), Some("same_file"));
    }
}
