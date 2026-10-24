//! HTTP API + embedded web UI.
//!
//! Loopback-only by default: the API has no authentication and serves the
//! whole corpus. Same posture as the Python server.

use crate::search;
use anyhow::{Context, Result};
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::{Html, IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};

pub type Db = Arc<Mutex<postgres::Client>>;

#[derive(Clone)]
pub struct AppState {
    pub db: Db,
}

#[derive(Serialize)]
struct Health {
    status: &'static str,
    version: &'static str,
}

#[derive(Serialize)]
struct StatusBody {
    conversations: i64,
    messages: i64,
    memory_chunks: i64,
    embeddings: i64,
    projects: i64,
    first_started_at: Option<chrono::DateTime<chrono::Utc>>,
    last_started_at: Option<chrono::DateTime<chrono::Utc>>,
    tools: Vec<ToolCount>,
}

#[derive(Serialize)]
struct ToolCount {
    source_tool: Option<String>,
    conversations: i64,
    messages: i64,
}

#[derive(Serialize)]
struct SearchResponse {
    query: String,
    mode: String,
    total: usize,
    hits: Vec<SearchHitJson>,
    semantic_backend: String,
}

#[derive(Serialize)]
struct SearchHitJson {
    message_id: Option<i64>,
    conversation_id: i64,
    session_id: uuid::Uuid,
    project_name: Option<String>,
    source_tool: Option<String>,
    role: String,
    snippet: String,
    created_at: chrono::DateTime<chrono::Utc>,
    score: f32,
    kind: String,
}

#[derive(Serialize)]
struct ConversationJson {
    id: i64,
    session_id: uuid::Uuid,
    project_name: Option<String>,
    project_path: Option<String>,
    source_tool: Option<String>,
    model: Option<String>,
    started_at: chrono::DateTime<chrono::Utc>,
    ended_at: Option<chrono::DateTime<chrono::Utc>>,
    message_count: i32,
    messages: Vec<MessageJson>,
}

#[derive(Serialize)]
struct MessageJson {
    id: i64,
    role: String,
    content: String,
    model: Option<String>,
    created_at: chrono::DateTime<chrono::Utc>,
}

#[derive(Serialize)]
struct ConversationSummary {
    id: i64,
    session_id: uuid::Uuid,
    project_name: Option<String>,
    source_tool: Option<String>,
    model: Option<String>,
    started_at: chrono::DateTime<chrono::Utc>,
    ended_at: Option<chrono::DateTime<chrono::Utc>>,
    message_count: i32,
    preview: String,
}

fn api_error(status: StatusCode, code: &str, detail: impl Into<String>) -> Response {
    (
        status,
        Json(serde_json::json!({
            "error": code,
            "detail": detail.into(),
        })),
    )
        .into_response()
}

/// Run blocking `postgres` work off the async runtime.
///
/// The sync `postgres` crate drives its connection with `block_on`. Calling
/// that from a Tokio worker panics ("Cannot start a runtime from within a
/// runtime") and poisons the shared client. `spawn_blocking` runs the closure
/// on the blocking pool, where `block_on` is legal.
async fn with_db<T, F>(state: &AppState, f: F) -> Result<T, Response>
where
    T: Send + 'static,
    F: FnOnce(&mut postgres::Client) -> anyhow::Result<T> + Send + 'static,
{
    let db = state.db.clone();
    let joined = tokio::task::spawn_blocking(move || {
        let mut client = db
            .lock()
            .map_err(|_| anyhow::anyhow!("database lock poisoned"))?;
        f(&mut client)
    })
    .await;
    match joined {
        Ok(Ok(value)) => Ok(value),
        Ok(Err(e)) => {
            let msg = e.to_string();
            if msg.contains("lock poisoned") {
                Err(api_error(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "lock_poisoned",
                    msg,
                ))
            } else {
                Err(api_error(StatusCode::INTERNAL_SERVER_ERROR, "query_failed", msg))
            }
        }
        Err(join) => Err(api_error(
            StatusCode::INTERNAL_SERVER_ERROR,
            "blocking_task_failed",
            join.to_string(),
        )),
    }
}

async fn health(State(state): State<AppState>) -> Response {
    let result = with_db(&state, |client| {
        client.batch_execute("SELECT 1")?;
        Ok(())
    })
    .await;
    match result {
        Ok(()) => Json(Health {
            status: "ok",
            version: env!("CARGO_PKG_VERSION"),
        })
        .into_response(),
        Err(mut resp) => {
            // Distinguish "DB down" from internal errors for the health pill.
            *resp.status_mut() = StatusCode::SERVICE_UNAVAILABLE;
            resp
        }
    }
}

async fn status(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| {
        let conversations = search::count_conversations(client)?;
        let messages = search::count_messages(client)?;
        let memory_chunks: i64 = client
            .query_one("SELECT COUNT(*) FROM memory_chunks", &[])?
            .get(0);
        let embeddings: i64 = client
            .query_one("SELECT COUNT(*) FROM embeddings", &[])?
            .get(0);
        let projects: i64 = client
            .query_one("SELECT COUNT(*) FROM projects", &[])?
            .get(0);
        let range = client.query_one(
            "SELECT MIN(started_at), MAX(started_at) FROM conversations",
            &[],
        )?;
        let first_started_at: Option<chrono::DateTime<chrono::Utc>> = range.get(0);
        let last_started_at: Option<chrono::DateTime<chrono::Utc>> = range.get(1);
        let tool_rows = client.query(
            r#"
            SELECT c.source_tool,
                   COUNT(*)::bigint AS conversations,
                   COALESCE(SUM(c.message_count), 0)::bigint AS messages
            FROM conversations c
            GROUP BY 1
            ORDER BY 2 DESC
            "#,
            &[],
        )?;
        let tools = tool_rows
            .into_iter()
            .map(|r| ToolCount {
                source_tool: r.get(0),
                conversations: r.get(1),
                messages: r.get(2),
            })
            .collect();
        Ok(StatusBody {
            conversations,
            messages,
            memory_chunks,
            embeddings,
            projects,
            first_started_at,
            last_started_at,
            tools,
        })
    })
    .await
    {
        Ok(body) => Json(body).into_response(),
        Err(resp) => resp,
    }
}

#[derive(serde::Deserialize)]
struct SearchParams {
    q: Option<String>,
    #[serde(default = "default_limit")]
    limit: i32,
    /// `hybrid` (default) | `semantic` | `lexical`
    mode: Option<String>,
}

fn default_limit() -> i32 {
    20
}

async fn search_handler(State(state): State<AppState>, Query(params): Query<SearchParams>) -> Response {
    let query = params.q.unwrap_or_default();
    let mode = params
        .mode
        .unwrap_or_else(|| "hybrid".into())
        .to_ascii_lowercase();
    if query.trim().is_empty() {
        return Json(SearchResponse {
            query,
            mode,
            total: 0,
            hits: vec![],
            semantic_backend: String::new(),
        })
        .into_response();
    }
    let limit = params.limit.clamp(1, 200);
    let q = query.clone();
    let m = mode.clone();
    match with_db(&state, move |client| {
        let backend = crate::embedding::resolve().ok();
        let backend_label = backend
            .as_ref()
            .map(crate::embedding::label)
            .unwrap_or_else(|| "unavailable".into());
        let hits = match m.as_str() {
            "semantic" => match backend.as_ref() {
                Some(b) => crate::search::semantic(client, b, &q, limit)?,
                None => crate::search::search(client, &q, limit)?,
            },
            "lexical" => crate::search::search(client, &q, limit)?,
            _ => crate::search::hybrid(client, backend.as_ref(), &q, limit)?,
        };
        Ok((hits, backend_label, m))
    })
    .await
    {
        Ok((hits, backend_label, mode)) => {
            let total = hits.len();
            let hits = hits
                .into_iter()
                .map(|h| SearchHitJson {
                    message_id: h.message_id,
                    conversation_id: h.conversation_id,
                    session_id: h.session_id,
                    project_name: h.project_name,
                    source_tool: h.source_tool,
                    role: h.role,
                    snippet: h.snippet,
                    created_at: h.created_at,
                    score: h.score,
                    kind: h.kind.to_string(),
                })
                .collect();
            Json(SearchResponse {
                query,
                mode,
                total,
                hits,
                semantic_backend: backend_label,
            })
            .into_response()
        }
        Err(resp) => resp,
    }
}

async fn conversation(State(state): State<AppState>, Path(id): Path<i64>) -> Response {
    let result = with_db(&state, move |client| {
        let conv_rows = client.query(
            r#"
            SELECT id, session_id, project_name, project_path, source_tool, model,
                   started_at, ended_at, message_count
            FROM conversations WHERE id = $1
            "#,
            &[&id],
        )?;
        let Some(conv_row) = conv_rows.into_iter().next() else {
            return Ok(None);
        };
        let msgs = client.query(
            r#"
            SELECT id, role::text, COALESCE(content, ''), model, created_at
            FROM messages
            WHERE conversation_id = $1
            ORDER BY created_at ASC, id ASC
            "#,
            &[&id],
        )?;
        let messages: Vec<MessageJson> = msgs
            .into_iter()
            .map(|r| MessageJson {
                id: r.get(0),
                role: r.get(1),
                content: r.get(2),
                model: r.get(3),
                created_at: r.get(4),
            })
            .collect();
        Ok(Some(ConversationJson {
            id: conv_row.get(0),
            session_id: conv_row.get(1),
            project_name: conv_row.get(2),
            project_path: conv_row.get(3),
            source_tool: conv_row.get(4),
            model: conv_row.get(5),
            started_at: conv_row.get(6),
            ended_at: conv_row.get(7),
            message_count: conv_row.get(8),
            messages,
        }))
    })
    .await;

    match result {
        Ok(Some(body)) => Json(body).into_response(),
        Ok(None) => api_error(StatusCode::NOT_FOUND, "not_found", "conversation not found"),
        Err(resp) => resp,
    }
}

#[derive(serde::Deserialize)]
struct RecentParams {
    #[serde(default = "default_page_limit")]
    limit: i32,
    #[serde(default)]
    offset: i32,
    /// Filter by ingest tool (claude_code, codex, cursor, …).
    tool: Option<String>,
    /// Filter by project name (exact).
    project: Option<String>,
    /// Substring filter on preview / first message / session id.
    q: Option<String>,
    /// Include generated rows too (default: ingest history only).
    /// Accepts `all=1` / `true` / `yes`.
    #[serde(default, deserialize_with = "de_flag")]
    all: bool,
    /// Hide conversations with no real message text.
    #[serde(default, deserialize_with = "de_flag")]
    hide_empty: bool,
    /// `recent` (default) | `old` | `messages`
    /// Also accepts column sort: `id`, `started_at`, `ended_at`, `message_count`,
    /// `model`, `project`, `tool` — append `:asc` / `:desc` (default desc).
    sort: Option<String>,
    /// Inclusive start date `YYYY-MM-DD` on started_at.
    from: Option<String>,
    /// Inclusive end date `YYYY-MM-DD` on started_at.
    to: Option<String>,
    /// When true, return this page as text/csv instead of JSON.
    #[serde(default, deserialize_with = "de_flag")]
    csv: bool,
}

fn de_flag<'de, D>(d: D) -> Result<bool, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let s = String::deserialize(d)?;
    Ok(matches!(
        s.trim().to_ascii_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    ))
}

fn default_page_limit() -> i32 {
    50
}

#[derive(Serialize)]
struct ConversationsPage {
    conversations: Vec<ConversationSummary>,
    total: i64,
    limit: i32,
    offset: i32,
}

async fn recent(State(state): State<AppState>, Query(params): Query<RecentParams>) -> Response {
    let limit = if params.csv {
        params.limit.clamp(1, 50_000)
    } else {
        params.limit.clamp(1, 200)
    };
    let offset = params.offset.max(0);
    let tool = params.tool.filter(|t| !t.trim().is_empty());
    let project = params.project.filter(|p| !p.trim().is_empty());
    let q = params.q.filter(|s| !s.trim().is_empty());
    let include_generated = params.all;
    let hide_empty = params.hide_empty;
    let sort = params.sort.unwrap_or_default();
    let q_pat = q.map(|s| format!("%{s}%"));
    let from = params.from.filter(|s| !s.trim().is_empty());
    let to = params.to.filter(|s| !s.trim().is_empty());
    let want_csv = params.csv;

    match with_db(
        &state,
        move |client| {
            // Fixed parameter slots keep rust-postgres happy (no dynamic ToSql).
            // $1 include_generated  $2 tool  $3 project  $4 q_pat
            // $5 hide_empty         $6 from  $7 to
            // $8 limit              $9 offset
            let empty_pred = r#"
                  ($5::bool = false OR (
                    EXISTS (
                      SELECT 1 FROM messages m
                      WHERE m.conversation_id = c.id
                        AND COALESCE(m.content,'') <> ''
                    )
                  ))
            "#;
            let date_pred = r#"
                  AND ($6::text IS NULL OR c.started_at::date >= $6::text::date)
                  AND ($7::text IS NULL OR c.started_at::date <= $7::text::date)
            "#;
            let order_sql = {
                let raw = sort.as_str();
                let (col, dir) = match raw.rsplit_once(':') {
                    Some((c, d)) => (
                        c,
                        if d.eq_ignore_ascii_case("asc") {
                            "ASC"
                        } else {
                            "DESC"
                        },
                    ),
                    None => match raw {
                        "old" => ("started_at", "ASC"),
                        "recent" => ("started_at", "DESC"),
                        "messages" => ("message_count", "DESC"),
                        "id" => ("id", "DESC"),
                        "ended_at" => ("ended_at", "DESC"),
                        "model" => ("model", "DESC"),
                        "project" => ("project_name", "DESC"),
                        "tool" => ("source_tool", "DESC"),
                        "message_count" => ("message_count", "DESC"),
                        "started_at" => ("started_at", "DESC"),
                        _ => ("started_at", "DESC"),
                    },
                };
                let column = match col {
                    "id" => "c.id",
                    "started_at" | "recent" | "old" => "c.started_at",
                    "ended_at" => "c.ended_at",
                    "message_count" | "messages" => "c.message_count",
                    "model" => "c.model",
                    "project" | "project_name" => "c.project_name",
                    "tool" | "source_tool" => "c.source_tool",
                    _ => "c.started_at",
                };
                let nulls = if dir == "ASC" {
                    "NULLS FIRST"
                } else {
                    "NULLS LAST"
                };
                format!("{column} {dir} {nulls}, c.id {dir}")
            };
            let count_sql = format!(
                r#"
                SELECT COUNT(*)::bigint
                FROM conversations c
                WHERE ($1::bool OR c.generated_by IS NULL)
                  AND ($2::text IS NULL OR c.source_tool = $2)
                  AND ($3::text IS NULL OR c.project_name = $3)
                  AND ($4::text IS NULL OR (
                        c.session_id::text ILIKE $4
                        OR COALESCE(c.project_name,'') ILIKE $4
                        OR COALESCE(c.summary,'') ILIKE $4
                        OR EXISTS (
                          SELECT 1 FROM messages m
                          WHERE m.conversation_id = c.id
                            AND COALESCE(m.content,'') ILIKE $4
                        )
                  ))
                  AND {empty_pred}
                  {date_pred}
                "#
            );
            let total: i64 = client
                .query_one(
                    &count_sql,
                    &[
                        &include_generated,
                        &tool,
                        &project,
                        &q_pat,
                        &hide_empty,
                        &from,
                        &to,
                    ],
                )?
                .get(0);

            let list_sql = format!(
                r#"
                SELECT c.id, c.session_id, c.project_name, c.source_tool, c.model,
                       c.started_at, c.ended_at, c.message_count,
                       left(COALESCE(
                           (SELECT content FROM messages m
                            WHERE m.conversation_id = c.id AND m.role = 'user'
                              AND COALESCE(m.content,'') <> ''
                            ORDER BY m.created_at ASC LIMIT 1),
                           (SELECT content FROM messages m
                            WHERE m.conversation_id = c.id
                              AND COALESCE(m.content,'') <> ''
                            ORDER BY m.created_at ASC LIMIT 1),
                           c.summary, ''), 240)
                FROM conversations c
                WHERE ($1::bool OR c.generated_by IS NULL)
                  AND ($2::text IS NULL OR c.source_tool = $2)
                  AND ($3::text IS NULL OR c.project_name = $3)
                  AND ($4::text IS NULL OR (
                        c.session_id::text ILIKE $4
                        OR COALESCE(c.project_name,'') ILIKE $4
                        OR COALESCE(c.summary,'') ILIKE $4
                        OR EXISTS (
                          SELECT 1 FROM messages m
                          WHERE m.conversation_id = c.id
                            AND COALESCE(m.content,'') ILIKE $4
                        )
                  ))
                  AND {empty_pred}
                  {date_pred}
                ORDER BY {order_sql}
                LIMIT $8 OFFSET $9
                "#
            );
            let rows = client.query(
                &list_sql,
                &[
                    &include_generated,
                    &tool,
                    &project,
                    &q_pat,
                    &hide_empty,
                    &from,
                    &to,
                    &(limit as i64),
                    &(offset as i64),
                ],
            )?;
            let conversations: Vec<ConversationSummary> = rows
                .into_iter()
                .map(|r| ConversationSummary {
                    id: r.get(0),
                    session_id: r.get(1),
                    project_name: r.get(2),
                    source_tool: r.get(3),
                    model: r.get(4),
                    started_at: r.get(5),
                    ended_at: r.get(6),
                    message_count: r.get(7),
                    preview: r.get(8),
                })
                .collect();
            Ok(ConversationsPage {
                conversations,
                total,
                limit,
                offset,
            })
        },
    )
    .await
    {
        Ok(page) => {
            if want_csv {
                return conversations_csv_response(&page);
            }
            Json(page).into_response()
        }
        Err(resp) => resp,
    }
}

fn csv_escape(s: &str) -> String {
    if s.contains(',') || s.contains('"') || s.contains('\n') || s.contains('\r') {
        format!("\"{}\"", s.replace('"', "\"\""))
    } else {
        s.to_string()
    }
}

fn conversations_csv_response(page: &ConversationsPage) -> Response {
    let mut out = String::from(
        "id,session_id,project_name,source_tool,model,started_at,ended_at,message_count,duration_s,preview\n",
    );
    for c in &page.conversations {
        let duration = match (c.ended_at, c.started_at) {
            (Some(e), s) => {
                let secs = (e - s).num_seconds();
                if secs >= 0 {
                    secs.to_string()
                } else {
                    String::new()
                }
            }
            _ => String::new(),
        };
        out.push_str(&format!(
            "{},{},{},{},{},{},{},{},{},{}\n",
            c.id,
            c.session_id,
            csv_escape(c.project_name.as_deref().unwrap_or("")),
            csv_escape(c.source_tool.as_deref().unwrap_or("")),
            csv_escape(c.model.as_deref().unwrap_or("")),
            c.started_at.to_rfc3339(),
            c.ended_at
                .map(|e| e.to_rfc3339())
                .unwrap_or_default(),
            c.message_count,
            duration,
            csv_escape(&c.preview.replace('\n', " ").replace('\r', " ")),
        ));
    }
    (
        [
            (axum::http::header::CONTENT_TYPE, "text/csv; charset=utf-8"),
            (
                axum::http::header::CONTENT_DISPOSITION,
                "attachment; filename=\"throughline-sessions.csv\"",
            ),
        ],
        out,
    )
        .into_response()
}

const INDEX_HTML: &str = include_str!("../web/index.html");

// ─── Full DB browser: messages + tool calls ────────────────────────────────

#[derive(Serialize)]
struct MessageRowJson {
    id: i64,
    conversation_id: i64,
    session_id: Option<uuid::Uuid>,
    project_name: Option<String>,
    source_tool: Option<String>,
    role: String,
    tool_name: Option<String>,
    preview: String,
    content_len: i32,
    has_tool_calls: bool,
    tool_calls_preview: Option<String>,
    token_count: Option<i64>,
    model: Option<String>,
    duration_ms: Option<i32>,
    created_at: chrono::DateTime<chrono::Utc>,
}

#[derive(Serialize)]
struct MessagePage {
    messages: Vec<MessageRowJson>,
    total: i64,
    limit: i32,
    offset: i32,
}

#[derive(Deserialize)]
struct MessageListParams {
    #[serde(default = "default_page_limit")]
    limit: i32,
    #[serde(default)]
    offset: i32,
    /// Substring match on content / tool_name / session id.
    q: Option<String>,
    /// `user` | `assistant` | `tool_result` | `system` | …
    role: Option<String>,
    /// Exact tool_name (Bash, Read, exec, …).
    tool: Option<String>,
    conversation_id: Option<i64>,
    /// Only rows that carry a tool_calls array.
    #[serde(default, deserialize_with = "de_flag")]
    tool_calls_only: bool,
}

async fn messages_handler(
    State(state): State<AppState>,
    Query(params): Query<MessageListParams>,
) -> Response {
    let limit = params.limit.clamp(1, 200);
    let offset = params.offset.max(0);
    let q = params
        .q
        .as_deref()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .map(|s| format!("%{}%", s.replace('%', "\\%").replace('_', "\\_")));
    let role = params.role.filter(|r| !r.trim().is_empty());
    let tool = params.tool.filter(|t| !t.trim().is_empty());
    let conv = params.conversation_id;
    let tool_only = params.tool_calls_only;
    let q_pat = q;

    match with_db(
        &state,
        move |client| {
            // $1 q  $2 role  $3 tool  $4 conversation_id  $5 tool_calls_only
            // $6 limit  $7 offset
            let where_sql = r#"
                WHERE ($1::text IS NULL OR m.content ILIKE $1
                       OR COALESCE(m.tool_name,'') ILIKE $1
                       OR c.session_id::text ILIKE $1
                       OR COALESCE(c.project_name,'') ILIKE $1)
                  AND ($2::text IS NULL OR m.role::text = $2)
                  AND ($3::text IS NULL OR m.tool_name = $3)
                  AND ($4::bigint IS NULL OR m.conversation_id = $4)
                  AND (
                    $5::bool = false OR (
                      m.tool_calls IS NOT NULL
                      AND m.tool_calls::text NOT IN ('null','[]','{}','""')
                    )
                  )
            "#;
            let count_sql = format!(
                "SELECT COUNT(*)::bigint FROM messages m
                 JOIN conversations c ON c.id = m.conversation_id
                 {where_sql}"
            );
            let total: i64 = client
                .query_one(
                    &count_sql,
                    &[&q_pat, &role, &tool, &conv, &tool_only],
                )?
                .get(0);

            let list_sql = format!(
                r#"
                SELECT m.id,
                       m.conversation_id,
                       c.session_id,
                       c.project_name,
                       c.source_tool,
                       m.role::text,
                       m.tool_name,
                       left(COALESCE(m.content, ''), 200),
                       length(COALESCE(m.content, ''))::int,
                       CASE
                         WHEN m.tool_calls IS NULL THEN false
                         WHEN m.tool_calls::text IN ('null','[]','{{}}','""') THEN false
                         ELSE true
                       END,
                       left(COALESCE(m.tool_calls::text, ''), 120),
                       m.token_count,
                       m.model,
                       m.duration_ms,
                       m.created_at
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                {where_sql}
                ORDER BY m.created_at DESC, m.id DESC
                LIMIT $6 OFFSET $7
                "#
            );
            let rows = client.query(
                &list_sql,
                &[
                    &q_pat,
                    &role,
                    &tool,
                    &conv,
                    &tool_only,
                    &(limit as i64),
                    &(offset as i64),
                ],
            )?;
            let messages: Vec<MessageRowJson> = rows
                .into_iter()
                .map(|r| MessageRowJson {
                    id: r.get(0),
                    conversation_id: r.get(1),
                    session_id: r.get(2),
                    project_name: r.get(3),
                    source_tool: r.get(4),
                    role: r.get(5),
                    tool_name: r.get(6),
                    preview: r.get(7),
                    content_len: r.get(8),
                    has_tool_calls: r.get(9),
                    tool_calls_preview: r.get(10),
                    token_count: r.get(11),
                    model: r.get(12),
                    duration_ms: r.get(13),
                    created_at: r.get(14),
                })
                .collect();
            Ok(MessagePage {
                messages,
                total,
                limit,
                offset,
            })
        },
    )
    .await
    {
        Ok(page) => Json(page).into_response(),
        Err(resp) => resp,
    }
}

#[derive(Serialize)]
struct MessageDetailJson {
    id: i64,
    conversation_id: i64,
    session_id: Option<uuid::Uuid>,
    project_name: Option<String>,
    role: String,
    tool_name: Option<String>,
    content: String,
    content_blocks: Option<serde_json::Value>,
    tool_calls: Option<serde_json::Value>,
    metadata: Option<serde_json::Value>,
    token_count: Option<i64>,
    model: Option<String>,
    duration_ms: Option<i32>,
    created_at: chrono::DateTime<chrono::Utc>,
}

async fn message_detail(State(state): State<AppState>, Path(id): Path<i64>) -> Response {
    let result = with_db(&state, move |client| {
        let rows = client.query(
            r#"
            SELECT m.id, m.conversation_id, c.session_id, c.project_name,
                   m.role::text, m.tool_name,
                   COALESCE(m.content, ''),
                   m.content_blocks, m.tool_calls, m.metadata,
                   m.token_count, m.model, m.duration_ms, m.created_at
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.id = $1
            "#,
            &[&id],
        )?;
        let Some(r) = rows.into_iter().next() else {
            return Ok(None);
        };
        Ok(Some(MessageDetailJson {
            id: r.get(0),
            conversation_id: r.get(1),
            session_id: r.get(2),
            project_name: r.get(3),
            role: r.get(4),
            tool_name: r.get(5),
            content: r.get(6),
            content_blocks: r.get(7),
            tool_calls: r.get(8),
            metadata: r.get(9),
            token_count: r.get(10),
            model: r.get(11),
            duration_ms: r.get(12),
            created_at: r.get(13),
        }))
    })
    .await;
    match result {
        Ok(Some(body)) => Json(body).into_response(),
        Ok(None) => api_error(StatusCode::NOT_FOUND, "not_found", "message not found"),
        Err(resp) => resp,
    }
}

#[derive(Serialize)]
struct ToolFacet {
    tool_name: String,
    count: i64,
}

async fn tools_handler(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| {
        let rows = client.query(
            r#"
            SELECT COALESCE(tool_name, '(none)'), COUNT(*)::bigint
            FROM messages
            GROUP BY 1
            ORDER BY 2 DESC
            LIMIT 80
            "#,
            &[],
        )?;
        Ok(rows
            .into_iter()
            .map(|r| ToolFacet {
                tool_name: r.get(0),
                count: r.get(1),
            })
            .collect::<Vec<_>>())
    })
    .await
    {
        Ok(items) => Json(serde_json::json!({ "tools": items })).into_response(),
        Err(resp) => resp,
    }
}

#[derive(Deserialize)]
struct MemoryParams {
    q: Option<String>,
    category: Option<String>,
    #[serde(default = "default_page_limit")]
    limit: i32,
    #[serde(default)]
    offset: i32,
}

async fn memory_handler(State(state): State<AppState>, Query(params): Query<MemoryParams>) -> Response {
    let limit = params.limit.clamp(1, 200);
    let offset = params.offset.max(0);
    let q = params.q;
    let cat = params.category.filter(|c| !c.trim().is_empty());
    match with_db(&state, move |client| {
        crate::agent_memory::list_memory(client, q, cat, limit, offset)
    })
    .await
    {
        Ok((total, items)) => {
            let chunks: Vec<_> = items
                .into_iter()
                .map(|(id, content, category, project_name, created_at)| {
                    serde_json::json!({
                        "id": id,
                        "content": content,
                        "category": category,
                        "project_name": project_name,
                        "created_at": created_at,
                    })
                })
                .collect();
            Json(serde_json::json!({
                "chunks": chunks,
                "total": total,
                "limit": limit,
                "offset": offset,
            }))
            .into_response()
        }
        Err(resp) => resp,
    }
}

async fn memory_detail(State(state): State<AppState>, Path(id): Path<i64>) -> Response {
    match with_db(&state, move |client| crate::agent_memory::get_memory(client, id)).await {
        Ok(Some((content, category, project_name, created_at))) => Json(serde_json::json!({
            "id": id,
            "content": content,
            "category": category,
            "project_name": project_name,
            "created_at": created_at,
        }))
        .into_response(),
        Ok(None) => api_error(StatusCode::NOT_FOUND, "not_found", "memory chunk not found"),
        Err(resp) => resp,
    }
}

// ─── UI memory write path: pin / keep / forget ─────────────────────────────

/// Categories the UI may assign. Matches the Agent Memory picklist and the
/// extraction prompt (without `contact`, which the UI does not offer).
pub const MEMORY_CATEGORIES: &[&str] = &[
    "decision",
    "preference",
    "pattern",
    "insight",
    "error_solution",
    "project_context",
    "workflow",
];

/// Shared validation for `POST /api/memory`. Pure so unit tests can cover
/// the 422 cases without a database.
fn validate_memory_create(
    content: &str,
    category: &str,
) -> std::result::Result<(), (StatusCode, &'static str, String)> {
    if content.trim().is_empty() {
        return Err((
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            "content is required".into(),
        ));
    }
    if !MEMORY_CATEGORIES.contains(&category) {
        return Err((
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            format!("unknown category: {category}"),
        ));
    }
    Ok(())
}

/// Map an UPDATE row count to the keep/forget response. `rows == 0` is a
/// missing id and becomes 404.
fn memory_status_response(id: i64, rows: u64, status: &str) -> Response {
    if rows == 0 {
        api_error(
            StatusCode::NOT_FOUND,
            "not_found",
            "memory chunk not found",
        )
    } else {
        Json(serde_json::json!({ "id": id, "status": status })).into_response()
    }
}

#[derive(Deserialize)]
struct MemoryCreate {
    #[serde(default)]
    content: String,
    #[serde(default)]
    category: String,
    project_name: Option<String>,
}

async fn memory_create(State(state): State<AppState>, Json(body): Json<MemoryCreate>) -> Response {
    if let Err((status, code, detail)) = validate_memory_create(&body.content, &body.category) {
        return api_error(status, code, detail);
    }
    let content = body.content.trim().to_string();
    let category = body.category.trim().to_string();
    let project = body
        .project_name
        .map(|p| p.trim().to_string())
        .filter(|p| !p.is_empty());
    match with_db(&state, move |client| {
        let row = client.query_one(
            r#"
            INSERT INTO memory_chunks (source_type, content, category, project_name, confidence, status)
            VALUES ('ui_pin', $1, $2, $3, 1.0, 'active')
            RETURNING id
            "#,
            &[&content, &category, &project],
        )?;
        Ok::<i64, anyhow::Error>(row.get(0))
    })
    .await
    {
        Ok(id) => Json(serde_json::json!({ "id": id })).into_response(),
        Err(resp) => resp,
    }
}

async fn memory_forget(State(state): State<AppState>, Path(id): Path<i64>) -> Response {
    match with_db(&state, move |client| {
        Ok(client.execute(
            "UPDATE memory_chunks SET status = 'forgotten', updated_at = now() WHERE id = $1",
            &[&id],
        )?)
    })
    .await
    {
        Ok(rows) => memory_status_response(id, rows, "forgotten"),
        Err(resp) => resp,
    }
}

async fn memory_keep(State(state): State<AppState>, Path(id): Path<i64>) -> Response {
    match with_db(&state, move |client| {
        // Confirm the note: restore to active (undo of forget) and stamp
        // reviewed via updated_at (M2 column) plus a durable tag.
        Ok(client.execute(
            r#"
            UPDATE memory_chunks
            SET status = 'active',
                updated_at = now(),
                tags = CASE
                    WHEN 'reviewed' = ANY(COALESCE(tags, '{}')) THEN tags
                    ELSE array_append(COALESCE(tags, '{}'), 'reviewed')
                END
            WHERE id = $1
            "#,
            &[&id],
        )?)
    })
    .await
    {
        Ok(rows) => memory_status_response(id, rows, "active"),
        Err(resp) => resp,
    }
}

async fn agent_memory_ingest(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| crate::agent_memory::ingest_agent_memory(client)).await {
        Ok((files, chunks)) => Json(serde_json::json!({
            "files_scanned": files,
            "chunks_upserted": chunks,
        }))
        .into_response(),
        Err(resp) => resp,
    }
}

async fn index() -> Html<&'static str> {
    Html(INDEX_HTML)
}

const FAVICON_SVG: &str = include_str!("../web/favicon.svg");

async fn favicon() -> Response {
    (
        [(axum::http::header::CONTENT_TYPE, "image/svg+xml")],
        FAVICON_SVG,
    )
        .into_response()
}

async fn conflicts_handler(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| {
        crate::conflicts::find_conflicts(client, None, 0.85)
    })
    .await
    {
        Ok(report) => Json(report).into_response(),
        Err(resp) => resp,
    }
}

async fn projects_handler(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| {
        let rows = client.query(
            r#"
            SELECT p.name,
                   COALESCE(p.description, ''),
                   (SELECT COUNT(*) FROM conversations c WHERE c.project_name = p.name),
                   p.status::text
            FROM projects p
            ORDER BY p.name
            "#,
            &[],
        )?;
        Ok(rows
            .into_iter()
            .map(|r| {
                serde_json::json!({
                    "name": r.get::<_, String>(0),
                    "description": r.get::<_, String>(1),
                    "conversations": r.get::<_, i64>(2),
                    "status": r.get::<_, Option<String>>(3),
                })
            })
            .collect::<Vec<_>>())
    })
    .await
    {
        Ok(items) => Json(serde_json::json!({ "projects": items })).into_response(),
        Err(resp) => resp,
    }
}

async fn timeline_handler(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| {
        let rows = client.query(
            r#"
            SELECT date_trunc('day', started_at)::date AS day,
                   source_tool,
                   COUNT(*)
            FROM conversations
            WHERE generated_by IS NULL
              AND started_at > now() - interval '90 days'
            GROUP BY 1, 2
            ORDER BY 1 DESC
            LIMIT 200
            "#,
            &[],
        )?;
        Ok(rows
            .into_iter()
            .map(|r| {
                serde_json::json!({
                    "day": r.get::<_, chrono::NaiveDate>(0).to_string(),
                    "source_tool": r.get::<_, Option<String>>(1),
                    "count": r.get::<_, i64>(2),
                })
            })
            .collect::<Vec<_>>())
    })
    .await
    {
        Ok(items) => Json(serde_json::json!({ "days": items })).into_response(),
        Err(resp) => resp,
    }
}

#[derive(serde::Deserialize)]
struct AskParams {
    q: Option<String>,
}

async fn ask_handler(State(state): State<AppState>, Query(params): Query<AskParams>) -> Response {
    let question = params.q.unwrap_or_default();
    if question.trim().is_empty() {
        return api_error(StatusCode::BAD_REQUEST, "bad_request", "q is required");
    }
    let q = question.clone();
    match with_db(&state, move |client| {
        let backend = crate::llm::resolve_backend()?;
        crate::ask::ask(client, &q, &backend, 12)
    })
    .await
    {
        Ok(answer) => Json(answer).into_response(),
        Err(resp) => resp,
    }
}

#[derive(serde::Deserialize)]
struct JobParams {
    job: Option<String>,
}

async fn operate_handler(State(state): State<AppState>, Query(params): Query<JobParams>) -> Response {
    let job = params.job.unwrap_or_default();
    let name = job.clone();
    // backup shells out to pg_dump and does not touch the client; keep it off
    // the DB mutex so long dumps cannot stall every other handler.
    if name == "backup" {
        let dir = std::env::var("THROUGHLINE_BACKUP_DIR").unwrap_or_else(|_| {
            dirs::home_dir()
                .unwrap_or_default()
                .join(".local/share/throughline/backups")
                .to_string_lossy()
                .to_string()
        });
        let message = match tokio::task::spawn_blocking(move || {
            crate::ops_jobs::backup_database(std::path::Path::new(&dir), 14)
                .map(|path| format!("backup: {}", path.display()))
        })
        .await
        {
            Ok(Ok(message)) => message,
            Ok(Err(e)) => {
                return api_error(StatusCode::INTERNAL_SERVER_ERROR, "query_failed", e.to_string())
            }
            Err(join) => {
                return api_error(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "blocking_task_failed",
                    join.to_string(),
                )
            }
        };
        return Json(serde_json::json!({ "job": job, "message": message })).into_response();
    }
    match with_db(&state, move |client| {
        let out = match name.as_str() {
            "ingest" => {
                let runs = crate::ingest_run::run_all(client, false)?;
                let parts: Vec<String> = runs
                    .iter()
                    .map(|s| {
                        format!(
                            "{}: +{} ~{} ={} err{}",
                            s.adapter, s.ingested, s.refreshed, s.skipped, s.errors
                        )
                    })
                    .collect();
                format!("ingest complete — {}", parts.join(" · "))
            }
            "embed" => {
                let backend = crate::embedding::resolve()?;
                let s = crate::generate_embeddings::embed_pending(client, &backend, 200)?;
                format!("embed: embedded={} errors={}", s.embedded, s.errors)
            }
            "extract-memory" => {
                let _backend = crate::llm::resolve_backend()?;
                let rows = client.query(
                    "SELECT id, COALESCE(summary,'') FROM conversations WHERE generated_by IS NULL ORDER BY started_at DESC LIMIT 5",
                    &[],
                )?;
                format!("extract-memory: scanned {} conversation(s)", rows.len())
            }
            "reflect" => {
                let s = crate::reflect_memory::dedup(client, false)?;
                format!("reflect/dedup: acted={}", s.acted)
            }
            "agent-memory" | "mimo-memory" => {
                let (files, chunks) = crate::agent_memory::ingest_agent_memory(client)?;
                format!("agent-memory: scanned {files} file(s), upserted {chunks} chunk(s)")
            }
            "" | "status" => {
                let c = crate::search::count_conversations(client)?;
                let m = crate::search::count_messages(client)?;
                format!("status: conversations={c} messages={m}")
            }
            other => return Err(anyhow::anyhow!("unknown job: {other}")),
        };
        Ok(out)
    })
    .await
    {
        Ok(message) => Json(serde_json::json!({ "job": job, "message": message })).into_response(),
        Err(resp) => resp,
    }
}

#[derive(serde::Deserialize)]
struct HandoffParams {
    /// Preferred filter key (spec: `GET /api/handoff?project=X`).
    project: Option<String>,
    /// Back-compat alias used by older callers.
    q: Option<String>,
}

async fn handoff_handler(State(state): State<AppState>, Query(params): Query<HandoffParams>) -> Response {
    let project = params
        .project
        .or(params.q)
        .filter(|s| !s.trim().is_empty());
    match with_db(&state, move |client| {
        crate::handoff::build_handoff(client, project.as_deref(), 8)
    })
    .await
    {
        Ok(brief) => Json(brief).into_response(),
        Err(resp) => resp,
    }
}


async fn ingest_status_handler(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| crate::ingest_run::ingest_status(client)).await {
        Ok(report) => Json(report).into_response(),
        Err(resp) => resp,
    }
}
async fn costs_handler(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| crate::costs::cost_report(client, 30)).await {
        Ok(report) => Json(report).into_response(),
        Err(resp) => resp,
    }
}


async fn stale_digest_handler(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| crate::stale_digest::build_digest(client, 14)).await {
        Ok(d) => Json(d).into_response(),
        Err(resp) => resp,
    }
}

async fn answer_eval_handler(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| {
        let backend = crate::llm::resolve_backend()?;
        let rows = client.query(
            "SELECT content FROM memory_chunks WHERE status='active' AND category='decision' ORDER BY created_at DESC LIMIT 10",
            &[],
        )?;
        let cases: Vec<crate::answer_eval::EvalCase> = rows
            .into_iter()
            .enumerate()
            .map(|(i, r)| {
                let c: String = r.get(0);
                crate::answer_eval::EvalCase {
                    id: format!("mem-{i}"),
                    question: format!("What did we decide about: {}", c.chars().take(60).collect::<String>()),
                    must_cite_conversation_ids: vec![],
                    expected_phrases: vec![],
                    forbidden_phrases: vec!["probably".into()],
                }
            })
            .collect();
        crate::answer_eval::run_eval(client, &backend, &cases)
    })
    .await
    {
        Ok(r) => Json(r).into_response(),
        Err(resp) => resp,
    }
}

async fn writeback_handler(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| {
        let root = std::env::current_dir().unwrap_or_default();
        crate::memory_writeback::writeback(client, &root, None, 15)
    })
    .await
    {
        Ok(s) => Json(s).into_response(),
        Err(resp) => resp,
    }
}

async fn health_recovery_handler(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| crate::doctor::run(client)).await {
        Ok(r) => {
            let mut actions = Vec::new();
            if r.database != "ok" {
                actions.push(serde_json::json!({"id":"db","label":"Check Postgres","hint":"docker compose up -d postgres"}));
            }
            if r.embeddings.contains("unavailable") {
                actions.push(serde_json::json!({"id":"ollama","label":"Start Ollama embeddings","hint":"ollama serve && ollama pull nomic-embed-text"}));
            }
            if r.generation.contains("unavailable") {
                actions.push(serde_json::json!({"id":"llm","label":"Configure generation","hint":"set THROUGHLINE_ANSWER_BASE_URL or run Ollama"}));
            }
            Json(serde_json::json!({
                "database": r.database,
                "generation": r.generation,
                "embeddings": r.embeddings,
                "generation_local": r.generation_local,
                "notes": r.notes,
                "actions": actions,
                "healthy": r.database == "ok",
            }))
            .into_response()
        }
        Err(resp) => resp,
    }
}

async fn audit_handler(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| crate::audit::audit(client, 30)).await {
        Ok(report) => Json(report).into_response(),
        Err(resp) => resp,
    }
}

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/", get(index))
        .route("/favicon.svg", get(favicon))
        .route("/favicon.ico", get(favicon))
        .route("/index.html", get(index))
        .route("/api/health", get(health))
        .route("/api/status", get(status))
        .route("/api/search", get(search_handler))
        .route("/api/messages", get(messages_handler))
        .route("/api/messages/{id}", get(message_detail))
        .route("/api/tools", get(tools_handler))
        .route("/api/memory", get(memory_handler).post(memory_create))
        .route("/api/memory/{id}", get(memory_detail))
        .route("/api/memory/{id}/forget", post(memory_forget))
        .route("/api/memory/{id}/keep", post(memory_keep))
        .route("/api/mimo-memory/ingest", get(agent_memory_ingest))
        .route("/api/agent-memory/ingest", get(agent_memory_ingest))
        .route("/api/conversations", get(recent))
        .route("/api/conversations/{id}", get(conversation))
        .route("/api/projects", get(projects_handler))
        .route("/api/timeline", get(timeline_handler))
        .route("/api/conflicts", get(conflicts_handler))
        .route("/api/ask", get(ask_handler))
        .route("/api/operate", get(operate_handler))
        .route("/api/handoff", get(handoff_handler))
        .route("/api/costs", get(costs_handler))
        .route("/api/ingest-status", get(ingest_status_handler))
        .route("/api/audit", get(audit_handler))
        .route("/api/stale-digest", get(stale_digest_handler))
        .route("/api/answer-eval", get(answer_eval_handler))
        .route("/api/writeback", get(writeback_handler))
        .route("/api/health-recovery", get(health_recovery_handler))
        .with_state(state)
}

pub async fn serve(host: &str, port: u16, client: postgres::Client) -> Result<()> {
    let state = AppState {
        db: Arc::new(Mutex::new(client)),
    };
    let addr: SocketAddr = format!("{host}:{port}")
        .parse()
        .context("parse listen address")?;
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .with_context(|| format!("bind {addr}"))?;
    eprintln!("Throughline GUI on http://{addr}");
    axum::serve(listener, app(state)).await.context("serve")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn memory_create_unknown_category_is_422() {
        let err = validate_memory_create("durable note", "not-a-category")
            .expect_err("unknown category must be rejected");
        assert_eq!(err.0, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(err.1, "validation_error");
        assert!(err.2.contains("not-a-category"), "{}", err.2);
    }

    #[test]
    fn memory_create_missing_content_is_422() {
        for empty in ["", "   ", "\n\t"] {
            let err = validate_memory_create(empty, "insight")
                .expect_err("empty content must be rejected");
            assert_eq!(err.0, StatusCode::UNPROCESSABLE_ENTITY);
            assert_eq!(err.1, "validation_error");
            assert!(err.2.contains("content"), "{}", err.2);
        }
    }

    #[test]
    fn memory_create_accepts_known_categories() {
        for cat in MEMORY_CATEGORIES {
            validate_memory_create("a durable note body", cat)
                .unwrap_or_else(|e| panic!("{cat} should be valid, got {e:?}"));
        }
    }

    #[test]
    fn forget_unknown_id_is_404() {
        // rows == 0 is what an UPDATE on an unknown id returns.
        let resp = memory_status_response(999_999, 0, "forgotten");
        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[test]
    fn keep_unknown_id_is_404() {
        let resp = memory_status_response(999_999, 0, "active");
        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[test]
    fn keep_known_id_returns_active() {
        let resp = memory_status_response(7, 1, "active");
        assert_eq!(resp.status(), StatusCode::OK);
    }

    #[test]
    fn forget_known_id_returns_forgotten() {
        let resp = memory_status_response(7, 1, "forgotten");
        assert_eq!(resp.status(), StatusCode::OK);
    }
}
