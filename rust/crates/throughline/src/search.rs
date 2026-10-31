//! Lexical + semantic search over the ingested corpus.
//!
//! Ranking prefers memory hits and project instructions over raw messages,
//! and applies a mild recency boost so recent evidence surfaces first.

use crate::access::{push_visibility, Actor};
use crate::embedding::EmbeddingBackend;
use crate::generate_embeddings;
use anyhow::Result;
use chrono::{DateTime, Utc};
use postgres::types::ToSql;
use postgres::GenericClient;

#[derive(Debug, Clone)]
pub struct SearchHit {
    pub message_id: Option<i64>,
    pub conversation_id: i64,
    pub session_id: uuid::Uuid,
    pub project_name: Option<String>,
    pub source_tool: Option<String>,
    pub role: String,
    pub snippet: String,
    pub created_at: DateTime<Utc>,
    /// Higher is better. Cosine similarity for semantic; blended score for lexical.
    pub score: f32,
    pub kind: &'static str,
}

/// Base score by kind: memory and instructions matter more for continuity.
pub fn kind_bonus(kind: &str) -> f32 {
    match kind {
        "memory" => 0.25,
        "instruction" => 0.15,
        "semantic" => 0.10,
        _ => 0.0,
    }
}

/// Recency bonus: recent items get a mild boost (max 0.15).
pub fn recency_bonus(created_at: DateTime<Utc>) -> f32 {
    let now = Utc::now();
    let age_hours = (now - created_at).num_hours().max(0) as f32;
    if age_hours <= 24.0 {
        0.15
    } else if age_hours <= 72.0 {
        0.10
    } else if age_hours <= 168.0 {
        0.05
    } else {
        0.0
    }
}

/// Compute final blended score.
pub fn blended_score(base: f32, kind: &str, created_at: DateTime<Utc>) -> f32 {
    base + kind_bonus(kind) + recency_bonus(created_at)
}

/// ILIKE ranking over message text. With a project, only that project's
/// conversations are searched and configs (which carry no project) are left out.
/// Configs also stay out for an actor below `admin` (team mode, see
/// [`Actor::sees_assistant_configs`]).
pub fn search<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    query: &str,
    limit: i32,
    project: Option<&str>,
) -> Result<Vec<SearchHit>> {
    let like = format!("%{}%", query.replace('%', "\\%").replace('_', "\\_"));
    let rows = client.query(
        r#"
        SELECT m.id,
               m.conversation_id,
               c.session_id,
               c.project_name,
               c.source_tool,
               m.role::text,
               left(m.content, 240),
               m.created_at
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE m.content ILIKE $1
          AND ($3::text IS NULL OR c.project_name = $3)
        ORDER BY m.created_at DESC
        LIMIT $2::bigint
        "#,
        &[&like, &(limit as i64), &project],
    )?;

    let mut hits: Vec<SearchHit> = rows
        .into_iter()
        .map(|r| {
            let created_at: DateTime<Utc> = r.get(7);
            let base = 0.5;
            SearchHit {
                message_id: r.get(0),
                conversation_id: r.get(1),
                session_id: r.get(2),
                project_name: r.get(3),
                source_tool: r.get(4),
                role: r.get(5),
                snippet: r.get(6),
                created_at,
                score: blended_score(base, "lexical", created_at),
                kind: "lexical",
            }
        })
        .collect();
    if project.is_some() || !actor.sees_assistant_configs() {
        return Ok(hits);
    }
    let rows = client.query(
        r#"
        SELECT id, name, kind, left(content, 240), imported_at
        FROM assistant_configs
        WHERE present AND (content ILIKE $1 OR name ILIKE $1 OR path ILIKE $1)
        ORDER BY imported_at DESC
        LIMIT $2::bigint
        "#,
        &[&like, &(limit as i64)],
    )?;
    hits.extend(rows.into_iter().map(|r| SearchHit {
        message_id: Some(r.get(0)),
        conversation_id: 0,
        session_id: uuid::Uuid::nil(),
        project_name: Some(r.get(1)),
        source_tool: Some(r.get(2)),
        role: "config".into(),
        snippet: r.get(3),
        created_at: r.get(4),
        score: 0.85,
        kind: "config",
    }));
    Ok(hits)
}

/// Vector search over `embeddings` (pgvector cosine distance).
pub fn semantic<C: GenericClient>(
    client: &mut C,
    backend: &EmbeddingBackend,
    query: &str,
    limit: i32,
) -> Result<Vec<SearchHit>> {
    // Reuse embed + distance query, then hydrate conversation fields.
    let raw = generate_embeddings::semantic_search(client, backend, query, limit)?;
    if raw.is_empty() {
        return Ok(vec![]);
    }
    let mut hits = Vec::with_capacity(raw.len());
    for (message_id, _snippet, score) in raw {
        let rows = client.query(
            r#"
            SELECT m.id,
                   m.conversation_id,
                   c.session_id,
                   c.project_name,
                   c.source_tool,
                   m.role::text,
                   left(m.content, 240),
                   m.created_at
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.id = $1
            "#,
            &[&message_id],
        )?;
        if let Some(r) = rows.into_iter().next() {
            let created_at: DateTime<Utc> = r.get(7);
            hits.push(SearchHit {
                message_id: r.get(0),
                conversation_id: r.get(1),
                session_id: r.get(2),
                project_name: r.get(3),
                source_tool: r.get(4),
                role: r.get(5),
                snippet: r.get(6),
                created_at,
                score: blended_score(score, "semantic", created_at),
                kind: "semantic",
            });
        } else {
            // Zeitgrundsatz: a semantic hit whose message row is gone is
            // dropped, not backfilled with import time.
            continue;
        }
    }
    Ok(hits)
}

/// Lexical + semantic, deduped by message id, sorted by score then recency.
/// Falls back to lexical when the embedding backend is down.
pub fn hybrid<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    backend: Option<&EmbeddingBackend>,
    query: &str,
    limit: i32,
) -> Result<Vec<SearchHit>> {
    let mut hits: Vec<SearchHit> = Vec::new();
    if let Some(backend) = backend {
        match semantic(client, backend, query, limit) {
            Ok(mut sem) => hits.append(&mut sem),
            Err(e) => {
                eprintln!("search: semantic skipped: {e}");
            }
        }
    }
    let mut lex = search(client, actor, query, limit, None)?;
    hits.append(&mut lex);
    // Durable notes (agent memory, project instructions) are first-class hits —
    // otherwise Find never surfaces MiMo/agent memory in the GUI.
    hits.append(&mut memory_hits(client, actor, query, limit)?);

    let mut seen = std::collections::HashSet::new();
    hits.retain(|h| {
        let key = format!("{}:{}", h.kind, h.message_id.unwrap_or(0));
        seen.insert(key)
    });
    hits.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| b.created_at.cmp(&a.created_at))
    });
    hits.truncate(limit.max(1) as usize);
    Ok(hits)
}

/// Lexical hits from `memory_chunks` and indexed instruction files, and
/// imported configs for an actor who may see them.
pub(crate) fn memory_hits<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    query: &str,
    limit: i32,
) -> Result<Vec<SearchHit>> {
    let like = format!("%{}%", query.replace('%', "\\%").replace('_', "\\_"));
    let mut hits = Vec::new();
    let limit64 = limit as i64;
    let mut params: Vec<&(dyn ToSql + Sync)> = vec![&like, &limit64];
    let seen = push_visibility(actor, "ch", &mut params);
    let rows = client.query(
        &format!(
            r#"
        SELECT ch.id, COALESCE(ch.source_id, 0), COALESCE(ch.project_name, ''),
               ch.source_type, 'memory', left(ch.content, 240), ch.created_at
        FROM memory_chunks ch
        WHERE ch.content ILIKE $1 AND ch.status = 'active' AND {seen}
        ORDER BY ch.created_at DESC
        LIMIT $2::bigint
        "#
        ),
        &params,
    )?;
    hits.extend(rows.into_iter().map(|r| SearchHit {
        message_id: Some(r.get(0)),
        conversation_id: r.get(1),
        session_id: uuid::Uuid::nil(),
        project_name: {
            let p: String = r.get(2);
            if p.is_empty() {
                None
            } else {
                Some(p)
            }
        },
        source_tool: r.get(3),
        role: r.get(4),
        snippet: r.get(5),
        created_at: r.get(6),
        // Durable notes that literally match the query outrank fuzzy semantic
        // hits so Find surfaces agent/project memory in the GUI.
        score: 0.9,
        kind: "memory",
    }));
    if let Ok(rows) = client.query(
        r#"
        SELECT id, name, category, left(content, 240), updated_at
        FROM prompts
        WHERE content ILIKE $1 OR name ILIKE $1
        ORDER BY updated_at DESC
        LIMIT $2::bigint
        "#,
        &[&like, &(limit as i64)],
    ) {
        hits.extend(rows.into_iter().map(|r| SearchHit {
            message_id: Some(r.get(0)),
            conversation_id: 0,
            session_id: uuid::Uuid::nil(),
            project_name: Some(r.get(1)),
            source_tool: Some(r.get(2)),
            role: "instruction".into(),
            snippet: r.get(3),
            created_at: r.get(4),
            score: 0.85,
            kind: "instruction",
        }));
    }
    if !actor.sees_assistant_configs() {
        return Ok(hits);
    }
    if let Ok(rows) = client.query(
        r#"
        SELECT id, name, kind, left(content, 240), imported_at
        FROM assistant_configs
        WHERE present AND (content ILIKE $1 OR name ILIKE $1 OR path ILIKE $1)
        ORDER BY imported_at DESC
        LIMIT $2::bigint
        "#,
        &[&like, &(limit as i64)],
    ) {
        hits.extend(rows.into_iter().map(|r| SearchHit {
            message_id: Some(r.get(0)),
            conversation_id: 0,
            session_id: uuid::Uuid::nil(),
            project_name: Some(r.get(1)),
            source_tool: Some(r.get(2)),
            role: "config".into(),
            snippet: r.get(3),
            created_at: r.get(4),
            score: 0.85,
            kind: "config",
        }));
    }
    Ok(hits)
}

pub fn count_conversations<C: GenericClient>(client: &mut C) -> Result<i64> {
    Ok(client
        .query_one("SELECT COUNT(*) FROM conversations", &[])?
        .get(0))
}

pub fn count_messages<C: GenericClient>(client: &mut C) -> Result<i64> {
    Ok(client
        .query_one("SELECT COUNT(*) FROM messages", &[])?
        .get(0))
}

/// Which corpus to search (R4).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SearchSource {
    Messages,
    Memory,
    All,
}

impl SearchSource {
    pub fn parse(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "memory" => Self::Memory,
            "messages" | "message" => Self::Messages,
            _ => Self::All,
        }
    }
}

/// Source-aware lexical search: messages, extracted memory, or both.
/// Memory and instruction hits are scored higher; recency boost is applied.
pub fn search_from<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    query: &str,
    limit: i32,
    source: SearchSource,
) -> Result<Vec<SearchHit>> {
    if source == SearchSource::Messages {
        return search(client, actor, query, limit, None);
    }
    let like = format!("%{}%", query.replace('%', "\\%").replace('_', "\\_"));
    let mut hits = Vec::new();
    if matches!(source, SearchSource::Memory | SearchSource::All) {
        let limit64 = limit as i64;
        let mut params: Vec<&(dyn ToSql + Sync)> = vec![&like, &limit64];
        let seen = push_visibility(actor, "ch", &mut params);
        let rows = client.query(
            &format!(
                r#"
            SELECT ch.id,
                   COALESCE(ch.source_id, 0),
                   COALESCE(ch.project_name, ''),
                   ch.source_type,
                   'memory',
                   left(ch.content, 240),
                   ch.created_at
            FROM memory_chunks ch
            WHERE ch.content ILIKE $1
              AND ch.status = 'active'
              AND {seen}
            ORDER BY ch.created_at DESC
            LIMIT $2::bigint
            "#
            ),
            &params,
        )?;
        hits.extend(rows.into_iter().map(|r| {
            let created_at: DateTime<Utc> = r.get(6);
            let project_name: String = r.get(2);
            SearchHit {
                message_id: Some(r.get(0)),
                conversation_id: r.get(1),
                session_id: uuid::Uuid::nil(),
                project_name: {
                    if project_name.is_empty() {
                        None
                    } else {
                        Some(project_name)
                    }
                },
                source_tool: r.get(3),
                role: r.get(4),
                snippet: r.get(5),
                created_at,
                score: blended_score(0.6, "memory", created_at),
                kind: "memory",
            }
        }));
    }
    if source == SearchSource::All {
        hits.extend(search(client, actor, query, limit, None)?);
        // Project instruction files (AGENTS.md, CLAUDE.md, …) live in prompts.
        let rows = client.query(
            r#"
            SELECT id, name, category, left(content, 240), updated_at
            FROM prompts
            WHERE content ILIKE $1 OR name ILIKE $1
            ORDER BY updated_at DESC
            LIMIT $2::bigint
            "#,
            &[&like, &(limit as i64)],
        )?;
        hits.extend(rows.into_iter().map(|r| {
            let created_at: DateTime<Utc> = r.get(4);
            SearchHit {
                message_id: Some(r.get(0)),
                conversation_id: 0,
                session_id: uuid::Uuid::nil(),
                project_name: Some(r.get(1)),
                source_tool: Some(r.get(2)),
                role: "instruction".into(),
                snippet: r.get(3),
                created_at,
                score: blended_score(0.5, "instruction", created_at),
                kind: "instruction",
            }
        }));
        if actor.sees_assistant_configs() {
            if let Ok(rows) = client.query(
                r#"
                SELECT id, name, kind, left(content, 240), imported_at
                FROM assistant_configs
                WHERE present AND (content ILIKE $1 OR name ILIKE $1 OR path ILIKE $1)
                ORDER BY imported_at DESC
                LIMIT $2::bigint
                "#,
                &[&like, &(limit as i64)],
            ) {
                hits.extend(rows.into_iter().map(|r| SearchHit {
                    message_id: Some(r.get(0)),
                    conversation_id: 0,
                    session_id: uuid::Uuid::nil(),
                    project_name: Some(r.get(1)),
                    source_tool: Some(r.get(2)),
                    role: "config".into(),
                    snippet: r.get(3),
                    created_at: r.get(4),
                    score: 0.85,
                    kind: "config",
                }));
            }
        }
    }
    // Sort by blended score (memory > instructions > messages), then recency.
    hits.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| b.created_at.cmp(&a.created_at))
    });
    hits.truncate(limit.max(0) as usize);
    Ok(hits)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kind_bonus_memory_beats_lexical() {
        assert!(kind_bonus("memory") > kind_bonus("lexical"));
        assert!(kind_bonus("instruction") > kind_bonus("lexical"));
        assert!(kind_bonus("semantic") > kind_bonus("lexical"));
    }

    #[test]
    fn recency_bonus_fresh_beats_old() {
        let now = Utc::now();
        let old = now - chrono::Duration::hours(500);
        assert!(recency_bonus(now) > recency_bonus(old));
        assert!(recency_bonus(now) >= 0.10);
        assert!(recency_bonus(old) <= 0.0);
    }

    #[test]
    fn blended_score_memory_is_high() {
        let now = Utc::now();
        let s = blended_score(0.5, "memory", now);
        // base 0.5 + kind 0.25 + recency 0.15
        assert!(s >= 0.85, "got {s}");
    }

    #[test]
    fn blended_score_old_message_is_low() {
        let old = Utc::now() - chrono::Duration::hours(500);
        let s = blended_score(0.5, "lexical", old);
        // base 0.5 + kind 0.0 + recency 0.0
        assert!(s <= 0.55, "got {s}");
    }

    #[test]
    fn search_source_parse() {
        assert_eq!(SearchSource::parse("memory"), SearchSource::Memory);
        assert_eq!(SearchSource::parse("messages"), SearchSource::Messages);
        assert_eq!(SearchSource::parse("all"), SearchSource::All);
        assert_eq!(SearchSource::parse("anything"), SearchSource::All);
    }

    #[test]
    fn search_hides() {
        use crate::access::team_test::{assert_hidden, note, two_accounts};
        let Some(mut client) = crate::config::DbConfig::from_env().connect().ok() else {
            eprintln!("skipping search_hides: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let (a, b) = two_accounts(&mut tx);
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let secret = format!("secret-{tag}");
        let private = note(
            &mut tx,
            a.user_id,
            "private",
            &format!("plan {secret}"),
            "insight",
            None,
        );
        let shared = note(
            &mut tx,
            None,
            "shared",
            &format!("plan shared-{tag}"),
            "insight",
            None,
        );
        let texts = |hits: &[SearchHit]| -> String {
            hits.iter()
                .map(|h| format!("{} {:?} {}", h.kind, h.message_id, h.snippet))
                .collect::<Vec<_>>()
                .join("\n")
        };
        let ids = |hits: &[SearchHit]| -> Vec<i64> {
            hits.iter()
                .filter(|h| h.kind == "memory")
                .filter_map(|h| h.message_id)
                .collect()
        };
        let mine = memory_hits(&mut tx, &a, &tag, 50).expect("memory hits");
        assert!(ids(&mine).contains(&private), "A finds her note");
        for (what, hits) in [
            (
                "memory_hits",
                memory_hits(&mut tx, &b, &tag, 50).expect("memory hits"),
            ),
            (
                "hybrid",
                hybrid(&mut tx, &b, None, &tag, 50).expect("hybrid"),
            ),
            (
                "search_from",
                search_from(&mut tx, &b, &tag, 50, SearchSource::All).expect("search_from"),
            ),
        ] {
            assert!(!ids(&hits).contains(&private), "{what}: {}", texts(&hits));
            assert_hidden(&texts(&hits), &secret, what);
            assert!(ids(&hits).contains(&shared), "{what} finds the shared note");
        }
    }
}
