//! Recall of memory notes: candidate selection and a weighted ranking.
//!
//! Candidates and scores are separate steps so that other modes and an
//! evaluation can rank the same candidates in a different way.

use crate::access::{push_visibility, Actor};
use crate::generate_embeddings::vector_literal;
use crate::lifecycle;
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

/// How the candidates are ordered; [`DEFAULT_RANK`] when nobody asks.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Rank {
    /// `ts_rank` of the question words; the default before 2026-10-31.
    Fulltext,
    /// The weighted sum of `WEIGHTS`; full text without a question vector.
    Weighted,
    /// Cosine of the question's vector to the note's. A note without a
    /// vector yet scores its full-text rank over the best one of the
    /// question, and the result says how many did. Without a question vector
    /// it ranks by full text and the result says `degraded`.
    Cosine,
}

/// The rank of a request in mode `current` that names none (see
/// [`default_rank`]): HTTP, MCP `recall` and `project_context`, the CLI
/// `recall` and the context pack. Cosine since
/// 2026-10-31: on 74 questions it hit 94.6 % at k = 5, full text 50.0 %, and
/// the rule set in block 2 makes the method with the most hits the default.
/// Without a question vector it ranks by full text and says `degraded`.
pub const DEFAULT_RANK: Rank = Rank::Cosine;

/// The rank of a request in `mode` that names none: [`DEFAULT_RANK`] in
/// `current`, the only mode it was measured on; full text, as before
/// 2026-10-31, in `as_of`, `associative` and `grounded`.
pub fn default_rank(mode: RecallMode) -> Rank {
    match mode {
        RecallMode::Current => DEFAULT_RANK,
        RecallMode::AsOf | RecallMode::Associative | RecallMode::Grounded => Rank::Fulltext,
    }
}

impl Rank {
    pub fn as_str(self) -> &'static str {
        match self {
            Rank::Fulltext => "fulltext",
            Rank::Weighted => "weighted",
            Rank::Cosine => "cosine",
        }
    }
}

impl std::str::FromStr for Rank {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, String> {
        match s {
            "fulltext" => Ok(Rank::Fulltext),
            "weighted" => Ok(Rank::Weighted),
            "cosine" => Ok(Rank::Cosine),
            other => Err(format!("unknown rank: {other}")),
        }
    }
}

/// Whether a request uses the question's vector: the cosine and weighted
/// rankings, the cosine gate of `as_of` and the evidence of `grounded`.
/// Callers embed the question only then, so full text in `current` never
/// waits for Ollama.
pub fn needs_embedding(mode: RecallMode, rank: Rank) -> bool {
    matches!(rank, Rank::Weighted | Rank::Cosine)
        || matches!(mode, RecallMode::AsOf | RecallMode::Grounded)
}

pub const DEFAULT_LIMIT: usize = 10;
const MAX_LIMIT: usize = 50;
/// The longest question recall takes, in characters.
pub const MAX_QUERY_CHARS: usize = 2000;

/// Refuse a question longer than `MAX_QUERY_CHARS`.
pub fn check_query(text: &str) -> Result<(), String> {
    let n = text.chars().count();
    if n > MAX_QUERY_CHARS {
        return Err(format!(
            "query has {n} characters, at most {MAX_QUERY_CHARS} are allowed"
        ));
    }
    Ok(())
}
/// `as_of` admits a note that shares a word with the question or has at
/// least the active model's `as_of_min_cosine` to it. Without an embedding
/// no cosine exists, so the threshold is moot.
fn as_of_min_cosine(emb: Option<&QueryEmbedding>) -> f64 {
    emb.map_or(0.5, |e| e.min_cosine)
}

pub struct RecallQuery<'a> {
    /// Whose notes may appear: every query, chain step and neighbour is
    /// filtered to what this actor sees.
    pub actor: &'a Actor,
    pub text: &'a str,
    pub project: Option<&'a str>,
    pub mode: RecallMode,
    pub valid_at: Option<DateTime<Utc>>,
    pub known_at: Option<DateTime<Utc>>,
    /// 1..=50, default 10. Associative mode returns up to 2 × limit (max 100):
    /// the seeds fill `limit`, the neighbours get the same room again.
    pub limit: usize,
    pub rank: Rank,
}

pub struct QueryEmbedding {
    pub model: String,
    pub column: &'static str,
    pub vector: Vec<f32>,
    /// `as_of` cosine threshold of the model that embedded the question.
    pub min_cosine: f64,
}

/// Embed the question with the configured backend. A refusal by the
/// remote-model lock is an error; any other failure (no Ollama, model missing,
/// empty vector) is `None`, which the ranking reports as degraded.
pub fn query_embedding(text: &str) -> anyhow::Result<Option<QueryEmbedding>> {
    query_embedding_with(
        crate::embedding::resolve,
        |b, t| crate::embedding::embed_text(b, t, crate::embedding::TextKind::Query),
        text,
    )
}

/// The question's vector for a request whose rank is `rank`; `None` means the
/// caller named no rank, so [`default_rank`] of `mode` applies. That path waits
/// at most [`crate::embedding::QUICK_EMBED_TIMEOUT`] and skips Ollama while
/// `breaker` is open; an explicit rank keeps the long timeouts and ignores
/// the breaker. A refusal by the remote-model lock is an error either way.
pub fn question_vector(
    text: &str,
    mode: RecallMode,
    rank: Option<Rank>,
    breaker: &crate::embedding::EmbedBreaker,
) -> anyhow::Result<Option<QueryEmbedding>> {
    let owned = text.to_string();
    question_vector_with(
        mode,
        rank,
        breaker,
        crate::embedding::now_ms(),
        || {
            crate::embedding::within(crate::embedding::QUICK_EMBED_TIMEOUT, move || {
                query_embedding(&owned)
            })
        },
        || query_embedding(text),
    )
}

/// [`question_vector`] with the clock and both embedders passed in.
pub(crate) fn question_vector_with(
    mode: RecallMode,
    rank: Option<Rank>,
    breaker: &crate::embedding::EmbedBreaker,
    now_ms: i64,
    quick: impl FnOnce() -> anyhow::Result<Option<QueryEmbedding>>,
    long: impl FnOnce() -> anyhow::Result<Option<QueryEmbedding>>,
) -> anyhow::Result<Option<QueryEmbedding>> {
    if !needs_embedding(mode, rank.unwrap_or(default_rank(mode))) {
        return Ok(None);
    }
    match rank {
        Some(_) => long(),
        None => default_query_embedding_with(breaker, now_ms, quick),
    }
}

/// The default path: no call while `breaker` is open; a failure or a timeout
/// opens it. Both answer `None`, so the ranking falls back to full text.
pub(crate) fn default_query_embedding_with(
    breaker: &crate::embedding::EmbedBreaker,
    now_ms: i64,
    embed: impl FnOnce() -> anyhow::Result<Option<QueryEmbedding>>,
) -> anyhow::Result<Option<QueryEmbedding>> {
    if breaker.is_open(now_ms) {
        return Ok(None);
    }
    match embed() {
        Ok(Some(e)) => Ok(Some(e)),
        Err(e)
            if e.downcast_ref::<crate::llm::RemoteLlmNotAllowed>()
                .is_some() =>
        {
            Err(e)
        }
        // No Ollama, no model, an empty vector or no answer in time.
        Ok(None) | Err(_) => {
            breaker.trip(now_ms);
            Ok(None)
        }
    }
}

/// [`query_embedding`] with resolve and embed passed in, so tests need no Ollama.
pub fn query_embedding_with(
    resolve: impl FnOnce() -> anyhow::Result<crate::embedding::EmbeddingBackend>,
    embed: impl FnOnce(&crate::embedding::EmbeddingBackend, &str) -> anyhow::Result<Vec<f32>>,
    text: &str,
) -> anyhow::Result<Option<QueryEmbedding>> {
    let refused = |e: &anyhow::Error| {
        e.downcast_ref::<crate::llm::RemoteLlmNotAllowed>()
            .is_some()
    };
    let backend = match resolve() {
        Ok(b) => b,
        Err(e) if refused(&e) => return Err(e),
        Err(_) => return Ok(None),
    };
    let vector = match embed(&backend, text) {
        Ok(v) => v,
        Err(e) if refused(&e) => return Err(e),
        Err(_) => return Ok(None),
    };
    Ok(Some(QueryEmbedding {
        model: backend.model,
        column: backend.column,
        vector,
        min_cosine: backend.profile.as_of_min_cosine,
    }))
}

#[cfg(test)]
pub(crate) mod test_ollama {
    use std::sync::Mutex;

    static LOCK: Mutex<()> = Mutex::new(());

    /// Runs `f` with `OLLAMA_HOST` on a local port nothing listens on, so
    /// every embedding call fails as it does when Ollama is down.
    pub(crate) fn unreachable<T>(f: impl FnOnce() -> T) -> T {
        // Bound and dropped at once: the port is closed again.
        let port = std::net::TcpListener::bind("127.0.0.1:0")
            .and_then(|l| l.local_addr())
            .expect("free local port")
            .port();
        at(&format!("http://127.0.0.1:{port}"), f)
    }

    /// Runs `f` with `OLLAMA_HOST` on a local port that takes connections
    /// and never answers, like an Ollama that hangs.
    pub(crate) fn hanging<T>(f: impl FnOnce() -> T) -> T {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("free local port");
        let port = listener.local_addr().expect("local addr").port();
        // The thread keeps every connection open and outlives the test.
        std::thread::spawn(move || {
            let mut open = Vec::new();
            for stream in listener.incoming().flatten() {
                open.push(stream);
            }
        });
        at(&format!("http://127.0.0.1:{port}"), f)
    }

    /// Runs `f` with `OLLAMA_HOST` set to `url`. The old value comes back
    /// afterwards, also when `f` panics.
    fn at<T>(url: &str, f: impl FnOnce() -> T) -> T {
        struct Restore(Option<String>);
        impl Drop for Restore {
            fn drop(&mut self) {
                match &self.0 {
                    Some(v) => std::env::set_var("OLLAMA_HOST", v),
                    None => std::env::remove_var("OLLAMA_HOST"),
                }
            }
        }
        let _lock = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _restore = Restore(std::env::var("OLLAMA_HOST").ok());
        std::env::set_var("OLLAMA_HOST", url);
        f()
    }
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
    /// `ts_rank` of the note when the full-text ranking ordered the hits.
    pub fulltext: Option<f64>,
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
    /// When the source said it: `source_at` of the chain's first note, else
    /// the start of its conversation.
    pub source_at: Option<DateTime<Utc>>,
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

/// Weights of `Rank::Weighted`; `WEIGHTS` is the default, `recall-eval
/// --weights` passes others.
#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub struct Weights {
    pub cosine: f64,
    pub lexical: f64,
    pub recency: f64,
    pub reliability: f64,
    pub strength: f64,
    pub provenance: f64,
}

/// Calibrated on 42 questions of one installation (block 8, 2026-10-31):
/// the best of the 53,130 vectors with step 0.05. On the 32 held-out
/// questions it hit 93.8 % at k = 5, the weights before 71.9 %, and it ties
/// plain cosine there.
pub const WEIGHTS: Weights = Weights {
    cosine: 0.85,
    lexical: 0.0,
    recency: 0.0,
    reliability: 0.15,
    strength: 0.0,
    provenance: 0.0,
};

const STOPWORDS: &[&str] = &[
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "und", "oder", "ist", "sind",
    "war", "wie", "was", "wer", "wo", "wann", "warum", "mit", "für", "von", "auf", "aus", "bei",
    "nach", "über", "unter", "nicht", "auch", "noch", "schon", "nur", "the", "and", "for", "with",
    "from", "that", "this", "what", "when", "where", "which", "who", "why", "how", "are", "was",
    "were", "not", "but", "can", "you", "our", "your", "has", "have", "had", "into", "about",
];

/// Lower-case runs of letters and digits, at least 3 characters, no stopwords,
/// each word once in the order it first appears.
pub fn query_words(text: &str) -> Vec<String> {
    let mut words: Vec<String> = Vec::new();
    for w in text
        .to_lowercase()
        .split(|c: char| !c.is_alphanumeric())
        .filter(|w| w.chars().count() >= 3 && !STOPWORDS.contains(w))
    {
        if !words.iter().any(|x| x == w) {
            words.push(w.to_string());
        }
    }
    words
}

/// `to_tsquery` string of OR-joined words; only `[a-z0-9äöüß]` survives.
pub fn fulltext_tsquery(words: &[String]) -> Option<String> {
    let clean: Vec<String> = words
        .iter()
        .map(|w| {
            w.to_lowercase()
                .chars()
                .filter(|c| matches!(c, 'a'..='z' | '0'..='9' | 'ä' | 'ö' | 'ü' | 'ß'))
                .collect::<String>()
        })
        .filter(|w| !w.is_empty())
        .collect();
    if clean.is_empty() {
        None
    } else {
        Some(clean.join(" | "))
    }
}

/// `ts_rank` of the notes `ids` against the OR-joined `words`. A note without
/// a match, or every note when no word is left, is missing and counts as 0;
/// so is a note `actor` does not see.
pub(crate) fn fulltext_ranks<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    words: &[String],
    ids: &[i64],
) -> Result<HashMap<i64, f64>> {
    let Some(tsq) = fulltext_tsquery(words) else {
        return Ok(HashMap::new());
    };
    let mut params: Vec<&(dyn ToSql + Sync)> = vec![&tsq, &ids];
    let seen = push_visibility(actor, "mc", &mut params);
    let rows = client.query(
        &format!(
            "SELECT id, ts_rank(to_tsvector('simple', content), to_tsquery('simple', $1))::float8 \
             FROM memory_chunks mc WHERE id = ANY($2) AND {seen}"
        ),
        &params,
    )?;
    Ok(rows
        .iter()
        .map(|r| (r.get::<_, i64>(0), r.get::<_, f64>(1)))
        .collect())
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
    pub expires_at: Option<DateTime<Utc>>,
    pub source_at: Option<DateTime<Utc>>,
    pub access_count: i64,
    pub last_accessed: Option<DateTime<Utc>>,
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
    // The chain is cut at a note the actor does not see: no successor, no
    // predecessor, no merge member and no root behind it.
    let seen = push_visibility(q.actor, "mc", &mut params);
    let seen_next = push_visibility(q.actor, "s", &mut params);
    let seen_prev = push_visibility(q.actor, "p", &mut params);
    let seen_member = push_visibility(q.actor, "mm", &mut params);
    let seen_root = push_visibility(q.actor, "p", &mut params);
    let sql = format!(
        r#"
        SELECT mc.id, mc.content, mc.category::text, mc.project_name,
               COALESCE(mc.status, 'active'),
               COALESCE(mc.valid_from, mc.source_at, mc.created_at), mc.valid_to,
               mc.source_type, c.id, c.summary,
               (SELECT s.id FROM memory_chunks s WHERE s.id = mc.superseded_by AND {seen_next}),
               (SELECT min(p.id) FROM memory_chunks p WHERE p.superseded_by = mc.id AND {seen_prev}),
               COALESCE(mc.confidence::float8, 0.5),
               {cosine},
               COALESCE(mc.tags, '{{}}'), mc.source_id,
               ARRAY(SELECT u.x FROM unnest(COALESCE(mc.merged_from, '{{}}')) WITH ORDINALITY u(x, n)
                     WHERE EXISTS (SELECT 1 FROM memory_chunks mm WHERE mm.id = u.x AND {seen_member})
                     ORDER BY u.n),
               mc.expires_at, COALESCE(root.source_at, c.started_at),
               COALESCE(mc.access_count, 0)::bigint, mc.last_accessed
        FROM memory_chunks mc
        LEFT JOIN LATERAL (
            WITH RECURSIVE up(id, source_type, source_id, source_at, depth) AS (
                SELECT mc.id, mc.source_type, mc.source_id, mc.source_at, 0
                UNION ALL
                SELECT p.id, p.source_type, p.source_id, p.source_at, up.depth + 1
                FROM memory_chunks p JOIN up ON p.superseded_by = up.id
                WHERE up.depth < 50 AND {seen_root})
            SELECT source_type, source_id, source_at FROM up ORDER BY depth DESC LIMIT 1
        ) root ON true
        LEFT JOIN conversations c ON root.source_type = 'conversation' AND c.id = root.source_id
        {join}
        WHERE {state} {predicate} AND {seen}
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
            expires_at: r.get(17),
            source_at: r.get(18),
            access_count: r.get(19),
            last_accessed: r.get(20),
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
    w: &Weights,
) -> (f64, Why) {
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
    let used_days = ((now - c.last_accessed.unwrap_or(c.valid_from)).num_milliseconds() as f64
        / 86_400_000.0)
        .max(0.0);
    let pinned_or_decision = c.source_type == "ui_pin" || c.category == "decision";
    let why = Why {
        cosine: has_embedding.then(|| c.cosine.unwrap_or(0.0)),
        lexical,
        recency: (-days / 90.0).exp(),
        reliability: c.confidence.clamp(0.0, 1.0),
        strength: lifecycle::strength(used_days, c.access_count, pinned_or_decision),
        provenance: provenance(&c.source_type),
        spread: None,
        grounding: None,
        fulltext: None,
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

/// What orders the candidates once the rank meets the question vector.
enum Order {
    /// `ts_rank` per note id.
    Fulltext(HashMap<i64, f64>),
    Weighted,
    /// Cosine; a note without a vector scores its `ts_rank` over `best`, the
    /// highest `ts_rank` among the candidates.
    Cosine {
        words: HashMap<i64, f64>,
        best: f64,
    },
}

/// The order of `rank` for `cands`. Cosine and weighted without a question
/// vector order by full text; the result reports that as degraded.
fn order_for<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    rank: Rank,
    has_embedding: bool,
    words: &[String],
    cands: &[Candidate],
) -> Result<Order> {
    let ids: Vec<i64> = cands.iter().map(|c| c.id).collect();
    Ok(match rank {
        Rank::Weighted if has_embedding => Order::Weighted,
        Rank::Cosine if has_embedding => {
            // Only when a note lacks a vector are the words needed at all.
            let words = if cands.iter().any(|c| c.cosine.is_none()) {
                fulltext_ranks(client, actor, words, &ids)?
            } else {
                HashMap::new()
            };
            let best = words.values().copied().fold(0.0, f64::max);
            Order::Cosine { words, best }
        }
        Rank::Fulltext | Rank::Weighted | Rank::Cosine => {
            Order::Fulltext(fulltext_ranks(client, actor, words, &ids)?)
        }
    })
}

/// Score of one candidate by the order: its `ts_rank` (0 without a match),
/// the weighted score, or its cosine; under cosine a note without a vector
/// scores its `ts_rank` over the best one (0 without a match). The weighted
/// parts stay in `Why` either way.
fn rank_score(
    c: &Candidate,
    words: &[String],
    has_embedding: bool,
    now: DateTime<Utc>,
    weights: &Weights,
    order: &Order,
) -> (f64, Why) {
    let (weighted, mut why) = score(c, words, has_embedding, now, weights);
    match order {
        Order::Fulltext(ranks) => {
            let r = ranks.get(&c.id).copied().unwrap_or(0.0);
            why.fulltext = Some(r);
            (r, why)
        }
        Order::Weighted => (weighted, why),
        Order::Cosine { words, best } => match c.cosine {
            Some(cos) => (cos, why),
            None => {
                let r = words.get(&c.id).copied().unwrap_or(0.0);
                why.cosine = None;
                why.fulltext = Some(r);
                (if *best > 0.0 { r / best } else { 0.0 }, why)
            }
        },
    }
}

/// Highest score first, the lower id first on a tie.
fn by_score(scored: &mut [(f64, Why, Candidate)]) {
    scored.sort_by(|a, b| b.0.total_cmp(&a.0).then(a.2.id.cmp(&b.2.id)));
}

/// Spread factor per step and the number of steps in associative mode.
const SPREAD_FACTOR: f64 = 0.8;
const MAX_HOPS: usize = 2;

/// Notes one edge away from `frontier`: (id, relation, frontier id it comes
/// from), in the order of the edge table, so the first row per id wins a tie.
fn neighbour_edges<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    frontier: &[i64],
) -> Result<Vec<(i64, &'static str, i64)>> {
    let mut params: Vec<&(dyn ToSql + Sync)> = vec![&frontier];
    // A neighbour the actor does not see is no step: the walk stops there.
    let seen = push_visibility(actor, "v", &mut params);
    let sql = format!(
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
               AND EXISTS (SELECT 1 FROM unnest(COALESCE(f.tags, '{{}}')) t
                           WHERE left(t, 5) = 'file:' AND t = ANY(COALESCE(mc.tags, '{{}}')))
             WHERE f.id = ANY($1)
            UNION ALL
            SELECT mc.id, 4, f.id FROM memory_chunks f
              JOIN memory_chunks mc ON mc.id <> f.id
               AND (f.id = ANY(COALESCE(mc.merged_from, '{{}}'))
                    OR mc.id = ANY(COALESCE(f.merged_from, '{{}}')))
             WHERE f.id = ANY($1)
         ) x WHERE id <> ALL($1)
             AND EXISTS (SELECT 1 FROM memory_chunks v WHERE v.id = x.id AND {seen})
         ORDER BY prio, id, via"
    );
    let rows = client.query(&sql, &params)?;
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
/// away. A neighbour scores `max(own score, seed score * 0.8^steps)`, its own
/// score by the rank of the query, with the
/// spread in `why.spread`; reached twice, the higher score stays. Neighbours
/// may be replaced or merged notes (relation "superseded") or notes that no
/// longer hold, closed, expired or stale (relation "expired"), never forgotten
/// ones, and keep the project filter of the query. One query loads the notes
/// of a step.
#[allow(clippy::too_many_arguments)]
fn associate<C: GenericClient>(
    client: &mut C,
    q: &RecallQuery,
    emb: Option<&QueryEmbedding>,
    words: &[String],
    now: DateTime<Utc>,
    weights: &Weights,
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
        for (id, rel, via) in neighbour_edges(client, q.actor, &ids)? {
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
            let cands = load_candidates(client, CandidateFilter::Ids, q, emb, &wanted)?;
            let order = order_for(client, q.actor, q.rank, emb.is_some(), words, &cands)?;
            for c in cands {
                let (spread, rel) = reached[&c.id];
                let (own, mut why) = rank_score(&c, words, emb.is_some(), now, weights, &order);
                why.spread = Some(spread);
                let total = own.max(spread);
                let replaced = c.superseded_by.is_some()
                    || matches!(c.status.as_str(), "superseded" | "merged");
                let lapsed = c.valid_to.is_some_and(|t| t <= now)
                    || c.expires_at.is_some_and(|t| t <= now)
                    || c.status == "stale";
                let relation = if replaced {
                    "superseded"
                } else if lapsed {
                    "expired"
                } else {
                    rel
                };
                next.insert(c.id, spread);
                if found.get(&c.id).is_none_or(|(best, _, _)| total > *best) {
                    relations.insert(c.id, relation.to_string());
                    found.insert(c.id, (total, why, c));
                }
            }
        }
        frontier = next;
    }
    scored.extend(found.into_values());
    by_score(scored);
    Ok(())
}

/// The message that backs a note and how well: (strength, evidence).
type Grounding = (f64, Evidence);

fn evidence_from(r: &postgres::Row, first: usize) -> Evidence {
    Evidence {
        message_id: r.get(first),
        excerpt: r.get(first + 1),
        created_at: r.get(first + 2),
    }
}

#[cfg(test)]
thread_local! {
    /// Notes handed to `ground` on this thread, so a test sees where it stopped.
    static GROUNDED_NOTES: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}

/// Evidence for notes taken from a conversation (`notes` = note id and the
/// conversation of its chain root). With an embedding: the message with the
/// highest cosine to the note vector, for notes that have a vector and a
/// conversation with message vectors. Every other note: the message that
/// holds the largest share of the note's words. Two queries for all notes.
fn ground<C: GenericClient>(
    client: &mut C,
    notes: &[(&Candidate, i64)],
    emb: Option<&QueryEmbedding>,
) -> Result<HashMap<i64, Grounding>> {
    #[cfg(test)]
    GROUNDED_NOTES.with(|g| g.set(g.get() + notes.len()));
    let mut found: HashMap<i64, Grounding> = HashMap::new();
    let note_ids: Vec<i64> = notes.iter().map(|n| n.0.id).collect();
    let conv_ids: Vec<i64> = notes.iter().map(|n| n.1).collect();
    if let Some(e) = emb {
        let col = e.column;
        let sql = format!(
            "SELECT n.note_id, best.id, best.excerpt, best.created_at, best.cos
             FROM unnest($1::bigint[], $2::bigint[]) AS n(note_id, conv_id)
             JOIN embeddings ne ON ne.source_type = 'memory_chunk'
              AND ne.source_id = n.note_id AND ne.model = $3 AND ne.{col} IS NOT NULL
             CROSS JOIN LATERAL (
                SELECT m.id, left(COALESCE(m.content, ''), 240) AS excerpt, m.created_at,
                       GREATEST(0, LEAST(1, 1 - (ne.{col} <=> me.{col})))::float8 AS cos
                FROM messages m
                JOIN embeddings me ON me.source_type = 'message' AND me.source_id = m.id
                 AND me.model = $3 AND me.{col} IS NOT NULL
                WHERE m.conversation_id = n.conv_id
                ORDER BY ne.{col} <=> me.{col}, m.id LIMIT 1) best"
        );
        for r in client.query(&sql, &[&note_ids, &conv_ids, &e.model])? {
            let cos: Option<f64> = r.get(4);
            found.insert(r.get(0), (cos.unwrap_or(0.0), evidence_from(&r, 1)));
        }
    }
    // One row per note and word; a message scores the words it contains.
    let (mut word_notes, mut word_convs, mut word_list) = (vec![], vec![], vec![]);
    let mut totals: HashMap<i64, usize> = HashMap::new();
    for (c, conv) in notes.iter().filter(|n| !found.contains_key(&n.0.id)) {
        let words = query_words(&c.content);
        totals.insert(c.id, words.len());
        for w in words {
            word_notes.push(c.id);
            word_convs.push(*conv);
            word_list.push(w);
        }
    }
    if word_list.is_empty() {
        return Ok(found);
    }
    // Each message is lowered once and each word searched once per
    // conversation, however many notes share them.
    let rows = client.query(
        "WITH w AS (SELECT * FROM unnest($1::bigint[], $2::bigint[], $3::text[])
                      AS w(note_id, conv_id, word)),
              cw AS (SELECT DISTINCT conv_id, word FROM w),
              msg AS MATERIALIZED (
                SELECT m.id, m.conversation_id, lower(m.content) AS lc FROM messages m
                WHERE m.conversation_id IN (SELECT conv_id FROM cw)),
              hit AS (
                SELECT cw.conv_id, cw.word, msg.id AS message_id FROM cw
                JOIN msg ON msg.conversation_id = cw.conv_id AND strpos(msg.lc, cw.word) > 0),
              x AS (
                SELECT w.note_id, hit.message_id, count(*)::float8 AS hits FROM w
                JOIN hit ON hit.conv_id = w.conv_id AND hit.word = w.word
                GROUP BY w.note_id, hit.message_id)
         SELECT DISTINCT ON (x.note_id) x.note_id, m.id, left(m.content, 240), m.created_at, x.hits
         FROM x JOIN messages m ON m.id = x.message_id
         ORDER BY x.note_id, x.hits DESC, m.id",
        &[&word_notes, &word_convs, &word_list],
    )?;
    for r in rows {
        let id: i64 = r.get(0);
        let hits: f64 = r.get(4);
        found.insert(id, (hits / totals[&id] as f64, evidence_from(&r, 1)));
    }
    Ok(found)
}

pub fn recall<C: GenericClient>(
    client: &mut C,
    q: &RecallQuery,
    emb: Option<&QueryEmbedding>,
) -> Result<RecallResult> {
    recall_with(client, q, emb, &WEIGHTS, Utc::now())
}

/// [`recall`] with the weights of `Rank::Weighted` and the moment recency
/// and strength are measured from passed in, so an evaluation can try other
/// weights and score every question at one fixed time.
pub(crate) fn recall_with<C: GenericClient>(
    client: &mut C,
    q: &RecallQuery,
    emb: Option<&QueryEmbedding>,
    weights: &Weights,
    now: DateTime<Utc>,
) -> Result<RecallResult> {
    if let Err(e) = check_query(q.text) {
        bail!(e);
    }
    let filter = match q.mode {
        RecallMode::Current => CandidateFilter::Current,
        RecallMode::AsOf => {
            if q.valid_at.is_none() && q.known_at.is_none() {
                bail!("as_of needs valid_at or known_at");
            }
            CandidateFilter::AsOf
        }
        RecallMode::Associative => CandidateFilter::Current,
        RecallMode::Grounded => CandidateFilter::Current,
    };
    let words = query_words(q.text);
    let cands = candidates(client, filter, q, emb)?;
    let order = order_for(client, q.actor, q.rank, emb.is_some(), &words, &cands)?;
    // Under cosine, the notes that had to be ranked by their words.
    let without_vector = match order {
        Order::Cosine { .. } => cands.iter().filter(|c| c.cosine.is_none()).count(),
        _ => 0,
    };
    let mut scored: Vec<(f64, Why, Candidate)> = cands
        .into_iter()
        .map(|c| {
            let (s, why) = rank_score(&c, &words, emb.is_some(), now, weights, &order);
            (s, why, c)
        })
        .collect();
    let limit = q.limit.clamp(1, MAX_LIMIT);
    by_score(&mut scored);
    if q.mode == RecallMode::AsOf {
        // Only notes that match the question, the `limit` best by the rank;
        // those come out as a timeline.
        let min_cosine = as_of_min_cosine(emb);
        scored
            .retain(|(_, why, _)| why.lexical > 0.0 || why.cosine.is_some_and(|c| c >= min_cosine));
        scored.truncate(limit);
        scored.sort_by(|a, b| {
            a.2.valid_from
                .cmp(&b.2.valid_from)
                .then(a.2.id.cmp(&b.2.id))
        });
    }
    let mut evidence: HashMap<i64, Evidence> = HashMap::new();
    if q.mode == RecallMode::Grounded {
        // Only notes whose chain starts in an existing conversation; a hit
        // without a message to cite is dropped before the limit applies.
        // Grounding walks the ranking 2 × limit notes at a time and stops
        // once `limit` hits have evidence.
        let mut rest = std::mem::take(&mut scored)
            .into_iter()
            .filter(|s| s.2.conversation_id.is_some());
        while scored.len() < limit {
            let chunk: Vec<(f64, Why, Candidate)> = rest.by_ref().take(2 * limit).collect();
            if chunk.is_empty() {
                break;
            }
            let notes: Vec<(&Candidate, i64)> = chunk
                .iter()
                .filter_map(|s| s.2.conversation_id.map(|conv| (&s.2, conv)))
                .collect();
            let mut grounding = ground(client, &notes, emb)?;
            for (s, mut why, c) in chunk {
                if let Some((strength, ev)) = grounding.remove(&c.id) {
                    why.grounding = Some(strength);
                    evidence.insert(c.id, ev);
                    scored.push((s, why, c));
                }
            }
        }
    }
    scored.truncate(limit);
    let mut relations: HashMap<i64, String> = HashMap::new();
    if q.mode == RecallMode::Associative {
        // The seeds already fill `limit`: allow twice as many, so the
        // neighbours that rank high enough are visible.
        associate(
            client,
            q,
            emb,
            &words,
            now,
            weights,
            &mut scored,
            &mut relations,
        )?;
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
            source_at: c.source_at,
            superseded_by: c.superseded_by,
            supersedes: c.supersedes,
            score,
            why,
            evidence: evidence.remove(&c.id),
        })
        .collect();
    Ok(RecallResult {
        mode: q.mode.as_str().to_string(),
        hits,
        degraded: if needs_embedding(q.mode, q.rank) && emb.is_none() {
            Some("no_embeddings".to_string())
        } else if without_vector == 1 {
            Some("1 note without a vector".to_string())
        } else if without_vector > 1 {
            Some(format!("{without_vector} notes without a vector"))
        } else {
            None
        },
    })
}

/// One line per hit for the CLI:
/// `[m#id · valid_from · category · score] content (Quelle: title, date)`.
pub fn render_line(h: &RecallHit) -> String {
    let date = h.source_at.or(h.evidence.as_ref().map(|e| e.created_at));
    let source = match (&h.session_title, date) {
        (Some(t), Some(d)) => format!(" (Quelle: {t}, {})", d.format("%Y-%m-%d")),
        (Some(t), None) => format!(" (Quelle: {t})"),
        _ => String::new(),
    };
    format!(
        "[m#{} · {} · {} · {:.2}] {}{}",
        h.id,
        h.valid_from.format("%Y-%m-%d"),
        h.category,
        h.score,
        h.content.replace('\n', " "),
        source
    )
}

#[cfg(test)]
mod tests {
    #[test]
    fn as_of_threshold_follows_the_profile() {
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
        let granite = query_embedding_with(
            || Ok(backend("granite-embedding-multilingual:278m")),
            |_, _| Ok(vec![1.0]),
            "q",
        )
        .unwrap()
        .unwrap();
        assert_eq!(as_of_min_cosine(Some(&granite)), 0.68);
        let base = query_embedding_with(
            || Ok(backend("nomic-embed-text")),
            |_, _| Ok(vec![1.0]),
            "q",
        )
        .unwrap()
        .unwrap();
        assert_eq!(as_of_min_cosine(Some(&base)), 0.5);
    }

    #[test]
    fn refused_embedding_is_an_error() {
        let refused = || {
            Err(anyhow::Error::new(crate::llm::RemoteLlmNotAllowed {
                label: "ollama/nomic-embed-text".into(),
            }))
        };
        let r = query_embedding_with(refused, |_, _| Ok(vec![1.0]), "q");
        assert!(r.is_err());
    }

    #[test]
    fn unreachable_ollama_degrades_not_refuses() {
        let r = query_embedding_with(
            || Err(anyhow::anyhow!("ollama unreachable")),
            |_, _| Ok(vec![1.0]),
            "q",
        );
        assert!(matches!(r, Ok(None)));
    }

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

    /// Local mode: every note visible, as before team mode.
    static LOCAL: Actor = Actor::local();

    fn query<'a>(text: &'a str, project: Option<&'a str>) -> RecallQuery<'a> {
        RecallQuery {
            actor: &LOCAL,
            text,
            project,
            mode: RecallMode::Current,
            valid_at: None,
            known_at: None,
            limit: 50,
            rank: Rank::Fulltext,
        }
    }

    fn weighted<'a>(text: &'a str, project: Option<&'a str>) -> RecallQuery<'a> {
        let mut q = query(text, project);
        q.rank = Rank::Weighted;
        q
    }

    /// The default weights before 2026-10-31. Tests of one weighted part use
    /// them, since the calibrated default gives most parts no share.
    const WEIGHTS_BEFORE_2026_10_31: Weights = Weights {
        cosine: 0.45,
        lexical: 0.20,
        recency: 0.10,
        reliability: 0.10,
        strength: 0.10,
        provenance: 0.05,
    };

    fn model_name() -> String {
        format!("test-embed-{}", uuid::Uuid::new_v4())
    }

    fn query_embedding(model: &str) -> QueryEmbedding {
        QueryEmbedding {
            model: model.to_string(),
            column: "embedding_768",
            vector: vec768(&[1.0]),
            min_cosine: 0.5,
        }
    }

    fn long_ago() -> DateTime<Utc> {
        Utc::now() - Duration::days(30)
    }

    fn position(r: &RecallResult, id: i64) -> Option<usize> {
        r.hits.iter().position(|h| h.id == id)
    }

    fn candidate(now: DateTime<Utc>) -> Candidate {
        Candidate {
            id: 1,
            content: "Kafka retention policy for the audit topic".into(),
            category: "insight".into(),
            project_name: None,
            status: "active".into(),
            valid_from: now - Duration::days(40),
            valid_to: None,
            source_type: "manual".into(),
            conversation_id: None,
            session_title: None,
            superseded_by: None,
            supersedes: None,
            confidence: 0.7,
            cosine: Some(0.62),
            tags: vec![],
            source_id: None,
            merged_from: vec![],
            expires_at: None,
            source_at: None,
            access_count: 3,
            last_accessed: Some(now - Duration::days(5)),
        }
    }

    #[test]
    #[allow(non_snake_case)]
    fn score_with_custom_weights_matches_default_for_WEIGHTS() {
        let now = Utc::now();
        let c = candidate(now);
        let words = query_words("kafka retention backlog");
        let same = Weights {
            cosine: 0.85,
            lexical: 0.0,
            recency: 0.0,
            reliability: 0.15,
            strength: 0.0,
            provenance: 0.0,
        };
        assert_eq!(same, WEIGHTS);
        for has_embedding in [true, false] {
            let (a, _) = score(&c, &words, has_embedding, now, &WEIGHTS);
            let (b, _) = score(&c, &words, has_embedding, now, &same);
            assert_eq!(a.to_bits(), b.to_bits());
        }
        // Other weights take effect: the total is their sum over the same parts.
        let w = Weights {
            cosine: 0.10,
            lexical: 0.30,
            recency: 0.25,
            reliability: 0.05,
            strength: 0.20,
            provenance: 0.10,
        };
        let (total, why) = score(&c, &words, true, now, &w);
        let rest = w.lexical * why.lexical
            + w.recency * why.recency
            + w.reliability * why.reliability
            + w.strength * why.strength
            + w.provenance * why.provenance;
        assert_eq!(why.cosine, Some(0.62));
        assert!((total - (w.cosine * 0.62 + rest)).abs() < 1e-12);
        let (default_total, _) = score(&c, &words, true, now, &WEIGHTS);
        assert!((total - default_total).abs() > 1e-3);
        // Without a question vector the rest is scaled by 1 / (1 - w.cosine).
        let (scaled, why) = score(&c, &words, false, now, &w);
        assert_eq!(why.cosine, None);
        assert!((scaled - rest / (1.0 - w.cosine)).abs() < 1e-12);
    }

    /// The offline re-ranking `grid_b.py` does on a dump: the same sum in the
    /// same order as `score`, highest first, the lower id first on a tie.
    fn rerank(parts: &[crate::recall_eval::CandidateParts], w: &Weights) -> Vec<i64> {
        let mut s: Vec<(f64, i64)> = parts
            .iter()
            .map(|p| {
                let rest = w.lexical * p.lexical
                    + w.recency * p.recency
                    + w.reliability * p.reliability
                    + w.strength * p.strength
                    + w.provenance * p.provenance;
                let total = match p.cosine {
                    Some(cos) => w.cosine * cos + rest,
                    None => rest / (1.0 - w.cosine),
                };
                (total, p.id)
            })
            .collect();
        s.sort_by(|a, b| b.0.total_cmp(&a.0).then(a.1.cmp(&b.1)));
        s.into_iter().map(|x| x.1).collect()
    }

    #[test]
    fn dump_parts_reproduces_weighted_ranks() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping dump_parts_reproduces_weighted_ranks: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let model = model_name();
        let text = format!("ledger audit {tag}");
        let twin = format!("ledger audit {tag} twin");
        let half = format!("ledger {tag} half");
        let bare = format!("ledger audit {tag} no vector");
        let pinned = format!("audit {tag} pinned");
        let ago = long_ago();
        let mut s_twin = Seed::new(&twin, ago);
        s_twin.project = Some(&project);
        // Two notes with identical parts: the tie goes to the lower id.
        let t1 = seed(&mut tx, &s_twin);
        let t2 = seed(&mut tx, &s_twin);
        let mut s_half = Seed::new(&half, ago - Duration::days(200));
        s_half.project = Some(&project);
        s_half.confidence = 0.4;
        let h = seed(&mut tx, &s_half);
        let mut s_bare = Seed::new(&bare, ago);
        s_bare.project = Some(&project);
        let b = seed(&mut tx, &s_bare);
        let mut s_pin = Seed::new(&pinned, ago - Duration::days(10));
        s_pin.project = Some(&project);
        s_pin.source_type = "ui_pin";
        let p = seed(&mut tx, &s_pin);
        embed(&mut tx, t1, &model, &vec768(&[0.7, 0.7]));
        embed(&mut tx, t2, &model, &vec768(&[0.7, 0.7]));
        embed(&mut tx, h, &model, &vec768(&[0.9, 0.1]));
        embed(&mut tx, p, &model, &vec768(&[0.3, 0.9]));
        let emb = query_embedding(&model);
        let q = weighted(&text, Some(&project));
        let words = query_words(&text);
        let now = Utc::now();
        let other = Weights {
            cosine: 0.15,
            lexical: 0.35,
            recency: 0.05,
            reliability: 0.20,
            strength: 0.05,
            provenance: 0.20,
        };
        for e in [Some(&emb), None] {
            let cands = candidates(&mut tx, CandidateFilter::Current, &q, e).unwrap();
            let mut parts = crate::recall_eval::candidate_parts(&cands, &words, e.is_some(), now);
            assert_eq!(parts.len(), cands.len());
            let bare_parts = parts.iter().find(|x| x.id == b).unwrap();
            assert!(!bare_parts.has_vector);
            // With a question vector a note without one counts cosine 0;
            // without a question vector every cosine is missing.
            assert_eq!(bare_parts.cosine, e.map(|_| 0.0));
            assert_eq!(
                parts.iter().find(|x| x.id == t1).unwrap().has_vector,
                e.is_some()
            );
            // The offline ranking must not depend on the dump's order.
            parts.reverse();
            if e.is_none() {
                // Without a question vector `weighted` ranks by full text.
                let r = recall_with(&mut tx, &q, e, &WEIGHTS, now).unwrap();
                assert_eq!(r.degraded.as_deref(), Some("no_embeddings"));
                assert!(r.hits.iter().all(|h| h.why.fulltext == Some(h.score)));
                continue;
            }
            for w in [&WEIGHTS, &other] {
                let hits: Vec<i64> = recall_with(&mut tx, &q, e, w, now)
                    .unwrap()
                    .hits
                    .iter()
                    .map(|x| x.id)
                    .collect();
                let offline = rerank(&parts, w);
                assert_eq!(
                    offline[..hits.len()],
                    hits[..],
                    "weights {w:?}, vector {}",
                    e.is_some()
                );
                // Every question word matches these three, so they are in the top 50.
                for id in [t1, t2, b] {
                    assert!(hits.contains(&id), "{id} missing");
                }
                let (i1, i2) = (
                    hits.iter().position(|x| *x == t1).unwrap(),
                    hits.iter().position(|x| *x == t2).unwrap(),
                );
                assert_eq!(i2, i1 + 1, "the twins tie, the lower id first");
            }
        }
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
            &weighted("kafka retention", Some(&project)),
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
    fn weighted_ranking_prefers_a_recently_used_note() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping weighted_ranking_prefers_a_recently_used_note: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let model = model_name();
        let (ca, cb) = (
            format!("rotation plan {tag} one"),
            format!("rotation plan {tag} two"),
        );
        let mut sa = Seed::new(&ca, long_ago());
        sa.project = Some(&project);
        let mut sb = Seed::new(&cb, long_ago());
        sb.project = Some(&project);
        let a = seed(&mut tx, &sa);
        let b = seed(&mut tx, &sb);
        embed(&mut tx, a, &model, &vec768(&[0.8, 0.6]));
        embed(&mut tx, b, &model, &vec768(&[0.8, 0.6]));
        // b was used yesterday, a never
        tx.execute(
            "UPDATE memory_chunks SET access_count = 1, \
             last_accessed = now() - interval '1 day' WHERE id = $1",
            &[&b],
        )
        .unwrap();
        let emb = query_embedding(&model);
        // The calibrated default gives strength no share; the weights before do.
        let w = &WEIGHTS_BEFORE_2026_10_31;
        let r = recall_with(
            &mut tx,
            &weighted("rotation plan", Some(&project)),
            Some(&emb),
            w,
            Utc::now(),
        )
        .unwrap();
        let (ha, hb) = (
            &r.hits[position(&r, a).unwrap()],
            &r.hits[position(&r, b).unwrap()],
        );
        assert!(position(&r, b) < position(&r, a));
        assert!(hb.why.strength > ha.why.strength);
        let diff = hb.score - ha.score;
        let expected = w.strength * (hb.why.strength - ha.why.strength);
        assert!((diff - expected).abs() < 1e-9, "{diff} vs {expected}");
        assert!((hb.why.strength - lifecycle::strength(1.0, 1, false)).abs() < 1e-3);
        assert!((ha.why.strength - lifecycle::strength(30.0, 0, false)).abs() < 1e-3);
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
        let r = recall(&mut tx, &weighted("deploy checklist", Some(&project)), None).unwrap();
        assert_eq!(r.degraded.as_deref(), Some("no_embeddings"));
        let h = &r.hits[position(&r, id).unwrap()];
        assert_eq!(h.why.cosine, None);
        // Without a question vector the weighted ranking falls back to full
        // text, as cosine does.
        assert!(h.score > 0.0);
        assert_eq!(h.why.fulltext, Some(h.score));
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
            &crate::access::Actor::local(),
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

    /// A seed and a second note of the same conversation, `other` set up by
    /// the caller. Returns (seed, other).
    fn conversation_pair<C: GenericClient>(
        tx: &mut C,
        project: &str,
        other_project: Option<&str>,
        tag: &str,
    ) -> (i64, i64) {
        let conv = 987_000_000 + i64::from(uuid::Uuid::new_v4().as_u128() as u32 % 1_000_000);
        let cs = format!("lemur {tag} seed");
        let mut ss = Seed::new(&cs, long_ago());
        ss.project = Some(project);
        ss.source_type = "conversation";
        ss.source_id = Some(conv);
        let mut so = Seed::new("nothing alike", long_ago());
        so.project = other_project;
        so.source_type = "conversation";
        so.source_id = Some(conv);
        (seed(tx, &ss), seed(tx, &so))
    }

    #[test]
    fn recall_associative_never_adds_forgotten_notes() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping recall_associative_never_adds_forgotten_notes: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let (s, f) = conversation_pair(&mut tx, &project, Some(&project), &tag);
        tx.execute(
            "UPDATE memory_chunks SET status = 'forgotten' WHERE id = $1",
            &[&f],
        )
        .unwrap();
        let r = recall(
            &mut tx,
            &associative(&format!("lemur {tag}"), &project, 1),
            None,
        )
        .unwrap();
        assert!(position(&r, s).is_some());
        assert_eq!(position(&r, f), None);
    }

    #[test]
    fn recall_associative_keeps_the_project_filter() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping recall_associative_keeps_the_project_filter: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let (project, elsewhere) = (format!("p-{tag}"), format!("q-{tag}"));
        let (s, o) = conversation_pair(&mut tx, &project, Some(&elsewhere), &tag);
        let r = recall(
            &mut tx,
            &associative(&format!("lemur {tag}"), &project, 1),
            None,
        )
        .unwrap();
        assert!(position(&r, s).is_some());
        assert_eq!(position(&r, o), None);
    }

    #[test]
    fn recall_associative_does_not_pass_through_forgotten_notes() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_associative_does_not_pass_through_forgotten_notes: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let contents = [
            format!("civet {tag} head"),
            "middle".into(),
            "behind".into(),
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
        tx.execute(
            "UPDATE memory_chunks SET status = 'forgotten' WHERE id = $1",
            &[&ids[1]],
        )
        .unwrap();
        let r = recall(
            &mut tx,
            &associative(&format!("civet {tag}"), &project, 5),
            None,
        )
        .unwrap();
        assert!(position(&r, ids[0]).is_some());
        assert_eq!(position(&r, ids[1]), None);
        assert_eq!(position(&r, ids[2]), None);
    }

    #[test]
    fn recall_associative_final_score_is_the_larger_of_own_and_spread() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_associative_final_score_is_the_larger_of_own_and_spread: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let conv = 987_000_000 + i64::from(uuid::Uuid::new_v4().as_u128() as u32 % 1_000_000);
        let (cs, cn) = (
            format!("gecko {tag} seed"),
            format!("gecko {tag} neighbour"),
        );
        let mut ss = Seed::new(&cs, long_ago());
        ss.project = Some(&project);
        ss.source_type = "conversation";
        ss.source_id = Some(conv);
        // slightly ahead of the neighbour, so only the seed is a seed
        ss.confidence = 0.99;
        let mut sn = Seed::new(&cn, long_ago());
        sn.project = Some(&project);
        sn.source_type = "conversation";
        sn.source_id = Some(conv);
        sn.confidence = 0.9;
        let (s, n) = (seed(&mut tx, &ss), seed(&mut tx, &sn));
        let mut q = associative(&format!("gecko {tag}"), &project, 1);
        q.rank = Rank::Weighted;
        let r = recall(&mut tx, &q, None).unwrap();
        let (hs, hn) = (
            &r.hits[position(&r, s).unwrap()],
            &r.hits[position(&r, n).unwrap()],
        );
        assert_eq!(hn.relation.as_deref(), Some("same_conversation"));
        let spread = hn.why.spread.unwrap();
        assert!((spread - hs.score * 0.8).abs() < 1e-9);
        // own score: no embedding, so weighted ranks by full text
        let own = hn.why.fulltext.expect("full-text score");
        assert!(own > spread, "own {own} must beat spread {spread}");
        assert!((hn.score - own).abs() < 1e-9);
    }

    fn conversation<C: GenericClient>(tx: &mut C, tag: &str) -> i64 {
        tx.query_one(
            "INSERT INTO conversations (session_id, project_path, started_at, summary) \
             VALUES ($1, '/tmp/recall-test', now(), $2) RETURNING id",
            &[&uuid::Uuid::new_v4(), &format!("Session {tag}")],
        )
        .unwrap()
        .get(0)
    }

    fn message<C: GenericClient>(
        tx: &mut C,
        conv: i64,
        content: &str,
        model: &str,
        vector: Option<&[f32]>,
    ) -> i64 {
        let id: i64 = tx
            .query_one(
                "INSERT INTO messages (conversation_id, role, content, created_at) \
                 VALUES ($1, 'user', $2, now()) RETURNING id",
                &[&conv, &content],
            )
            .unwrap()
            .get(0);
        if let Some(v) = vector {
            tx.execute(
                "INSERT INTO embeddings (source_type, source_id, model, embedding_768) \
                 VALUES ('message', $1, $2, $3::text::vector)",
                &[&id, &model, &vector_literal(v)],
            )
            .unwrap();
        }
        id
    }

    fn grounded(text: &str, project: &str, limit: usize) -> RecallQuery<'static> {
        let text: &'static str = Box::leak(text.to_string().into_boxed_str());
        let project: &'static str = Box::leak(project.to_string().into_boxed_str());
        let mut q = query(text, Some(project));
        q.mode = RecallMode::Grounded;
        q.limit = limit;
        q
    }

    /// A note taken from `conv`, with project and optional note vector.
    fn sourced_note<C: GenericClient>(
        tx: &mut C,
        content: &str,
        project: &str,
        conv: i64,
        model: &str,
        vector: Option<&[f32]>,
    ) -> i64 {
        let mut s = Seed::new(content, long_ago());
        s.project = Some(project);
        s.source_type = "conversation";
        s.source_id = Some(conv);
        let id = seed(tx, &s);
        if let Some(v) = vector {
            embed(tx, id, model, v);
        }
        id
    }

    #[test]
    fn recall_grounded_returns_evidence() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_grounded_returns_evidence: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let model = model_name();
        let conv = conversation(&mut tx, &tag);
        let other = conversation(&mut tx, &tag);
        let near = vec768(&[1.0, 0.1]);
        // the far message comes first, the foreign exact match must not count
        let far = message(
            &mut tx,
            conv,
            "far away",
            &model,
            Some(&vec768(&[0.0, 1.0])),
        );
        let long = "x".repeat(500);
        let m = message(&mut tx, conv, &long, &model, Some(&near));
        let _novec = message(&mut tx, conv, "no vector here", &model, None);
        let foreign = message(&mut tx, other, "foreign", &model, Some(&vec768(&[1.0])));
        let content = format!("decision {tag}");
        let n = sourced_note(
            &mut tx,
            &content,
            &project,
            conv,
            &model,
            Some(&vec768(&[1.0])),
        );
        let emb = query_embedding(&model);
        let r = recall(&mut tx, &grounded(&tag, &project, 10), Some(&emb)).unwrap();
        let h = &r.hits[position(&r, n).unwrap()];
        let ev = h.evidence.as_ref().expect("evidence");
        assert_eq!(ev.message_id, m);
        assert_ne!(ev.message_id, far);
        assert_ne!(ev.message_id, foreign);
        assert_eq!(ev.excerpt.chars().count(), 240);
        let want = 1.0 / (1.0f64 + 0.01).sqrt();
        let g = h.why.grounding.expect("grounding");
        assert!((g - want).abs() < 1e-4, "grounding {g} vs {want}");
        assert_eq!(r.degraded, None);
        assert_eq!(r.mode, "grounded");
    }

    #[test]
    fn recall_grounded_skips_notes_without_a_source() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping recall_grounded_skips_notes_without_a_source: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let model = model_name();
        let conv = conversation(&mut tx, &tag);
        message(
            &mut tx,
            conv,
            &format!("pinned {tag}"),
            &model,
            Some(&vec768(&[1.0])),
        );
        let content = format!("pinned {tag}");
        let mut pin = Seed::new(&content, long_ago());
        pin.project = Some(&project);
        pin.source_type = "ui_pin";
        let pin_id = seed(&mut tx, &pin);
        // source_id points at a conversation that does not exist
        let gone = sourced_note(&mut tx, &content, &project, 987_654_321_000, &model, None);
        let ok = sourced_note(&mut tx, &content, &project, conv, &model, None);
        let r = recall(&mut tx, &grounded(&tag, &project, 10), None).unwrap();
        assert!(position(&r, pin_id).is_none());
        assert!(position(&r, gone).is_none());
        assert!(position(&r, ok).is_some());
    }

    #[test]
    fn recall_grounded_follows_the_chain_to_the_source() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping recall_grounded_follows_the_chain_to_the_source: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let model = model_name();
        let conv = conversation(&mut tx, &tag);
        let m = message(&mut tx, conv, "the origin", &model, Some(&vec768(&[1.0])));
        let ca = format!("old {tag}");
        let cb = format!("new {tag}");
        let a = sourced_note(&mut tx, &ca, &project, conv, &model, None);
        let mut sb = Seed::new(&cb, long_ago());
        sb.project = Some(&project);
        sb.source_type = "ui_pin";
        let b = seed(&mut tx, &sb);
        embed(&mut tx, b, &model, &vec768(&[1.0]));
        replace(&mut tx, a, b);
        let emb = query_embedding(&model);
        let r = recall(&mut tx, &grounded(&tag, &project, 10), Some(&emb)).unwrap();
        assert!(position(&r, a).is_none());
        let h = &r.hits[position(&r, b).unwrap()];
        assert_eq!(h.evidence.as_ref().unwrap().message_id, m);
        assert_eq!(h.conversation_id, Some(conv));
    }

    #[test]
    fn recall_grounded_without_embedding_uses_the_word_share() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_grounded_without_embedding_uses_the_word_share: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let model = model_name();
        let conv = conversation(&mut tx, &tag);
        message(&mut tx, conv, "Kubernetes only", &model, None);
        let best = message(
            &mut tx,
            conv,
            "the kubernetes ROLLOUT checklist",
            &model,
            None,
        );
        message(&mut tx, conv, "nothing", &model, None);
        let content = format!("kubernetes rollout checklist {tag}");
        let n = sourced_note(&mut tx, &content, &project, conv, &model, None);
        let r = recall(&mut tx, &grounded(&tag, &project, 10), None).unwrap();
        assert_eq!(r.degraded.as_deref(), Some("no_embeddings"));
        let h = &r.hits[position(&r, n).unwrap()];
        assert_eq!(h.evidence.as_ref().unwrap().message_id, best);
        let g = h.why.grounding.unwrap();
        assert!((g - 0.75).abs() < 1e-9, "grounding {g}");
    }

    #[test]
    fn recall_grounded_falls_back_per_note_without_a_vector() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_grounded_falls_back_per_note_without_a_vector: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let model = model_name();
        // conversation one has message vectors, conversation two has none
        let c1 = conversation(&mut tx, &tag);
        let c2 = conversation(&mut tx, &tag);
        message(&mut tx, c1, "vector message", &model, Some(&vec768(&[1.0])));
        let words = message(&mut tx, c2, "kubernetes rollout here", &model, None);
        let content = format!("kubernetes rollout {tag}");
        // note without a vector in c1, note with a vector in c2
        let no_vec = sourced_note(&mut tx, &content, &project, c1, &model, None);
        let no_msgs = sourced_note(
            &mut tx,
            &content,
            &project,
            c2,
            &model,
            Some(&vec768(&[1.0])),
        );
        let emb = query_embedding(&model);
        let r = recall(&mut tx, &grounded(&tag, &project, 10), Some(&emb)).unwrap();
        // c1 has no word match at all: the lexical rule gives 0 and no evidence
        assert!(position(&r, no_vec).is_none());
        let h = &r.hits[position(&r, no_msgs).unwrap()];
        assert_eq!(h.evidence.as_ref().unwrap().message_id, words);
        assert!((h.why.grounding.unwrap() - 2.0 / 3.0).abs() < 1e-9);
    }

    #[test]
    fn recall_grounded_drops_hits_without_evidence_before_the_limit() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_grounded_drops_hits_without_evidence_before_the_limit: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let model = model_name();
        let empty = conversation(&mut tx, &tag);
        let full = conversation(&mut tx, &tag);
        message(&mut tx, full, "grounded", &model, Some(&vec768(&[1.0])));
        // the note without messages is newer and ranks higher
        let c = format!("hollow {tag}");
        let mut hollow = Seed::new(&c, Utc::now());
        hollow.project = Some(&project);
        hollow.source_type = "conversation";
        hollow.source_id = Some(empty);
        hollow.confidence = 1.0;
        let hollow_id = seed(&mut tx, &hollow);
        let c2 = format!("solid {tag}");
        let solid = sourced_note(&mut tx, &c2, &project, full, &model, Some(&vec768(&[1.0])));
        let emb = query_embedding(&model);
        let mut q = grounded(&tag, &project, 1);
        q.rank = Rank::Weighted;
        let r = recall(&mut tx, &q, Some(&emb)).unwrap();
        assert!(position(&r, hollow_id).is_none());
        assert_eq!(r.hits.len(), 1);
        assert_eq!(r.hits[0].id, solid);
    }

    #[test]
    fn recall_grounded_orders_by_score_then_id() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_grounded_orders_by_score_then_id: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let model = model_name();
        let conv = conversation(&mut tx, &tag);
        message(&mut tx, conv, &format!("both {tag}"), &model, None);
        let c = format!("both {tag}");
        // the same instant for all three, so only confidence and id decide
        let when = long_ago();
        let mut ids = vec![];
        for confidence in [0.8, 0.8, 0.1] {
            let mut s = Seed::new(&c, when);
            s.project = Some(&project);
            s.source_type = "conversation";
            s.source_id = Some(conv);
            s.confidence = confidence;
            ids.push(seed(&mut tx, &s));
        }
        let mut q = grounded(&tag, &project, 10);
        q.rank = Rank::Weighted;
        let r = recall(&mut tx, &q, None).unwrap();
        let got: Vec<i64> = r.hits.iter().map(|h| h.id).collect();
        assert_eq!(got, ids);
    }

    #[test]
    fn recall_grounded_stops_grounding_at_the_limit() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping recall_grounded_stops_grounding_at_the_limit: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let model = model_name();
        let conv = conversation(&mut tx, &tag);
        message(&mut tx, conv, &format!("cited {tag}"), &model, None);
        let c = format!("cited {tag}");
        for _ in 0..12 {
            sourced_note(&mut tx, &c, &project, conv, &model, None);
        }
        GROUNDED_NOTES.with(|g| g.set(0));
        let r = recall(&mut tx, &grounded(&tag, &project, 2), None).unwrap();
        assert_eq!(r.hits.len(), 2);
        let n = GROUNDED_NOTES.with(|g| g.get());
        assert!(n <= 4, "grounded {n} notes for limit 2");
    }

    #[test]
    fn recall_grounded_word_share_counts_each_word_once() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_grounded_word_share_counts_each_word_once: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let model = model_name();
        let conv = conversation(&mut tx, &tag);
        let m = message(&mut tx, conv, "alpha only", &model, None);
        let content = format!("alpha alpha alpha beta {tag}");
        let n = sourced_note(&mut tx, &content, &project, conv, &model, None);
        let r = recall(&mut tx, &grounded(&tag, &project, 10), None).unwrap();
        let h = &r.hits[position(&r, n).unwrap()];
        assert_eq!(h.evidence.as_ref().unwrap().message_id, m);
        let g = h.why.grounding.unwrap();
        assert!((g - 1.0 / 3.0).abs() < 1e-9, "grounding {g}");
        assert_eq!(
            query_words("Docker docker DOCKER compose docker"),
            vec!["docker", "compose"]
        );
    }

    #[test]
    fn recall_current_ranks_by_full_text_on_request() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping recall_current_ranks_by_full_text_on_request: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let model = model_name();
        // words in common but a distant vector, against the closest vector
        // without a shared word
        let content = format!("wombat burrow {tag}");
        let mut sw = Seed::new(&content, long_ago());
        sw.project = Some(&project);
        let mut sv = Seed::new("marsupial den", long_ago());
        sv.project = Some(&project);
        let (w, v) = (seed(&mut tx, &sw), seed(&mut tx, &sv));
        embed(&mut tx, w, &model, &vec768(&[0.0, 1.0]));
        embed(&mut tx, v, &model, &vec768(&[1.0]));
        let emb = query_embedding(&model);
        let text = format!("wombat burrow {tag}");
        let r = recall(&mut tx, &query(&text, Some(&project)), Some(&emb)).unwrap();
        assert!(
            position(&r, w) < position(&r, v),
            "{:?}",
            r.hits.iter().map(|h| h.id).collect::<Vec<_>>()
        );
        let (hw, hv) = (
            &r.hits[position(&r, w).unwrap()],
            &r.hits[position(&r, v).unwrap()],
        );
        assert!(hw.score > 0.0);
        assert_eq!(hw.why.fulltext, Some(hw.score));
        assert_eq!(hv.why.fulltext, Some(0.0));
        assert_eq!(hv.score, 0.0);
        // the weighted parts stay filled for display
        assert_eq!(hv.why.cosine, Some(1.0));
        assert_eq!(hw.why.lexical, 1.0);
        // control: the weighted ranking puts the close vector first
        let r = recall(&mut tx, &weighted(&text, Some(&project)), Some(&emb)).unwrap();
        assert!(position(&r, v) < position(&r, w));
        assert_eq!(r.hits[position(&r, v).unwrap()].why.fulltext, None);
    }

    #[test]
    fn cosine_rank_orders_by_vector_similarity() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping cosine_rank_orders_by_vector_similarity: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let model = model_name();
        // `far` shares every question word, `near` only the tag but has the
        // closer vector; `bare` has an orthogonal one. (A note without a
        // vector: `cosine_rank_scores_vectorless_notes_by_words_and_says_so`.)
        let text = format!("kafka retention backlog {tag}");
        let (cf, cn, cb) = (
            format!("kafka retention backlog {tag} far"),
            format!("{tag} near"),
            format!("kafka {tag} bare"),
        );
        let mut sf = Seed::new(&cf, long_ago());
        sf.project = Some(&project);
        let mut sn = Seed::new(&cn, long_ago());
        sn.project = Some(&project);
        let mut sb = Seed::new(&cb, long_ago());
        sb.project = Some(&project);
        let (far, near, bare) = (seed(&mut tx, &sf), seed(&mut tx, &sn), seed(&mut tx, &sb));
        embed(&mut tx, far, &model, &vec768(&[0.2, 0.98]));
        embed(&mut tx, near, &model, &vec768(&[0.95, 0.31]));
        embed(&mut tx, bare, &model, &vec768(&[0.0, 1.0]));
        let emb = query_embedding(&model);
        let mut q = query(&text, Some(&project));
        q.rank = Rank::Cosine;
        let r = recall(&mut tx, &q, Some(&emb)).unwrap();
        assert_eq!(r.degraded, None);
        let order: Vec<i64> = r
            .hits
            .iter()
            .map(|h| h.id)
            .filter(|id| [far, near, bare].contains(id))
            .collect();
        assert_eq!(order, vec![near, far, bare], "closest vector first");
        for h in r.hits.iter().filter(|h| [far, near, bare].contains(&h.id)) {
            // the score is the cosine itself, no word share, no full text
            assert_eq!(Some(h.score), h.why.cosine, "{}", h.id);
            assert_eq!(h.why.fulltext, None);
        }
        let score = |id: i64| r.hits[position(&r, id).unwrap()].score;
        assert!((score(near) - 0.95 / (0.95f64.powi(2) + 0.31f64.powi(2)).sqrt()).abs() < 1e-3);
        assert!((score(far) - 0.2 / (0.2f64.powi(2) + 0.98f64.powi(2)).sqrt()).abs() < 1e-3);
        assert_eq!(score(bare), 0.0);
        // control: full text puts the note with every word first
        q.rank = Rank::Fulltext;
        let r = recall(&mut tx, &q, Some(&emb)).unwrap();
        assert!(position(&r, far) < position(&r, near));
    }

    #[test]
    fn cosine_rank_scores_vectorless_notes_by_words_and_says_so() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping cosine_rank_scores_vectorless_notes_by_words_and_says_so: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let model = model_name();
        // `fresh` and `half` have no vector yet; `fresh` holds every question
        // word, `half` two of them. `near` and `far` have vectors.
        let text = format!("kafka retention backlog {tag}");
        let contents = [
            format!("kafka retention backlog {tag} fresh"),
            format!("kafka {tag} half"),
            format!("{tag} near"),
            format!("{tag} far"),
        ];
        let ids: Vec<i64> = contents
            .iter()
            .map(|c| {
                let mut s = Seed::new(c, long_ago());
                s.project = Some(&project);
                seed(&mut tx, &s)
            })
            .collect();
        let (fresh, half, near, far) = (ids[0], ids[1], ids[2], ids[3]);
        embed(&mut tx, near, &model, &vec768(&[0.95, 0.31]));
        embed(&mut tx, far, &model, &vec768(&[0.2, 0.98]));
        let emb = query_embedding(&model);
        let mut q = query(&text, Some(&project));
        q.rank = Rank::Cosine;
        let r = recall(&mut tx, &q, Some(&emb)).unwrap();
        assert_eq!(r.degraded.as_deref(), Some("2 notes without a vector"));
        let hit = |id: i64| &r.hits[position(&r, id).unwrap()];
        // A note without a vector scores its full-text rank over the best one.
        let best = hit(fresh).why.fulltext.unwrap();
        assert!(best > 0.0);
        assert_eq!(hit(fresh).score, 1.0);
        assert_eq!(hit(fresh).why.cosine, None);
        let part = hit(half).why.fulltext.unwrap();
        assert!(part > 0.0 && part < best, "{part} vs {best}");
        assert!((hit(half).score - part / best).abs() < 1e-12);
        // Notes with a vector keep their cosine.
        assert_eq!(Some(hit(near).score), hit(near).why.cosine);
        assert!(position(&r, fresh) < position(&r, near));
        assert!(position(&r, near) < position(&r, far));
        embed(&mut tx, fresh, &model, &vec768(&[0.5, 0.5]));
        let r = recall(&mut tx, &q, Some(&emb)).unwrap();
        assert_eq!(r.degraded.as_deref(), Some("1 note without a vector"));
        // Every candidate with a vector: nothing to report.
        embed(&mut tx, half, &model, &vec768(&[0.1, 0.9]));
        let r = recall(&mut tx, &q, Some(&emb)).unwrap();
        assert_eq!(r.degraded, None);
    }

    #[test]
    fn default_rank_breaker_skips_ollama_for_60s_after_a_failure() {
        use crate::embedding::{EmbedBreaker, BREAKER_WINDOW_MS};
        use std::cell::Cell;
        let breaker = EmbedBreaker::new();
        let calls = Cell::new(0);
        let t0 = 1_000_000_000;
        let failing = || -> Result<Option<QueryEmbedding>> {
            calls.set(calls.get() + 1);
            Ok(None)
        };
        let working = || -> Result<Option<QueryEmbedding>> {
            calls.set(calls.get() + 1);
            Ok(Some(query_embedding("m")))
        };
        let ask = |now: i64, quick: &dyn Fn() -> Result<Option<QueryEmbedding>>| {
            question_vector_with(RecallMode::Current, None, &breaker, now, quick, || {
                panic!("the default rank never takes the long path")
            })
            .unwrap()
        };
        assert!(ask(t0, &failing).is_none());
        assert_eq!(calls.get(), 1);
        // Inside the minute Ollama is not asked again.
        assert!(ask(t0 + BREAKER_WINDOW_MS - 1, &working).is_none());
        assert_eq!(calls.get(), 1);
        // An explicit rank ignores the breaker and takes the long path.
        let long = question_vector_with(
            RecallMode::Current,
            Some(Rank::Cosine),
            &breaker,
            t0 + 1,
            || panic!("an explicit rank never takes the quick path"),
            || Ok(Some(query_embedding("m"))),
        )
        .unwrap();
        assert!(long.is_some());
        // After the minute it asks again.
        assert!(ask(t0 + BREAKER_WINDOW_MS, &working).is_some());
        assert_eq!(calls.get(), 2);
        // A timeout counts as a failure; a refusal stays an error and does not trip.
        let timed_out = EmbedBreaker::new();
        let r = default_query_embedding_with(&timed_out, t0, || {
            Err(anyhow::anyhow!("no answer within 3000 ms"))
        });
        assert!(matches!(r, Ok(None)));
        assert!(timed_out.is_open(t0 + 1));
        let refused = EmbedBreaker::new();
        let r = default_query_embedding_with(&refused, t0, || {
            Err(anyhow::Error::new(crate::llm::RemoteLlmNotAllowed {
                label: "ollama/x".into(),
            }))
        });
        assert!(r.is_err());
        assert!(!refused.is_open(t0 + 1));
    }

    #[test]
    fn default_rank_is_cosine() {
        // Block 8, B2 (2026-10-31): on 74 questions full text hit 50.0 % at
        // k = 5, cosine 94.6 %. By the rule set in block 2 the default left
        // full text for the method with the most hits.
        assert_eq!(DEFAULT_RANK, Rank::Cosine);
        assert_eq!(DEFAULT_RANK.as_str().parse::<Rank>(), Ok(DEFAULT_RANK));
        // so the default asks for a question vector and falls back without one
        assert!(needs_embedding(RecallMode::Current, DEFAULT_RANK));
    }

    #[test]
    fn weighted_default_weights_are_calibrated() {
        // Calibrated in block 8, B3, on 42 questions of one installation.
        // Before 2026-10-31 the default was cosine 0.45, lexical 0.20,
        // recency 0.10, reliability 0.10, strength 0.10, provenance 0.05
        // (`WEIGHTS_BEFORE_2026_10_31`).
        assert_eq!(
            WEIGHTS,
            Weights {
                cosine: 0.85,
                lexical: 0.0,
                recency: 0.0,
                reliability: 0.15,
                strength: 0.0,
                provenance: 0.0,
            }
        );
        let w = &WEIGHTS;
        let sum = w.cosine + w.lexical + w.recency + w.reliability + w.strength + w.provenance;
        assert!((sum - 1.0).abs() < 1e-12, "{sum}");
    }

    #[test]
    fn rank_parses_and_decides_the_embedding() {
        assert_eq!("fulltext".parse::<Rank>(), Ok(Rank::Fulltext));
        assert_eq!("weighted".parse::<Rank>(), Ok(Rank::Weighted));
        assert_eq!("cosine".parse::<Rank>(), Ok(Rank::Cosine));
        assert!("Weighted".parse::<Rank>().is_err());
        assert!("".parse::<Rank>().is_err());
        assert_eq!(Rank::Weighted.as_str(), "weighted");
        use RecallMode::*;
        assert!(!needs_embedding(Current, Rank::Fulltext));
        assert!(!needs_embedding(Associative, Rank::Fulltext));
        assert!(needs_embedding(Current, Rank::Weighted));
        assert!(needs_embedding(Associative, Rank::Weighted));
        assert!(needs_embedding(Current, Rank::Cosine));
        assert_eq!(Rank::Cosine.as_str(), "cosine");
        assert!(needs_embedding(AsOf, Rank::Fulltext));
        assert!(needs_embedding(Grounded, Rank::Fulltext));
    }

    #[test]
    fn fulltext_query_is_safe() {
        let words = query_words("it's a & b | c: d Müller straße");
        let q = fulltext_tsquery(&words).expect("words present");
        assert!(q
            .chars()
            .all(|c| matches!(c, 'a'..='z' | '0'..='9' | 'ä' | 'ö' | 'ü' | 'ß' | ' ' | '|')));
        assert_eq!(q, "müller | straße");
        let dirty = vec!["a'b&c".to_string(), "x:y".to_string()];
        assert_eq!(fulltext_tsquery(&dirty).unwrap(), "abc | xy");
        assert_eq!(fulltext_tsquery(&[]), None);
        assert_eq!(fulltext_tsquery(&["'&|".to_string()]), None);
    }

    #[test]
    fn recall_current_full_text_needs_no_embedding() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping recall_current_full_text_needs_no_embedding: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let content = format!("deploy checklist {tag}");
        let mut s = Seed::new(&content, long_ago());
        s.project = Some(&project);
        let id = seed(&mut tx, &s);
        let text = format!("deploy checklist {tag}");
        let r = recall(&mut tx, &query(&text, Some(&project)), None).unwrap();
        assert_eq!(r.degraded, None);
        assert_eq!(position(&r, id), Some(0));
    }

    #[test]
    fn recall_as_of_keeps_the_most_relevant_then_orders_by_time() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_as_of_keeps_the_most_relevant_then_orders_by_time: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        // three old notes share one word, two later ones share all three;
        // the latest is the most relevant by both ranks
        let notes = [
            (format!("{tag} alpha"), at(2026, 1, 1)),
            (format!("{tag} beta"), at(2026, 1, 10)),
            (format!("{tag} gamma"), at(2026, 1, 20)),
            (format!("zebra kiwi {tag} zebra kiwi"), at(2026, 3, 1)),
            (format!("zebra kiwi {tag}"), at(2026, 2, 1)),
        ];
        let ids: Vec<i64> = notes
            .iter()
            .map(|(c, from)| {
                let mut sd = Seed::new(c, *from);
                sd.project = Some(&project);
                seed(&mut tx, &sd)
            })
            .collect();
        let text = format!("zebra kiwi {tag}");
        // Without a vector `weighted` falls back to full text.
        for rank in [Rank::Fulltext, Rank::Weighted] {
            let mut q = query(&text, Some(&project));
            q.mode = RecallMode::AsOf;
            q.valid_at = Some(at(2026, 6, 1));
            q.known_at = Some(Utc::now());
            q.limit = 2;
            q.rank = rank;
            let r = recall(&mut tx, &q, None).unwrap();
            let got: Vec<i64> = r.hits.iter().map(|h| h.id).collect();
            assert_eq!(got, vec![ids[4], ids[3]], "{rank:?}");
        }
    }

    #[test]
    fn recall_associative_marks_neighbours_that_no_longer_hold() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_associative_marks_neighbours_that_no_longer_hold: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let cases = [
            ("", "same_conversation"),
            ("valid_to = now() - interval '1 day'", "expired"),
            ("expires_at = now() - interval '1 day'", "expired"),
            ("status = 'stale'", "expired"),
            (
                "valid_to = now() - interval '1 day', status = 'superseded'",
                "superseded",
            ),
        ];
        for (set, want) in cases {
            let tag = uuid::Uuid::new_v4().simple().to_string();
            let project = format!("p-{tag}");
            let (s, o) = conversation_pair(&mut tx, &project, Some(&project), &tag);
            if !set.is_empty() {
                tx.execute(
                    &format!("UPDATE memory_chunks SET {set} WHERE id = $1"),
                    &[&o],
                )
                .unwrap();
            }
            let q = associative(&format!("lemur {tag}"), &project, 1);
            let r = recall(&mut tx, &q, None).unwrap();
            assert_eq!(r.hits[position(&r, s).unwrap()].relation, None);
            let ho = &r.hits[position(&r, o).unwrap_or_else(|| panic!("{set}"))];
            assert_eq!(ho.relation.as_deref(), Some(want), "{set}");
        }
    }

    #[test]
    fn recall_refuses_a_query_over_2000_characters() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping recall_refuses_a_query_over_2000_characters: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().unwrap();
        let at_cap = "abc ".repeat(500);
        assert_eq!(at_cap.chars().count(), 2000);
        assert!(recall(&mut tx, &query(&at_cap, None), None).is_ok());
        let over = format!("{at_cap}x");
        let err = recall(&mut tx, &query(&over, None), None)
            .expect_err("2001 characters must be refused");
        assert!(err.to_string().contains("2000"), "{err}");
        // characters, not bytes
        let umlauts = "ü".repeat(2000);
        assert!(recall(&mut tx, &query(&umlauts, None), None).is_ok());
        assert!(check_query(&umlauts).is_ok());
        assert!(check_query(&over).is_err());
        assert_eq!(MAX_QUERY_CHARS, 2000);
    }

    #[test]
    fn recall_hits_carry_the_source_date() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_hits_carry_the_source_date: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let model = model_name();
        let conv: i64 = tx
            .query_one(
                "INSERT INTO conversations (session_id, project_path, started_at, summary) \
                 VALUES ($1, '/tmp/recall-test', $2, $3) RETURNING id",
                &[
                    &uuid::Uuid::new_v4(),
                    &at(2026, 2, 20),
                    &format!("Session {tag}"),
                ],
            )
            .unwrap()
            .get(0);
        let (ca, cb, cc) = (
            format!("old {tag}"),
            format!("new {tag}"),
            format!("undated {tag}"),
        );
        let a = sourced_note(&mut tx, &ca, &project, conv, &model, None);
        let mut sb = Seed::new(&cb, long_ago());
        sb.project = Some(&project);
        sb.source_type = "ui_pin";
        let b = seed(&mut tx, &sb);
        replace(&mut tx, a, b);
        let c = sourced_note(&mut tx, &cc, &project, conv, &model, None);
        for (id, when) in [
            (a, Some(at(2026, 2, 28))),
            (b, Some(at(2026, 4, 1))),
            (c, None),
        ] {
            tx.execute(
                "UPDATE memory_chunks SET source_at = $2 WHERE id = $1",
                &[&id, &when],
            )
            .unwrap();
        }
        let r = recall(&mut tx, &query(&tag, Some(&project)), None).unwrap();
        let json = |id: i64| serde_json::to_value(&r.hits[position(&r, id).unwrap()]).unwrap();
        // the chain root's source date, else the start of the conversation
        assert_eq!(json(b)["source_at"], "2026-02-28T00:00:00Z");
        assert_eq!(json(c)["source_at"], "2026-02-20T00:00:00Z");
        let line = render_line(&r.hits[position(&r, b).unwrap()]);
        assert!(line.starts_with(&format!("[m#{b} · ")), "{line}");
        assert!(
            line.ends_with(&format!("new {tag} (Quelle: Session {tag}, 2026-02-28)")),
            "{line}"
        );
    }

    #[test]
    fn recall_hides_private_notes_of_others() {
        use crate::access::team_test::{assert_hidden, note, two_accounts};
        let Some(mut client) = test_db() else {
            eprintln!("skipping recall_hides_private_notes_of_others: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let (a, b) = two_accounts(&mut tx);
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("p-{tag}");
        let secret = format!("secret{tag}");
        let p = Some(project.as_str());
        // A shared note that A's private note replaced, the private note,
        // a shared neighbour of it (same file) and a note one step behind it
        // (same conversation). B sees the shared notes, never the private one,
        // and no walk passes through it.
        let old = note(
            &mut tx,
            None,
            "shared",
            &format!("plan{tag} old"),
            "insight",
            p,
        );
        let private = note(
            &mut tx,
            Some(a.user_id.unwrap()),
            "private",
            &format!("plan{tag} {secret}"),
            "insight",
            p,
        );
        replace(&mut tx, old, private);
        let near = note(
            &mut tx,
            None,
            "shared",
            &format!("zebra{tag} near"),
            "insight",
            p,
        );
        let behind = note(
            &mut tx,
            None,
            "shared",
            &format!("behind{tag}"),
            "insight",
            p,
        );
        let merged = note(
            &mut tx,
            None,
            "shared",
            &format!("merged{tag}"),
            "insight",
            p,
        );
        let conv = 986_000_000 + i64::from(uuid::Uuid::new_v4().as_u128() as u32 % 1_000_000);
        tx.execute(
            "UPDATE memory_chunks SET tags = ARRAY[$2]::text[] WHERE id = ANY($1)",
            &[&vec![near, private], &format!("file:{tag}.rs")],
        )
        .unwrap();
        tx.execute(
            "UPDATE memory_chunks SET source_type = 'conversation', source_id = $2 WHERE id = ANY($1)",
            &[&vec![private, behind], &conv],
        )
        .unwrap();
        tx.execute(
            "UPDATE memory_chunks SET merged_from = ARRAY[$2]::bigint[] WHERE id = $1",
            &[&merged, &private],
        )
        .unwrap();

        fn run(
            tx: &mut postgres::Transaction<'_>,
            actor: &Actor,
            q: RecallQuery<'_>,
        ) -> RecallResult {
            recall(tx, &RecallQuery { actor, ..q }, None).unwrap()
        }
        let ids = |r: &RecallResult| -> Vec<i64> {
            r.hits
                .iter()
                .flat_map(|h| [Some(h.id), h.superseded_by, h.supersedes])
                .flatten()
                .collect()
        };
        let as_of = |text: &'static str| {
            let mut q = query(text, None);
            q.mode = RecallMode::AsOf;
            q.valid_at = Some(Utc::now());
            q
        };
        let leak = |s: String| -> &'static str { Box::leak(s.into_boxed_str()) };
        let current_q = || query(leak(format!("plan{tag} {secret}")), None);
        let as_of_q = || as_of(leak(format!("plan{tag}")));
        let near_q = || associative(&format!("zebra{tag}"), &project, 1);
        let behind_q = || associative(&format!("behind{tag}"), &project, 1);

        // A, the owner, sees the note, the chain and the neighbours.
        let r = run(&mut tx, &a, current_q());
        assert!(position(&r, private).is_some(), "A must see her note");
        let r = run(&mut tx, &a, as_of_q());
        assert_eq!(
            r.hits[position(&r, old).unwrap()].superseded_by,
            Some(private)
        );
        for q in [near_q(), behind_q()] {
            let r = run(&mut tx, &a, q);
            assert!(position(&r, private).is_some(), "a neighbour of A's");
        }

        // B sees none of it.
        for (what, r) in [
            ("current", run(&mut tx, &b, current_q())),
            ("as_of", run(&mut tx, &b, as_of_q())),
            ("associative by file", run(&mut tx, &b, near_q())),
            ("associative by conversation", run(&mut tx, &b, behind_q())),
        ] {
            assert!(!ids(&r).contains(&private), "{what}: {:?}", ids(&r));
            assert_hidden(&serde_json::to_string(&r).unwrap(), &secret, what);
            if what == "as_of" {
                let h = &r.hits[position(&r, old).expect("the shared old note stays")];
                assert_eq!(h.superseded_by, None, "the chain ends at the private note");
            }
        }
        // No edge leads B to the private note, so no walk passes through it:
        // from its shared neighbours B reaches nothing behind it.
        for frontier in [vec![near], vec![behind], vec![merged], vec![old]] {
            let edges = neighbour_edges(&mut tx, &b, &frontier).unwrap();
            assert!(
                edges.iter().all(|e| e.0 != private),
                "{frontier:?}: {edges:?}"
            );
            let mine = neighbour_edges(&mut tx, &a, &frontier).unwrap();
            assert!(
                mine.iter().any(|e| e.0 == private),
                "{frontier:?}: {mine:?}"
            );
        }
        // Weighted candidates of B: the merge member list is cut too.
        let cands = candidates(
            &mut tx,
            CandidateFilter::Current,
            &RecallQuery {
                actor: &b,
                ..query("", Some(&project))
            },
            None,
        )
        .unwrap();
        assert!(cands
            .iter()
            .all(|c| c.id != private && !c.merged_from.contains(&private)));

        // The other direction: a shared note that replaced a private note of
        // A's, which came from a session. B sees the shared successor, but
        // neither its predecessor nor the session at the root of the chain.
        let session = conversation(&mut tx, &tag);
        let first = note(
            &mut tx,
            a.user_id,
            "private",
            &format!("draft{tag} {secret}"),
            "insight",
            p,
        );
        tx.execute(
            "UPDATE memory_chunks SET source_type = 'conversation', source_id = $2 WHERE id = $1",
            &[&first, &session],
        )
        .unwrap();
        let after = note(
            &mut tx,
            None,
            "shared",
            &format!("final{tag}"),
            "insight",
            p,
        );
        replace(&mut tx, first, after);
        let final_q = || query(leak(format!("final{tag}")), None);
        let r = run(&mut tx, &a, final_q());
        let h = &r.hits[position(&r, after).expect("A's hit")];
        assert_eq!(
            (h.supersedes, h.conversation_id),
            (Some(first), Some(session))
        );
        let r = run(&mut tx, &b, final_q());
        let h = &r.hits[position(&r, after).expect("B's hit")];
        assert_eq!(h.supersedes, None, "no predecessor B does not see");
        assert_eq!(
            (h.conversation_id, h.session_title.as_deref()),
            (None, None),
            "the root walk stops before the private note"
        );
        assert_hidden(
            &serde_json::to_string(&r).unwrap(),
            &secret,
            "the successor's hit",
        );
    }
}
