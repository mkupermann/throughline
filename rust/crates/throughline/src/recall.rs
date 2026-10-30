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
    // $1 = valid_at, $2 = known_at, $3 = project, then model and vector.
    let (valid_at, known_at, state) = match filter {
        CandidateFilter::Current => (
            Some(Utc::now()),
            None,
            "COALESCE(mc.status,'active') NOT IN ('forgotten','superseded','merged') \
             AND mc.superseded_by IS NULL AND",
        ),
        CandidateFilter::AsOf => (q.valid_at, q.known_at, ""),
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

pub fn recall<C: GenericClient>(
    client: &mut C,
    q: &RecallQuery,
    emb: Option<&QueryEmbedding>,
) -> Result<RecallResult> {
    let filter = match q.mode {
        RecallMode::Current => CandidateFilter::Current,
        RecallMode::AsOf => CandidateFilter::AsOf,
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
    scored.sort_by(|a, b| b.0.total_cmp(&a.0).then(a.2.id.cmp(&b.2.id)));
    scored.truncate(q.limit.clamp(1, MAX_LIMIT));
    let hits = scored
        .into_iter()
        .map(|(score, why, c)| RecallHit {
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
            relation: None,
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
}
