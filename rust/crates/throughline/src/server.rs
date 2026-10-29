//! HTTP API + embedded web UI.
//!
//! Loopback-only by default: the API has no authentication and serves the
//! whole corpus. Same posture as the Python server.

use crate::search;
use crate::temporal::as_of_predicate;
use anyhow::{Context, Result};
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::{Html, IntoResponse, Response};
use axum::routing::{delete, get, post};
use axum::{Json, Router};
use postgres::GenericClient;
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
    /// Whether this conversation is favorited.
    favorite: bool,
}

#[derive(Serialize)]
struct MessageJson {
    id: i64,
    role: String,
    content: String,
    model: Option<String>,
    created_at: chrono::DateTime<chrono::Utc>,
    /// Whether this message is favorited.
    favorite: bool,
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
    /// Whether this conversation is favorited.
    favorite: bool,
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
// `Response` (axum) is the error type and inherently larger than the lint
// threshold; boxing it would churn every call site for no benefit.
#[allow(clippy::result_large_err)]
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
                Err(api_error(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "query_failed",
                    msg,
                ))
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

async fn search_handler(
    State(state): State<AppState>,
    Query(params): Query<SearchParams>,
) -> Response {
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

#[derive(Deserialize)]
struct ConversationParams {
    /// Sort direction for the message list: `asc` | `desc`. Default desc.
    order: Option<String>,
}

async fn conversation(
    State(state): State<AppState>,
    Path(id): Path<i64>,
    Query(params): Query<ConversationParams>,
) -> Response {
    let dir = order_dir(params.order.as_deref(), true);
    let result = with_db(&state, move |client| {
        let conv_rows = client.query(
            r#"
            SELECT id, session_id, project_name, project_path, source_tool, model,
                   started_at, ended_at, message_count,
                   EXISTS (SELECT 1 FROM favorites f
                           WHERE f.kind = 'conversation'
                             AND f.ref = conversations.id::text) AS favorite
            FROM conversations WHERE id = $1
            "#,
            &[&id],
        )?;
        let Some(conv_row) = conv_rows.into_iter().next() else {
            return Ok(None);
        };
        let msgs = client.query(
            &format!(
                r#"
            SELECT id, role::text, COALESCE(content, ''), model, created_at,
                   EXISTS (SELECT 1 FROM favorites f
                           WHERE f.kind = 'message'
                             AND f.ref = messages.id::text) AS favorite
            FROM messages
            WHERE conversation_id = $1
            ORDER BY created_at {dir}, id {dir}
            "#
            ),
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
                favorite: r.get(5),
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
            favorite: conv_row.get(9),
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
    /// Only favorited conversations (`favorites=1`).
    #[serde(default, deserialize_with = "de_flag")]
    favorites: bool,
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

/// Map the `order=asc|desc` query parameter onto a SQL sort direction.
/// Case-insensitive; anything else falls back to `default_desc`.
fn order_dir(raw: Option<&str>, default_desc: bool) -> &'static str {
    match raw.map(|s| s.trim().to_ascii_lowercase()).as_deref() {
        Some("asc") => "ASC",
        Some("desc") => "DESC",
        _ => {
            if default_desc {
                "DESC"
            } else {
                "ASC"
            }
        }
    }
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
    let favorites = params.favorites;

    match with_db(&state, move |client| {
        // Fixed parameter slots keep rust-postgres happy (no dynamic ToSql).
        // $1 include_generated  $2 tool  $3 project  $4 q_pat
        // $5 hide_empty         $6 from  $7 to
        // $8 favorites          $9 limit  $10 offset
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
        let fav_pred = r#"
                  AND ($8::bool = false OR EXISTS (
                    SELECT 1 FROM favorites f
                    WHERE f.kind = 'conversation' AND f.ref = c.id::text
                  ))
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
                  {fav_pred}
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
                    &favorites,
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
                           c.summary, ''), 240),
                       EXISTS (SELECT 1 FROM favorites f
                               WHERE f.kind = 'conversation'
                                 AND f.ref = c.id::text) AS favorite
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
                  {fav_pred}
                ORDER BY {order_sql}
                LIMIT $9 OFFSET $10
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
                &favorites,
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
                favorite: r.get(9),
            })
            .collect();
        Ok(ConversationsPage {
            conversations,
            total,
            limit,
            offset,
        })
    })
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
            c.ended_at.map(|e| e.to_rfc3339()).unwrap_or_default(),
            c.message_count,
            duration,
            csv_escape(&c.preview.replace(['\n', '\r'], " ")),
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
    /// Whether this message is favorited.
    favorite: bool,
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
    /// Sort direction: `asc` | `desc`. Default desc (newest first).
    order: Option<String>,
    /// Only favorited messages (`favorites=1`).
    #[serde(default, deserialize_with = "de_flag")]
    favorites: bool,
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
    let dir = order_dir(params.order.as_deref(), true);
    let favorites = params.favorites;
    let q_pat = q;

    match with_db(&state, move |client| {
        // $1 q  $2 role  $3 tool  $4 conversation_id  $5 tool_calls_only
        // $6 favorites  $7 limit  $8 offset
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
                  AND ($6::bool = false OR EXISTS (
                    SELECT 1 FROM favorites f
                    WHERE f.kind = 'message' AND f.ref = m.id::text
                  ))
            "#;
        let count_sql = format!(
            "SELECT COUNT(*)::bigint FROM messages m
                 JOIN conversations c ON c.id = m.conversation_id
                 {where_sql}"
        );
        let total: i64 = client
            .query_one(
                &count_sql,
                &[&q_pat, &role, &tool, &conv, &tool_only, &favorites],
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
                       m.created_at,
                       EXISTS (SELECT 1 FROM favorites f
                               WHERE f.kind = 'message'
                                 AND f.ref = m.id::text) AS favorite
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                {where_sql}
                ORDER BY m.created_at {dir}, m.id {dir}
                LIMIT $7 OFFSET $8
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
                &favorites,
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
                favorite: r.get(15),
            })
            .collect();
        Ok(MessagePage {
            messages,
            total,
            limit,
            offset,
        })
    })
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
    /// Whether this message is favorited.
    favorite: bool,
}

async fn message_detail(State(state): State<AppState>, Path(id): Path<i64>) -> Response {
    let result = with_db(&state, move |client| {
        let rows = client.query(
            r#"
            SELECT m.id, m.conversation_id, c.session_id, c.project_name,
                   m.role::text, m.tool_name,
                   COALESCE(m.content, ''),
                   m.content_blocks, m.tool_calls, m.metadata,
                   m.token_count, m.model, m.duration_ms, m.created_at,
                   EXISTS (SELECT 1 FROM favorites f
                           WHERE f.kind = 'message'
                             AND f.ref = m.id::text) AS favorite
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
            favorite: r.get(14),
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
    /// `active` (default) | `history`/`superseded` | `all`.
    status: Option<String>,
    #[serde(default = "default_page_limit")]
    limit: i32,
    #[serde(default)]
    offset: i32,
    /// Sort direction: `asc` | `desc`. Default desc (newest first).
    order: Option<String>,
    /// Only favorited memory notes (`favorites=1`).
    #[serde(default, deserialize_with = "de_flag")]
    favorites: bool,
    /// What was valid on this date (`YYYY-MM-DD` or RFC 3339).
    valid_at: Option<String>,
    /// What we knew on this date.
    known_at: Option<String>,
}

/// Parse the `valid_at` / `known_at` query values; empty counts as absent.
/// A plain date means the end of that day (UTC). A date that cannot be read
/// is a 422 naming the field.
#[allow(clippy::type_complexity)]
fn parse_as_of(
    valid_at: Option<&str>,
    known_at: Option<&str>,
) -> std::result::Result<
    (
        Option<chrono::DateTime<chrono::Utc>>,
        Option<chrono::DateTime<chrono::Utc>>,
    ),
    (StatusCode, &'static str, String),
> {
    let one = |name: &str, v: Option<&str>| match v.map(str::trim).filter(|s| !s.is_empty()) {
        None => Ok(None),
        Some(s) => crate::temporal::parse_as_of_instant(s)
            .map(Some)
            .ok_or_else(|| {
                (
                    StatusCode::UNPROCESSABLE_ENTITY,
                    "validation_error",
                    format!("{name} must be YYYY-MM-DD or an RFC 3339 timestamp, got {s:?}"),
                )
            }),
    };
    Ok((one("valid_at", valid_at)?, one("known_at", known_at)?))
}

/// Map the UI/URL status filter onto a SQL predicate + bind.
/// Unknown values fall back to active-only.
fn memory_status_predicate(status: Option<&str>) -> (&'static str, Option<String>) {
    match status.map(|s| s.trim().to_ascii_lowercase()).as_deref() {
        Some("all") => ("TRUE", None),
        Some("history") | Some("superseded") => {
            ("status = 'superseded'", Some("superseded".into()))
        }
        _ => ("status = 'active'", Some("active".into())),
    }
}

async fn memory_handler(
    State(state): State<AppState>,
    Query(params): Query<MemoryParams>,
) -> Response {
    let limit = params.limit.clamp(1, 200);
    let offset = params.offset.max(0);
    let q = params.q;
    let cat = params.category.filter(|c| !c.trim().is_empty());
    let status = params.status;
    let dir = order_dir(params.order.as_deref(), true);
    let favorites = params.favorites;
    let (valid_at, known_at) =
        match parse_as_of(params.valid_at.as_deref(), params.known_at.as_deref()) {
            Ok(v) => v,
            Err((status, code, detail)) => return api_error(status, code, detail),
        };
    match with_db(&state, move |client| {
        list_memory_rows(
            client, q, cat, status, favorites, limit, offset, dir, valid_at, known_at,
        )
    })
    .await
    {
        Ok((total, items)) => {
            let chunks: Vec<_> = items
                .into_iter()
                .map(|row| {
                    serde_json::json!({
                        "id": row.id,
                        "content": row.content,
                        "category": row.category,
                        "project_name": row.project_name,
                        "created_at": row.created_at,
                        "source_at": row.source_at,
                        "valid_from": row.valid_from,
                        "valid_to": row.valid_to,
                        "status": row.status,
                        "superseded_by": row.superseded_by,
                        "superseded_at": row.superseded_at,
                        "supersedes": row.supersedes,
                        "favorite": row.favorite,
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

/// One memory row for the list/detail JSON, including the supersession chain.
struct MemoryRow {
    id: i64,
    content: String,
    category: String,
    project_name: Option<String>,
    created_at: chrono::DateTime<chrono::Utc>,
    source_at: Option<chrono::DateTime<chrono::Utc>>,
    valid_from: Option<chrono::DateTime<chrono::Utc>>,
    valid_to: Option<chrono::DateTime<chrono::Utc>>,
    status: String,
    superseded_by: Option<i64>,
    superseded_at: Option<chrono::DateTime<chrono::Utc>>,
    supersedes: Option<i64>,
    /// Whether this memory note is favorited.
    favorite: bool,
}

/// List memory chunks with an optional status filter and chain links.
/// Superseded notes stay out of the default (active) list; the History filter
/// reaches them. `supersedes` points at the predecessor this note replaced.
/// With `valid_at` or `known_at` the as-of predicate replaces the status filter.
#[allow(clippy::too_many_arguments)]
fn list_memory_rows<C: GenericClient>(
    client: &mut C,
    q: Option<String>,
    category: Option<String>,
    status: Option<String>,
    favorites: bool,
    limit: i32,
    offset: i32,
    dir: &'static str,
    valid_at: Option<chrono::DateTime<chrono::Utc>>,
    known_at: Option<chrono::DateTime<chrono::Utc>>,
) -> Result<(i64, Vec<MemoryRow>)> {
    let q_pat = q.map(|s| format!("%{}%", s.replace('%', "\\%")));
    let as_of = valid_at.is_some() || known_at.is_some();
    let (pred, _label) = memory_status_predicate(status.as_deref());
    // Every placeholder must appear in the statement, so without a reference
    // date the as-of parameters are pinned to NULL instead of left out.
    let base_filter = |v: &str, k: &str| {
        let head = if as_of {
            as_of_predicate(v, k)
        } else {
            format!("{pred} AND {v}::timestamptz IS NULL AND {k}::timestamptz IS NULL")
        };
        format!(
            "{head}
         AND ($1::text IS NULL OR content ILIKE $1 OR COALESCE(project_name,'') ILIKE $1)
         AND ($2::text IS NULL OR category::text = $2)
         AND ($3::bool = false OR EXISTS (
             SELECT 1 FROM favorites f
             WHERE f.kind = 'memory' AND f.ref = mc.id::text
         ))"
        )
    };
    // The count binds $1..$5 (limit and offset are not in it), the page $1..$7.
    let count_filter = base_filter("$4", "$5");
    let sql_filter = base_filter("$6", "$7");
    let total: i64 = client
        .query_one(
            &format!("SELECT COUNT(*)::bigint FROM memory_chunks mc WHERE {count_filter}"),
            &[&q_pat, &category, &favorites, &valid_at, &known_at],
        )?
        .get(0);
    let rows = client.query(
        &format!(
            "SELECT mc.id, mc.content, mc.category::text, mc.project_name, mc.created_at,
                    mc.status, mc.superseded_by, mc.superseded_at,
                    (SELECT o.id FROM memory_chunks o
                      WHERE o.superseded_by = mc.id
                      ORDER BY o.id DESC LIMIT 1) AS supersedes,
                    mc.source_at,
                    EXISTS (SELECT 1 FROM favorites f
                            WHERE f.kind = 'memory'
                              AND f.ref = mc.id::text) AS favorite,
                    mc.valid_from, mc.valid_to
             FROM memory_chunks mc
             WHERE {sql_filter}
             ORDER BY COALESCE(mc.source_at, mc.created_at) {dir}
             LIMIT $4 OFFSET $5"
        ),
        &[
            &q_pat,
            &category,
            &favorites,
            &(limit as i64),
            &(offset as i64),
            &valid_at,
            &known_at,
        ],
    )?;
    let items = rows
        .into_iter()
        .map(|r| MemoryRow {
            id: r.get(0),
            content: r.get(1),
            category: r.get(2),
            project_name: r.get(3),
            created_at: r.get(4),
            status: r.get(5),
            superseded_by: r.get(6),
            superseded_at: r.get(7),
            supersedes: r.get(8),
            source_at: r.get(9),
            favorite: r.get(10),
            valid_from: r.get(11),
            valid_to: r.get(12),
        })
        .collect();
    Ok((total, items))
}

async fn memory_detail(State(state): State<AppState>, Path(id): Path<i64>) -> Response {
    match with_db(&state, move |client| {
        let row = client.query_opt(
            "SELECT mc.content, mc.category::text, mc.project_name, mc.created_at, mc.status,
                    mc.superseded_by, mc.superseded_at,
                    (SELECT o.id FROM memory_chunks o
                      WHERE o.superseded_by = mc.id
                      ORDER BY o.id DESC LIMIT 1) AS supersedes,
                    EXISTS (SELECT 1 FROM favorites f
                            WHERE f.kind = 'memory'
                              AND f.ref = mc.id::text) AS favorite,
                    mc.source_at, mc.valid_from, mc.valid_to
             FROM memory_chunks mc WHERE mc.id = $1",
            &[&id],
        )?;
        Ok(row.map(|r| MemoryRow {
            id,
            content: r.get(0),
            category: r.get(1),
            project_name: r.get(2),
            created_at: r.get(3),
            source_at: r.get(9),
            valid_from: r.get(10),
            valid_to: r.get(11),
            status: r.get(4),
            superseded_by: r.get(5),
            superseded_at: r.get(6),
            supersedes: r.get(7),
            favorite: r.get(8),
        }))
    })
    .await
    {
        Ok(Some(row)) => Json(serde_json::json!({
            "id": row.id,
            "content": row.content,
            "category": row.category,
            "project_name": row.project_name,
            "created_at": row.created_at,
            "source_at": row.source_at,
            "valid_from": row.valid_from,
            "valid_to": row.valid_to,
            "status": row.status,
            "superseded_by": row.superseded_by,
            "superseded_at": row.superseded_at,
            "supersedes": row.supersedes,
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
        api_error(StatusCode::NOT_FOUND, "not_found", "memory chunk not found")
    } else {
        Json(serde_json::json!({ "id": id, "status": status })).into_response()
    }
}

// ─── supersede: replace a note with a new active one, keep the old as history ─

/// Outcome codes for `supersede_memory`, mapped onto HTTP 422/404/409/500.
#[derive(Debug)]
pub enum SupersedeError {
    EmptyContent,
    InvalidCategory(String),
    NotFound,
    AlreadySuperseded,
    ValidFromInFuture,
    ValidFromBeforePredecessor,
    Db(anyhow::Error),
}

impl std::fmt::Display for SupersedeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SupersedeError::EmptyContent => write!(f, "content is required"),
            SupersedeError::InvalidCategory(c) => write!(f, "unknown category: {c}"),
            SupersedeError::NotFound => write!(f, "memory chunk not found"),
            SupersedeError::AlreadySuperseded => write!(f, "memory chunk already superseded"),
            SupersedeError::ValidFromInFuture => write!(f, "valid_from lies in the future"),
            SupersedeError::ValidFromBeforePredecessor => {
                write!(f, "valid_from lies before the start of the replaced note")
            }
            SupersedeError::Db(e) => write!(f, "{e}"),
        }
    }
}

impl From<anyhow::Error> for SupersedeError {
    fn from(e: anyhow::Error) -> Self {
        SupersedeError::Db(e)
    }
}

impl From<postgres::Error> for SupersedeError {
    fn from(e: postgres::Error) -> Self {
        SupersedeError::Db(anyhow::anyhow!(e))
    }
}

/// Pure request validation shared by HTTP and MCP. 422 on empty content or an
/// unknown category; the category default (old note's category) is applied later.
pub fn validate_supersede(
    content: &str,
    category: Option<&str>,
) -> std::result::Result<(), SupersedeError> {
    if content.trim().is_empty() {
        return Err(SupersedeError::EmptyContent);
    }
    if let Some(cat) = category {
        if !MEMORY_CATEGORIES.contains(&cat.trim()) {
            return Err(SupersedeError::InvalidCategory(cat.trim().to_string()));
        }
    }
    Ok(())
}

/// Why a note is replaced: the fact changed (`Update`) or the old note was
/// wrong from the start (`Correction`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SupersedeKind {
    Update,
    Correction,
}

impl SupersedeKind {
    /// `"update"` | `"correction"`; anything else is `None`.
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "update" => Some(Self::Update),
            "correction" => Some(Self::Correction),
            _ => None,
        }
    }
}

/// The note that replaces another one. `source_type` tags the write (`ui_pin`
/// from the GUI, `mcp_write` from MCP, `manual` from the agent-memory import).
pub struct Successor<'a> {
    pub content: &'a str,
    /// `None`: the old note's category.
    pub category: Option<&'a str>,
    pub source_type: &'a str,
    pub kind: SupersedeKind,
    /// `None`: an update starts now, a correction starts where the old note began.
    pub valid_from: Option<chrono::DateTime<chrono::Utc>>,
    /// `valid_from` came from a plain `YYYY-MM-DD` (midnight UTC). On the day
    /// the old note began it means the old note's start.
    pub valid_from_is_date: bool,
    pub confidence: f64,
    pub tags: Vec<String>,
    /// `None`: now().
    pub source_at: Option<chrono::DateTime<chrono::Utc>>,
}

/// Replace `id` with a new active note. One transaction: insert the successor,
/// mark the predecessor (`superseded_by`, `superseded_at`, `status`), close its
/// validity interval and move a star from the old note to the new one.
/// An update ends the old interval where the new one starts; a correction
/// empties it, because the old note was never true.
pub fn supersede_memory<C: GenericClient>(
    client: &mut C,
    id: i64,
    new: &Successor,
) -> std::result::Result<(i64, i64), SupersedeError> {
    validate_supersede(new.content, new.category)?;
    let content = new.content.trim().to_string();
    let mut tx = client.transaction()?;
    let old = tx.query_opt(
        "SELECT category::text, project_name, status, superseded_by,
                COALESCE(valid_from, source_at, created_at)
         FROM memory_chunks WHERE id = $1 FOR UPDATE",
        &[&id],
    )?;
    let Some(old) = old else {
        return Err(SupersedeError::NotFound);
    };
    let old_category: String = old.get(0);
    let old_project: Option<String> = old.get(1);
    let old_status: String = old.get(2);
    let old_superseded_by: Option<i64> = old.get(3);
    let old_vf: chrono::DateTime<chrono::Utc> = old.get(4);
    if old_superseded_by.is_some() || old_status == "superseded" || old_status == "merged" {
        return Err(SupersedeError::AlreadySuperseded);
    }
    let now = chrono::Utc::now();
    let vf = match (new.valid_from, new.kind) {
        (Some(v), _) => {
            if v > now {
                return Err(SupersedeError::ValidFromInFuture);
            }
            // A plain date on the day the old note began means "from its
            // start": the date's midnight would lie before it.
            if new.valid_from_is_date && v < old_vf && v.date_naive() == old_vf.date_naive() {
                old_vf
            } else {
                v
            }
        }
        (None, SupersedeKind::Update) => now.max(old_vf),
        (None, SupersedeKind::Correction) => old_vf,
    };
    if vf < old_vf {
        return Err(SupersedeError::ValidFromBeforePredecessor);
    }
    let cat = new
        .category
        .map(|c| c.trim().to_string())
        .unwrap_or(old_category);
    let category_type = crate::extract_memory::category_column_type(&mut tx)?;
    let new_id: i64 = tx
        .query_one(
            &format!(
                r#"
            INSERT INTO memory_chunks (source_type, content, category, project_name, confidence, status, source_at, valid_from, tags)
            VALUES ($1, $2, $3::text::{category_type}, $4, $5::float8, 'active', COALESCE($6, now()), $7, $8::text[])
            RETURNING id
            "#
            ),
            &[
                &new.source_type,
                &content,
                &cat,
                &old_project,
                &new.confidence,
                &new.source_at,
                &vf,
                &new.tags,
            ],
        )?
        .get(0);
    let closes_at = match new.kind {
        SupersedeKind::Update => vf,
        SupersedeKind::Correction => old_vf,
    };
    // A forgotten predecessor stays forgotten: forgotten appears in no answer,
    // neither in History nor as of a date.
    tx.execute(
        "UPDATE memory_chunks
         SET status = CASE WHEN status = 'forgotten' THEN status ELSE 'superseded' END,
             superseded_by = $2, superseded_at = now(), updated_at = now(),
             valid_to = CASE WHEN $4 THEN $3 ELSE LEAST(valid_to, $3) END
         WHERE id = $1",
        &[
            &id,
            &new_id,
            &closes_at,
            &(new.kind == SupersedeKind::Correction),
        ],
    )?;
    tx.execute(
        "UPDATE favorites SET ref = $2::text WHERE kind = 'memory' AND ref = $1::text",
        &[&id.to_string(), &new_id.to_string()],
    )?;
    tx.commit()?;
    Ok((id, new_id))
}

/// Map a `SupersedeError` onto the HTTP response the contract requires.
fn supersede_error_response(e: SupersedeError) -> Response {
    match e {
        SupersedeError::EmptyContent => api_error(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            "content is required",
        ),
        SupersedeError::InvalidCategory(c) => api_error(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            format!("unknown category: {c}"),
        ),
        SupersedeError::NotFound => {
            api_error(StatusCode::NOT_FOUND, "not_found", "memory chunk not found")
        }
        SupersedeError::AlreadySuperseded => api_error(
            StatusCode::CONFLICT,
            "already_superseded",
            "memory chunk already superseded",
        ),
        SupersedeError::ValidFromInFuture => api_error(
            StatusCode::UNPROCESSABLE_ENTITY,
            "valid_from_in_future",
            "valid_from lies in the future",
        ),
        SupersedeError::ValidFromBeforePredecessor => api_error(
            StatusCode::UNPROCESSABLE_ENTITY,
            "valid_from_before_predecessor",
            "valid_from lies before the start of the replaced note",
        ),
        SupersedeError::Db(err) => api_error(
            StatusCode::INTERNAL_SERVER_ERROR,
            "query_failed",
            err.to_string(),
        ),
    }
}

#[derive(Deserialize)]
struct MemorySupersede {
    #[serde(default)]
    content: String,
    #[serde(default)]
    category: Option<String>,
    #[serde(default)]
    valid_from: Option<String>,
    #[serde(default)]
    kind: Option<String>,
}

/// `POST /api/memory/{id}/supersede` — body `{content, category?, valid_from?, kind?}`
/// → `{old_id, new_id}`. 422 empty content / unknown category / unknown kind /
/// unparsable or out-of-range valid_from, 404 unknown id, 409 already superseded.
async fn memory_supersede(
    State(state): State<AppState>,
    Path(id): Path<i64>,
    Json(body): Json<MemorySupersede>,
) -> Response {
    let content = body.content;
    let category = body
        .category
        .map(|c| c.trim().to_string())
        .filter(|c| !c.is_empty());
    let kind = match body
        .kind
        .as_deref()
        .map(str::trim)
        .filter(|k| !k.is_empty())
    {
        None => SupersedeKind::Update,
        Some(k) => match SupersedeKind::parse(k) {
            Some(k) => k,
            None => {
                return api_error(
                    StatusCode::UNPROCESSABLE_ENTITY,
                    "validation_error",
                    "kind must be update or correction",
                )
            }
        },
    };
    let valid_from_is_date = body
        .valid_from
        .as_deref()
        .is_some_and(crate::temporal::is_plain_date);
    let valid_from = match body
        .valid_from
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty())
    {
        None => None,
        Some(v) => match crate::temporal::parse_instant(v) {
            Some(t) => Some(t),
            None => {
                return api_error(
                    StatusCode::UNPROCESSABLE_ENTITY,
                    "validation_error",
                    "valid_from must be an RFC 3339 timestamp or a YYYY-MM-DD date",
                )
            }
        },
    };
    // Carry SupersedeError as Ok(payload) so with_db keeps 500 only for real
    // connection failures and the typed codes stay exact.
    let outcome = with_db(&state, move |client| {
        Ok(supersede_memory(
            client,
            id,
            &Successor {
                content: &content,
                category: category.as_deref(),
                source_type: "ui_pin",
                kind,
                valid_from,
                valid_from_is_date,
                confidence: 1.0,
                tags: Vec::new(),
                source_at: None,
            },
        ))
    })
    .await;
    match outcome {
        Ok(Ok((old_id, new_id))) => Json(serde_json::json!({
            "old_id": old_id,
            "new_id": new_id,
        }))
        .into_response(),
        Ok(Err(e)) => supersede_error_response(e),
        Err(resp) => resp,
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
        let category_type = crate::extract_memory::category_column_type(client)?;
        let row = client.query_one(
            &format!(
                r#"
            INSERT INTO memory_chunks (source_type, content, category, project_name, confidence, status, source_at)
            VALUES ('ui_pin', $1, $2::text::{category_type}, $3, 1.0, 'active', now())
            RETURNING id
            "#
            ),
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

#[derive(Debug, PartialEq, Eq)]
enum KeepOutcome {
    Kept,
    NotFound,
    HasSuccessor,
}

/// Confirm a note: back to active (undo of forget), expiry cleared, `reviewed`
/// tag and `updated_at` stamped. A note that a successor replaced stays
/// superseded, so keeping it cannot revive it next to the successor.
fn keep_memory<C: GenericClient>(client: &mut C, id: i64) -> Result<KeepOutcome> {
    let mut tx = client.transaction()?;
    let Some(row) = tx.query_opt(
        "SELECT superseded_by FROM memory_chunks WHERE id = $1 FOR UPDATE",
        &[&id],
    )?
    else {
        return Ok(KeepOutcome::NotFound);
    };
    let superseded_by: Option<i64> = row.get(0);
    if superseded_by.is_some() {
        // Dropping tx rolls back; nothing was written.
        return Ok(KeepOutcome::HasSuccessor);
    }
    tx.execute(
        r#"
        UPDATE memory_chunks
        SET status = 'active',
            expires_at = NULL,
            updated_at = now(),
            tags = CASE
                WHEN 'reviewed' = ANY(COALESCE(tags, '{}')) THEN tags
                ELSE array_append(COALESCE(tags, '{}'), 'reviewed')
            END
        WHERE id = $1
        "#,
        &[&id],
    )?;
    tx.commit()?;
    Ok(KeepOutcome::Kept)
}

async fn memory_keep(State(state): State<AppState>, Path(id): Path<i64>) -> Response {
    match with_db(&state, move |client| keep_memory(client, id)).await {
        Ok(KeepOutcome::Kept) => {
            Json(serde_json::json!({ "id": id, "status": "active" })).into_response()
        }
        Ok(KeepOutcome::NotFound) => {
            api_error(StatusCode::NOT_FOUND, "not_found", "memory chunk not found")
        }
        Ok(KeepOutcome::HasSuccessor) => api_error(
            StatusCode::CONFLICT,
            "has_successor",
            "memory chunk was replaced by a newer note and stays superseded",
        ),
        Err(resp) => resp,
    }
}

async fn agent_memory_ingest(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| {
        crate::agent_memory::ingest_agent_memory(client)
    })
    .await
    {
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

/// Project cards: name, description, conversation count, status and the
/// newest `started_at` of the project's conversations (`last_started_at`,
/// ISO-8601 string or null when the project has no conversations). Each row
/// carries `favorite`; `favorites` keeps only favorited projects.
fn project_rows<C: GenericClient>(
    client: &mut C,
    favorites: bool,
) -> anyhow::Result<Vec<serde_json::Value>> {
    let rows = client.query(
        r#"
        SELECT p.name,
               COALESCE(p.description, ''),
               (SELECT COUNT(*) FROM conversations c WHERE c.project_name = p.name),
               p.status::text,
               (SELECT MAX(c.started_at) FROM conversations c WHERE c.project_name = p.name),
               EXISTS (SELECT 1 FROM favorites f
                       WHERE f.kind = 'project' AND f.ref = p.name) AS favorite
        FROM projects p
        WHERE ($1::bool = false OR EXISTS (
            SELECT 1 FROM favorites f
            WHERE f.kind = 'project' AND f.ref = p.name
        ))
        ORDER BY p.name
        "#,
        &[&favorites],
    )?;
    Ok(rows
        .into_iter()
        .map(|r| {
            serde_json::json!({
                "name": r.get::<_, String>(0),
                "description": r.get::<_, String>(1),
                "conversations": r.get::<_, i64>(2),
                "status": r.get::<_, Option<String>>(3),
                "last_started_at": r.get::<_, Option<chrono::DateTime<chrono::Utc>>>(4),
                "favorite": r.get::<_, bool>(5),
            })
        })
        .collect::<Vec<_>>())
}

#[derive(serde::Deserialize)]
struct ProjectsParams {
    /// Only favorited projects (`favorites=1`).
    #[serde(default, deserialize_with = "de_flag")]
    favorites: bool,
}

async fn projects_handler(
    State(state): State<AppState>,
    Query(params): Query<ProjectsParams>,
) -> Response {
    let favorites = params.favorites;
    match with_db(&state, move |client| project_rows(client, favorites)).await {
        Ok(items) => Json(serde_json::json!({ "projects": items })).into_response(),
        Err(resp) => resp,
    }
}

#[derive(serde::Deserialize)]
struct TimelineParams {
    since: Option<String>,
    until: Option<String>,
    /// Only favorited conversations (`favorites=1`).
    #[serde(default, deserialize_with = "de_flag")]
    favorites: bool,
}

/// Parse the optional `since`/`until` query bounds (`YYYY-MM-DD`).
/// The error message is what the handler sends as `{"error": "..."}` (HTTP 400).
fn parse_timeline_range(
    since: Option<&str>,
    until: Option<&str>,
) -> std::result::Result<(Option<chrono::NaiveDate>, Option<chrono::NaiveDate>), String> {
    fn parse_one(
        label: &str,
        raw: Option<&str>,
    ) -> std::result::Result<Option<chrono::NaiveDate>, String> {
        match raw {
            None => Ok(None),
            Some(s) => chrono::NaiveDate::parse_from_str(s, "%Y-%m-%d")
                .map(Some)
                .map_err(|_| format!("invalid {label} date '{s}': expected YYYY-MM-DD")),
        }
    }
    let since = parse_one("since", since)?;
    let until = parse_one("until", until)?;
    if let (Some(s), Some(u)) = (since, until) {
        if s > u {
            return Err(format!("since ({s}) is after until ({u})"));
        }
    }
    Ok((since, until))
}

/// Day × tool conversation counts, newest first. One row per group — the
/// grouping caps the row count by itself, `LIMIT 5000` is only the backstop.
/// Without bounds the classic rolling 90-day window applies; with bounds both
/// days are inclusive.
fn timeline_day_rows<C: GenericClient>(
    client: &mut C,
    since: Option<chrono::NaiveDate>,
    until: Option<chrono::NaiveDate>,
    favorites_only: bool,
) -> anyhow::Result<Vec<serde_json::Value>> {
    let rows = match (since, until) {
        (None, None) => client.query(
            r#"
            SELECT date_trunc('day', started_at)::date AS day,
                   source_tool,
                   COUNT(*)
            FROM conversations c
            WHERE generated_by IS NULL
              AND started_at > now() - interval '90 days'
              AND ($1::bool = false OR EXISTS (
                SELECT 1 FROM favorites f
                WHERE f.kind = 'conversation' AND f.ref = c.id::text
              ))
            GROUP BY 1, 2
            ORDER BY 1 DESC
            LIMIT 5000
            "#,
            &[&favorites_only],
        )?,
        (since, until) => {
            let since = since
                .unwrap_or_else(|| chrono::NaiveDate::from_ymd_opt(1, 1, 1).expect("min date"));
            let until = until.unwrap_or_else(|| {
                chrono::NaiveDate::from_ymd_opt(9999, 12, 31).expect("max date")
            });
            client.query(
                r#"
                SELECT date_trunc('day', started_at)::date AS day,
                       source_tool,
                       COUNT(*)
                FROM conversations c
                WHERE generated_by IS NULL
                  AND started_at >= $1::date
                  AND started_at < ($2::date + 1)
                  AND ($3::bool = false OR EXISTS (
                    SELECT 1 FROM favorites f
                    WHERE f.kind = 'conversation' AND f.ref = c.id::text
                  ))
                GROUP BY 1, 2
                ORDER BY 1 DESC
                LIMIT 5000
                "#,
                &[&since, &until, &favorites_only],
            )?
        }
    };
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
}

async fn timeline_handler(
    State(state): State<AppState>,
    Query(params): Query<TimelineParams>,
) -> Response {
    let (since, until) =
        match parse_timeline_range(params.since.as_deref(), params.until.as_deref()) {
            Ok(range) => range,
            Err(msg) => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({ "error": msg })),
                )
                    .into_response();
            }
        };
    let favorites = params.favorites;
    match with_db(&state, move |client| {
        timeline_day_rows(client, since, until, favorites)
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

async fn operate_handler(
    State(state): State<AppState>,
    Query(params): Query<JobParams>,
) -> Response {
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
                return api_error(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "query_failed",
                    e.to_string(),
                )
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
            "import-config" => {
                let s = crate::config_import::run(client)?;
                s.to_string()
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

async fn handoff_handler(
    State(state): State<AppState>,
    Query(params): Query<HandoffParams>,
) -> Response {
    let project = params.project.or(params.q).filter(|s| !s.trim().is_empty());
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
    match with_db(&state, crate::ingest_run::ingest_status).await {
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
    match with_db(&state, |client| {
        crate::stale_digest::build_digest(client, 14)
    })
    .await
    {
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
    match with_db(&state, crate::doctor::run).await {
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

// --- M17a: assistants inventory ---

#[derive(Deserialize)]
struct AssistantsToolParams {
    kind: String,
}

#[derive(Deserialize)]
struct AssistantsFileParams {
    id: String,
    /// Unmasked content for the editor (`edit=1`); needs THROUGHLINE_CONFIG_EDIT.
    #[serde(default, deserialize_with = "de_flag")]
    edit: bool,
}

/// Editing assistant files is opt-in: `THROUGHLINE_CONFIG_EDIT=1` and, in
/// Docker, writable mounts (`docker-compose.edit.yml`).
fn config_edit_enabled() -> bool {
    matches!(
        std::env::var("THROUGHLINE_CONFIG_EDIT")
            .unwrap_or_default()
            .trim()
            .to_lowercase()
            .as_str(),
        "1" | "true" | "yes" | "on"
    )
}

/// Where a file's previous text goes before an edit is saved.
fn config_backup_dir() -> std::path::PathBuf {
    if let Ok(dir) = std::env::var("THROUGHLINE_CONFIG_BACKUP_DIR") {
        return dir.into();
    }
    for var in ["THROUGHLINE_BACKUP_DIR", "CLAUDE_MEMORY_BACKUP_DIR"] {
        if let Ok(dir) = std::env::var(var) {
            if !dir.trim().is_empty() {
                return std::path::PathBuf::from(dir).join("config-edits");
            }
        }
    }
    dirs::home_dir()
        .unwrap_or_default()
        .join(".local/share/throughline/config-edits")
}

fn save_error_response(e: crate::assistants::SaveError) -> Response {
    use crate::assistants::SaveError;
    match e {
        SaveError::NotFound => api_error(
            StatusCode::NOT_FOUND,
            "not_found",
            "unknown configuration file",
        ),
        SaveError::HasSecrets => api_error(
            StatusCode::UNPROCESSABLE_ENTITY,
            "has_secrets",
            "the file contains keys or tokens and stays read-only",
        ),
        SaveError::TooLarge => api_error(
            StatusCode::PAYLOAD_TOO_LARGE,
            "too_large",
            "files over 256 KB cannot be edited here",
        ),
        SaveError::Conflict => api_error(
            StatusCode::CONFLICT,
            "conflict",
            "the file changed since it was opened",
        ),
        SaveError::Io(msg) => api_error(StatusCode::INTERNAL_SERVER_ERROR, "io_error", msg),
    }
}

fn editing_disabled() -> Response {
    api_error(
        StatusCode::FORBIDDEN,
        "editing_disabled",
        "editing is off; set THROUGHLINE_CONFIG_EDIT=1 (in Docker: docker-compose.edit.yml)",
    )
}

async fn assistant_entries(state: &AppState) -> Vec<crate::assistants::Entry> {
    match with_db(state, |client| {
        Ok(crate::assistants::inventory(Some(client)))
    })
    .await
    {
        Ok(e) => e,
        Err(_) => crate::assistants::inventory(None),
    }
}

#[derive(serde::Deserialize)]
struct AssistantsSaveBody {
    id: String,
    content: String,
    sha256: String,
}

async fn assistants_save_handler(
    State(state): State<AppState>,
    Json(body): Json<AssistantsSaveBody>,
) -> Response {
    if !config_edit_enabled() {
        return editing_disabled();
    }
    let entries = assistant_entries(&state).await;
    match crate::assistants::save_entry(
        &entries,
        &body.id,
        &body.content,
        &body.sha256,
        &config_backup_dir(),
    ) {
        Ok(outcome) => Json(outcome).into_response(),
        Err(e) => save_error_response(e),
    }
}

async fn assistants_handler(State(state): State<AppState>) -> Response {
    let result = with_db(&state, |client| {
        let entries = crate::assistants::inventory(Some(client));
        let roots = crate::assistants::project_roots(Some(client));
        Ok((entries, roots))
    })
    .await;
    let (entries, roots) = match result {
        Ok(v) => v,
        Err(_) => (
            crate::assistants::inventory(None),
            crate::assistants::project_roots(None),
        ),
    };
    let tools = crate::assistants::counts(&entries);
    let home = crate::assistants::home();
    Json(serde_json::json!({
        "tools": tools,
        "home": home.to_string_lossy(),
        "project_roots_checked": roots.len(),
    }))
    .into_response()
}

async fn assistants_file_handler(
    State(state): State<AppState>,
    Query(params): Query<AssistantsFileParams>,
) -> Response {
    let entries = assistant_entries(&state).await;
    if params.edit {
        if !config_edit_enabled() {
            return editing_disabled();
        }
        return match crate::assistants::read_entry_raw(&entries, &params.id) {
            Ok(raw) => Json(raw).into_response(),
            Err(e) => save_error_response(e),
        };
    }
    match crate::assistants::read_entry(&entries, &params.id) {
        Some(mut view) => {
            view.editable = config_edit_enabled()
                && !view.truncated
                && crate::assistants::read_entry_raw(&entries, &params.id).is_ok();
            Json(view).into_response()
        }
        None => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({ "error": "not found" })),
        )
            .into_response(),
    }
}

async fn assistants_tool_handler(
    State(state): State<AppState>,
    Path(tool): Path<String>,
    Query(params): Query<AssistantsToolParams>,
) -> Response {
    let kind = match params.kind.parse::<crate::assistants::Kind>() {
        Ok(k) => k,
        Err(e) => return api_error(StatusCode::BAD_REQUEST, "invalid_kind", e),
    };
    let known: Vec<String> = crate::adapters::registry::built_ins()
        .iter()
        .map(|a| a.name().to_string())
        .collect();
    if !known.contains(&tool) {
        return api_error(
            StatusCode::BAD_REQUEST,
            "invalid_tool",
            format!("unknown tool: {tool}"),
        );
    }
    let entries = match with_db(&state, |client| {
        Ok(crate::assistants::inventory(Some(client)))
    })
    .await
    {
        Ok(e) => e,
        Err(_) => crate::assistants::inventory(None),
    };
    let filtered: Vec<_> = entries
        .iter()
        .filter(|e| e.tool == tool && e.kind == kind)
        .cloned()
        .collect();
    Json(serde_json::json!({
        "tool": tool,
        "kind": kind.as_str(),
        "entries": filtered,
    }))
    .into_response()
}

#[derive(Deserialize)]
struct ImportedParams {
    tool: Option<String>,
    kind: Option<String>,
    /// Only favorited entries (`favorites=1`).
    #[serde(default, deserialize_with = "de_flag")]
    favorites: bool,
}

async fn assistants_imported_handler(
    State(state): State<AppState>,
    Query(params): Query<ImportedParams>,
) -> Response {
    match with_db(&state, move |client| {
        let entries = crate::config_import::list(
            client,
            params.tool.as_deref(),
            params.kind.as_deref(),
            params.favorites,
        )?;
        let last_import = crate::config_import::last_import(client)?;
        Ok(serde_json::json!({ "entries": entries, "last_import": last_import }))
    })
    .await
    {
        Ok(body) => Json(body).into_response(),
        Err(resp) => resp,
    }
}

async fn assistants_imported_file_handler(
    State(state): State<AppState>,
    Path(id): Path<i64>,
) -> Response {
    match with_db(&state, move |client| crate::config_import::get(client, id)).await {
        Ok(Some(file)) => Json(file).into_response(),
        Ok(None) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({ "error": "not found" })),
        )
            .into_response(),
        Err(resp) => resp,
    }
}

// ─── Favorites: set / remove / list ─────────────────────────────────────────

#[derive(Deserialize)]
struct FavoritesListParams {
    kind: Option<String>,
}

#[derive(Deserialize)]
struct FavoritesAddBody {
    kind: String,
    #[serde(rename = "ref")]
    ref_: String,
}

async fn favorites_list_handler(
    State(state): State<AppState>,
    Query(params): Query<FavoritesListParams>,
) -> Response {
    let kind = match params.kind {
        Some(raw) if !raw.trim().is_empty() => match raw.parse::<crate::favorites::FavKind>() {
            Ok(k) => Some(k),
            Err(_) => {
                return api_error(
                    StatusCode::BAD_REQUEST,
                    "invalid_kind",
                    format!("unknown kind '{raw}'"),
                )
            }
        },
        _ => None,
    };
    match with_db(&state, move |client| crate::favorites::list(client, kind)).await {
        Ok(favorites) => {
            let count = favorites.len();
            Json(serde_json::json!({ "favorites": favorites, "count": count })).into_response()
        }
        Err(resp) => resp,
    }
}

async fn favorites_add_handler(
    State(state): State<AppState>,
    Json(body): Json<FavoritesAddBody>,
) -> Response {
    let kind = match body.kind.parse::<crate::favorites::FavKind>() {
        Ok(k) => k,
        Err(_) => {
            return api_error(
                StatusCode::BAD_REQUEST,
                "invalid_kind",
                format!("unknown kind '{}'", body.kind),
            )
        }
    };
    if let Err(e) = crate::favorites::validate_ref(kind, &body.ref_) {
        return api_error(StatusCode::BAD_REQUEST, "invalid_ref", e);
    }
    let ref_ = body.ref_;
    let ref_for_resp = ref_.clone();
    match with_db(&state, move |client| {
        crate::favorites::add(client, kind, &ref_)
    })
    .await
    {
        Ok(()) => (
            StatusCode::CREATED,
            Json(serde_json::json!({
                "kind": kind.as_str(),
                "ref": ref_for_resp,
                "favorite": true,
            })),
        )
            .into_response(),
        Err(resp) => resp,
    }
}

async fn favorites_delete_handler(
    State(state): State<AppState>,
    Path((kind_raw, ref_raw)): Path<(String, String)>,
) -> Response {
    let kind = match kind_raw.parse::<crate::favorites::FavKind>() {
        Ok(k) => k,
        Err(_) => {
            return api_error(
                StatusCode::BAD_REQUEST,
                "invalid_kind",
                format!("unknown kind '{kind_raw}'"),
            )
        }
    };
    if let Err(e) = crate::favorites::validate_ref(kind, &ref_raw) {
        return api_error(StatusCode::BAD_REQUEST, "invalid_ref", e);
    }
    match with_db(&state, move |client| {
        crate::favorites::remove(client, kind, &ref_raw)
    })
    .await
    {
        Ok(_removed) => StatusCode::NO_CONTENT.into_response(),
        Err(resp) => resp,
    }
}

const LOOPBACK_HOSTS: [&str; 3] = ["localhost", "127.0.0.1", "::1"];

/// Hosts the server answers to: loopback, plus the host of `public_url` and
/// any comma-separated names in `extra`. `0.0.0.0` is deliberately absent.
fn allowed_hosts(public_url: Option<&str>, extra: Option<&str>) -> Vec<String> {
    let mut hosts: Vec<String> = LOOPBACK_HOSTS.map(String::from).to_vec();
    if let Some(url) = public_url.map(str::trim).filter(|u| !u.is_empty()) {
        let authority = url.split("://").nth(1).unwrap_or(url);
        hosts.push(host_of(authority.split('/').next().unwrap_or("")));
    }
    hosts.extend(
        extra
            .unwrap_or("")
            .split(',')
            .map(|h| h.trim().to_lowercase())
            .filter(|h| !h.is_empty()),
    );
    hosts
}

/// Lowercased host of `host[:port]` or `[v6]:port`.
fn host_of(authority: &str) -> String {
    let a = authority.trim();
    let host = match a.strip_prefix('[') {
        Some(rest) => rest.split(']').next().unwrap_or(""),
        None => a.split(':').next().unwrap_or(""),
    };
    host.to_lowercase()
}

/// Why a request must be refused, or `None` when it may pass.
///
/// The Host check stops DNS rebinding, the Origin check stops cross-site
/// writes, and `Sec-Fetch-Site` keeps other sites from calling the API even
/// with GET (an `<img>` pointing at a job route). Clients without these
/// headers, such as curl or the CLI, pass.
fn refuse_reason(
    host: Option<&str>,
    origin: Option<&str>,
    fetch_site: Option<&str>,
    path: &str,
    allowed: &[String],
) -> Option<&'static str> {
    if host.is_some_and(|h| !allowed.contains(&host_of(h))) {
        return Some("host not allowed");
    }
    if let Some(origin) = origin {
        // Same origin: the host and port the request was sent to. A named,
        // non-loopback host also passes, because a reverse proxy may rewrite
        // Host to its upstream address. Loopback must match the port exactly,
        // so another local app cannot pose as this one.
        let same = match (origin.split("://").nth(1), host) {
            (Some(authority), Some(host)) if authority.eq_ignore_ascii_case(host) => true,
            (Some(authority), _) => {
                let origin_host = host_of(authority);
                allowed.contains(&origin_host)
                    && (host.is_none() || !LOOPBACK_HOSTS.contains(&origin_host.as_str()))
            }
            (None, _) => false,
        };
        if !same {
            return Some("cross-origin request");
        }
    }
    if path.starts_with("/api/") && !matches!(fetch_site, None | Some("same-origin") | Some("none"))
    {
        return Some("cross-site request");
    }
    None
}

async fn request_guard(
    State(allowed): State<Arc<Vec<String>>>,
    req: axum::extract::Request,
    next: axum::middleware::Next,
) -> Response {
    let headers = req.headers();
    let header = |name: &str| headers.get(name).and_then(|v| v.to_str().ok());
    let host = header("host").or_else(|| req.uri().authority().map(|a| a.as_str()));
    if let Some(reason) = refuse_reason(
        host,
        header("origin"),
        header("sec-fetch-site"),
        req.uri().path(),
        &allowed,
    ) {
        return api_error(StatusCode::FORBIDDEN, "forbidden_request", reason);
    }
    next.run(req).await
}

pub fn app(state: AppState) -> Router {
    let allowed = Arc::new(allowed_hosts(
        std::env::var("THROUGHLINE_PUBLIC_URL").ok().as_deref(),
        std::env::var("THROUGHLINE_ALLOWED_HOSTS").ok().as_deref(),
    ));
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
        .route("/api/memory/{id}/supersede", post(memory_supersede))
        .route("/api/mimo-memory/ingest", get(agent_memory_ingest))
        .route("/api/agent-memory/ingest", get(agent_memory_ingest))
        .route("/api/conversations", get(recent))
        .route("/api/conversations/{id}", get(conversation))
        .route("/api/projects", get(projects_handler))
        .route(
            "/api/favorites",
            get(favorites_list_handler).post(favorites_add_handler),
        )
        .route(
            "/api/favorites/{kind}/{ref}",
            delete(favorites_delete_handler),
        )
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
        .route("/api/assistants", get(assistants_handler))
        .route(
            "/api/assistants/file",
            get(assistants_file_handler).put(assistants_save_handler),
        )
        .route("/api/assistants/imported", get(assistants_imported_handler))
        .route(
            "/api/assistants/imported/{id}",
            get(assistants_imported_file_handler),
        )
        .route("/api/assistants/{tool}", get(assistants_tool_handler))
        .with_state(state)
        .layer(axum::middleware::from_fn_with_state(allowed, request_guard))
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

    fn loopback() -> Vec<String> {
        allowed_hosts(None, None)
    }

    #[test]
    fn plain_clients_and_loopback_names_pass() {
        let a = loopback();
        for host in [
            "127.0.0.1:8788",
            "localhost:8787",
            "LOCALHOST",
            "[::1]:8788",
        ] {
            assert_eq!(
                refuse_reason(Some(host), None, None, "/api/status", &a),
                None,
                "{host}"
            );
        }
        assert_eq!(refuse_reason(None, None, None, "/api/status", &a), None);
    }

    #[test]
    fn foreign_and_wildcard_hosts_are_refused() {
        let a = loopback();
        for host in [
            "evil.example:8788",
            "evil.example",
            "0.0.0.0:8788",
            "127.0.0.1.evil.example",
        ] {
            assert!(
                refuse_reason(Some(host), None, None, "/api/status", &a).is_some(),
                "{host}"
            );
        }
    }

    #[test]
    fn origin_must_match_the_host_exactly() {
        let a = loopback();
        let host = Some("127.0.0.1:8788");
        assert_eq!(
            refuse_reason(
                host,
                Some("http://127.0.0.1:8788"),
                Some("same-origin"),
                "/api/memory/1/forget",
                &a
            ),
            None
        );
        for origin in ["https://evil.example", "http://127.0.0.1:3000", "null"] {
            assert!(
                refuse_reason(host, Some(origin), None, "/api/memory/1/forget", &a).is_some(),
                "{origin}"
            );
        }
    }

    #[test]
    fn api_calls_from_other_sites_are_refused_but_the_page_opens() {
        let a = loopback();
        let host = Some("127.0.0.1:8788");
        for site in ["cross-site", "same-site"] {
            assert!(
                refuse_reason(host, None, Some(site), "/api/operate", &a).is_some(),
                "{site}"
            );
            assert_eq!(
                refuse_reason(host, None, Some(site), "/", &a),
                None,
                "{site}"
            );
        }
        assert_eq!(
            refuse_reason(host, None, Some("none"), "/api/status", &a),
            None
        );
        assert_eq!(
            refuse_reason(host, None, Some("same-origin"), "/api/status", &a),
            None
        );
    }

    #[test]
    fn public_url_and_extra_hosts_are_allowed() {
        let a = allowed_hosts(
            Some("https://Throughline.Example.org/app"),
            Some("mbp.local, 10.0.0.5"),
        );
        for host in [
            "throughline.example.org",
            "mbp.local:8788",
            "10.0.0.5:8788",
            "127.0.0.1:8788",
        ] {
            assert_eq!(
                refuse_reason(Some(host), None, None, "/api/status", &a),
                None,
                "{host}"
            );
        }
        assert_eq!(
            refuse_reason(
                Some("throughline.example.org"),
                Some("https://throughline.example.org"),
                Some("same-origin"),
                "/api/memory",
                &a
            ),
            None
        );
    }

    #[test]
    fn public_origin_passes_behind_a_proxy_that_rewrites_host() {
        // nginx without `proxy_set_header Host $host` forwards the upstream address.
        let a = allowed_hosts(Some("https://throughline.example.org"), None);
        let proxied = Some("127.0.0.1:8787");
        assert_eq!(
            refuse_reason(
                proxied,
                Some("https://throughline.example.org"),
                Some("same-origin"),
                "/api/memory",
                &a
            ),
            None
        );
        assert!(refuse_reason(
            proxied,
            Some("http://127.0.0.1:3000"),
            None,
            "/api/memory",
            &a
        )
        .is_some());
        assert!(refuse_reason(
            proxied,
            Some("https://evil.example"),
            None,
            "/api/memory",
            &a
        )
        .is_some());
    }

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

    #[test]
    fn keep_refuses_a_note_with_a_successor() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping keep_refuses_a_note_with_a_successor: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let a = seed_note(&mut tx, "keep-successor note A", "decision");
        let (_, b) = supersede_memory(&mut tx, a, &test_successor("keep-successor note B"))
            .expect("supersede");
        assert_eq!(
            keep_memory(&mut tx, a).expect("keep"),
            KeepOutcome::HasSuccessor
        );
        assert_eq!(validity(&mut tx, a).0, "superseded");
        assert_eq!(validity(&mut tx, b).0, "active");
    }

    #[test]
    fn keep_restores_a_forgotten_note() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping keep_restores_a_forgotten_note: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let a = seed_note(&mut tx, "keep-restore forgotten note", "decision");
        tx.execute(
            "UPDATE memory_chunks SET status = 'forgotten' WHERE id = $1",
            &[&a],
        )
        .expect("forget");
        assert_eq!(keep_memory(&mut tx, a).expect("keep"), KeepOutcome::Kept);
        assert_eq!(validity(&mut tx, a).0, "active");
    }

    #[test]
    fn keep_clears_the_expiry() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping keep_clears_the_expiry: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let a = seed_note(&mut tx, "keep-expiry stale note", "decision");
        tx.execute(
            "UPDATE memory_chunks SET status = 'stale', expires_at = $2 WHERE id = $1",
            &[&a, &at("2026-02-01T00:00:00Z")],
        )
        .expect("mark stale");
        assert_eq!(keep_memory(&mut tx, a).expect("keep"), KeepOutcome::Kept);
        let (status, expires_at): (String, Option<chrono::DateTime<chrono::Utc>>) = tx
            .query_one(
                "SELECT status, expires_at FROM memory_chunks WHERE id = $1",
                &[&a],
            )
            .map(|r| (r.get(0), r.get(1)))
            .expect("row");
        assert_eq!(status, "active");
        assert!(expires_at.is_none(), "keep must clear expires_at");
    }

    // ─── supersede ────────────────────────────────────────────────────────

    #[test]
    fn supersede_empty_content_is_422() {
        for empty in ["", "   ", "\n\t"] {
            let err = validate_supersede(empty, None).expect_err("empty content must be rejected");
            assert!(matches!(err, SupersedeError::EmptyContent), "{err:?}");
            let resp = supersede_error_response(err);
            assert_eq!(resp.status(), StatusCode::UNPROCESSABLE_ENTITY);
        }
    }

    #[test]
    fn supersede_unknown_category_is_422() {
        let err = validate_supersede("a durable note", Some("nope"))
            .expect_err("unknown category must be rejected");
        assert!(matches!(err, SupersedeError::InvalidCategory(_)), "{err:?}");
        assert_eq!(
            supersede_error_response(err).status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
    }

    #[test]
    fn supersede_accepts_known_categories_and_default() {
        validate_supersede("a durable note", None).expect("default category is allowed");
        for cat in MEMORY_CATEGORIES {
            validate_supersede("a durable note", Some(cat))
                .unwrap_or_else(|e| panic!("{cat} should be valid, got {e:?}"));
        }
    }

    #[test]
    fn supersede_error_status_mapping() {
        assert_eq!(
            supersede_error_response(SupersedeError::NotFound).status(),
            StatusCode::NOT_FOUND
        );
        assert_eq!(
            supersede_error_response(SupersedeError::AlreadySuperseded).status(),
            StatusCode::CONFLICT
        );
        assert_eq!(
            supersede_error_response(SupersedeError::EmptyContent).status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
    }

    #[test]
    fn memory_status_predicate_maps_filters() {
        let (active_pred, active_bind) = memory_status_predicate(None);
        assert!(active_pred.contains("active"), "{active_pred}");
        assert_eq!(active_bind.as_deref(), Some("active"));

        let (hist_pred, hist_bind) = memory_status_predicate(Some("history"));
        assert!(hist_pred.contains("superseded"), "{hist_pred}");
        assert_eq!(hist_bind.as_deref(), Some("superseded"));

        let (sup_pred, _) = memory_status_predicate(Some("superseded"));
        assert!(sup_pred.contains("superseded"), "{sup_pred}");

        let (all_pred, all_bind) = memory_status_predicate(Some("all"));
        assert_eq!(all_pred, "TRUE");
        assert!(all_bind.is_none());
    }

    // ─── order parameter mapping (criterion 1) ─────────────────────────────

    #[test]
    fn order_dir_none_defaults_to_desc() {
        assert_eq!(order_dir(None, true), "DESC");
    }

    #[test]
    fn order_dir_asc_is_case_insensitive() {
        assert_eq!(order_dir(Some("asc"), true), "ASC");
    }

    #[test]
    fn order_dir_desc_uppercase() {
        assert_eq!(order_dir(Some("DESC"), true), "DESC");
    }

    #[test]
    fn order_dir_invalid_falls_back_to_default() {
        assert_eq!(order_dir(Some("sideways"), false), "ASC");
    }

    #[test]
    fn order_dir_valid_value_overrides_default() {
        assert_eq!(order_dir(Some("desc"), false), "DESC");
        assert_eq!(order_dir(Some(""), true), "DESC");
        assert_eq!(order_dir(Some(" Asc "), false), "ASC");
    }

    // ─── timeline range validation (criterion 1) ───────────────────────────

    #[test]
    fn timeline_range_rejects_invalid_dates_and_inverted_bounds() {
        for (since, until) in [
            (Some("kaputt"), None),
            (None, Some("kaputt")),
            (Some("2026-13-01"), Some("2026-12-31")),
            (Some(""), None),
            (Some("2026-02-01"), Some("2026-01-01")),
        ] {
            let err = parse_timeline_range(since, until).expect_err("must be rejected");
            assert!(!err.is_empty(), "error message must explain the rejection");
        }
        // The inverted range names both bounds in the message.
        let err = parse_timeline_range(Some("2026-02-01"), Some("2026-01-01")).unwrap_err();
        assert!(
            err.contains("2026-02-01") && err.contains("2026-01-01"),
            "{err}"
        );
    }

    #[test]
    fn timeline_range_accepts_iso_pairs_and_open_bounds() {
        let jan_first = chrono::NaiveDate::from_ymd_opt(2026, 1, 1).unwrap();
        let jan_last = chrono::NaiveDate::from_ymd_opt(2026, 1, 31).unwrap();
        assert_eq!(
            parse_timeline_range(Some("2026-01-01"), Some("2026-01-31")).unwrap(),
            (Some(jan_first), Some(jan_last))
        );
        assert_eq!(
            parse_timeline_range(Some("2026-01-01"), None).unwrap(),
            (Some(jan_first), None)
        );
        assert_eq!(
            parse_timeline_range(None, Some("2026-01-31")).unwrap(),
            (None, Some(jan_last))
        );
        assert_eq!(parse_timeline_range(None, None).unwrap(), (None, None));
        // Same day twice is a valid one-day range.
        parse_timeline_range(Some("2026-01-01"), Some("2026-01-01")).unwrap();
    }

    #[test]
    fn timeline_handler_invalid_range_is_400_with_error_body() {
        let Some(client) = test_db() else {
            eprintln!("skipping timeline_handler_invalid_range: no PostgreSQL reachable");
            return;
        };
        // Connect outside any runtime (the sync postgres client blocks); the
        // 400 path never touches the database afterwards.
        let state = AppState {
            db: Arc::new(Mutex::new(client)),
        };
        let rt = tokio::runtime::Runtime::new().expect("tokio runtime");
        for params in [
            TimelineParams {
                since: Some("kaputt".into()),
                until: None,
                favorites: false,
            },
            TimelineParams {
                since: Some("2026-02-01".into()),
                until: Some("2026-01-01".into()),
                favorites: false,
            },
        ] {
            let resp = rt.block_on(timeline_handler(State(state.clone()), Query(params)));
            assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
            let body = rt
                .block_on(axum::body::to_bytes(resp.into_body(), 64 * 1024))
                .expect("body");
            let v: serde_json::Value = serde_json::from_slice(&body).expect("json body");
            assert!(
                v.get("error").is_some_and(|e| e.is_string()),
                "400 body must carry an error string: {v}"
            );
        }
    }

    // ─── timeline data (criteria 1 + 2) ────────────────────────────────────

    /// Seed helper: one conversation at literal `stamp` (RFC 3339) on `project_path`.
    fn seed_conversation<C: GenericClient>(
        client: &mut C,
        stamp: &str,
        project_path: &str,
        tool: &str,
    ) {
        let stamp: chrono::DateTime<chrono::Utc> = stamp.parse().expect("stamp is RFC 3339");
        client
            .execute(
                "INSERT INTO conversations (session_id, project_path, started_at, message_count, source_tool)
                 VALUES (gen_random_uuid(), $1, $2, 1, $3)",
                &[&project_path, &stamp, &tool],
            )
            .expect("seed conversation");
    }

    #[test]
    fn timeline_day_rows_can_keep_only_favorite_conversations() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping timeline favorites test: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let day = chrono::NaiveDate::from_ymd_opt(2031, 3, 3).unwrap();
        let mut ids = Vec::new();
        for _ in 0..2 {
            let id: i64 = tx
                .query_one(
                    "INSERT INTO conversations (session_id, project_path, started_at, source_tool) \
                     VALUES (gen_random_uuid(), '/fictional/tl-fav', '2031-03-03 12:00:00+00', 'fav-probe') \
                     RETURNING id",
                    &[],
                )
                .expect("seed conversation")
                .get(0);
            ids.push(id);
        }
        tx.execute(
            "INSERT INTO favorites (kind, ref) VALUES ('conversation', $1)",
            &[&ids[0].to_string()],
        )
        .expect("favorite one");
        let count = |rows: &[serde_json::Value]| -> i64 {
            rows.iter()
                .filter(|r| r["source_tool"] == "fav-probe")
                .map(|r| r["count"].as_i64().unwrap())
                .sum()
        };
        let all = timeline_day_rows(&mut tx, Some(day), Some(day), false).expect("all");
        let favs = timeline_day_rows(&mut tx, Some(day), Some(day), true).expect("favorites");
        assert_eq!(count(&all), 2);
        assert_eq!(count(&favs), 1, "only the starred conversation counts");
    }

    #[test]
    fn timeline_day_rows_filter_by_range_and_default_to_90_days() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping timeline_day_rows_filter_by_range: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        seed_conversation(
            &mut tx,
            "2026-01-15T12:00:00Z",
            "/fictional/tl-range",
            "tl_range",
        );
        seed_conversation(
            &mut tx,
            "2026-02-15T12:00:00Z",
            "/fictional/tl-range",
            "tl_range",
        );
        tx.execute(
            "INSERT INTO conversations (session_id, project_path, started_at, message_count, source_tool)
             VALUES (gen_random_uuid(), '/fictional/tl-range', now() - interval '10 days', 1, 'tl_range'),
                    (gen_random_uuid(), '/fictional/tl-range', now() - interval '200 days', 1, 'tl_range')",
            &[],
        )
        .expect("seed relative rows");

        // Expected day strings derived in SQL so the session timezone applies
        // exactly as in date_trunc('day', started_at).
        let mut day_of = |stamp: &str| -> String {
            tx.query_one(&format!("SELECT ({stamp})::date::text"), &[])
                .expect("day of stamp")
                .get(0)
        };
        let jan_day = day_of("TIMESTAMPTZ '2026-01-15 12:00:00+00'");
        let feb_day = day_of("TIMESTAMPTZ '2026-02-15 12:00:00+00'");
        let recent_day = day_of("now() - interval '10 days'");
        let old_day = day_of("now() - interval '200 days'");

        // since/until keep only January 2026 — and every returned day is in it.
        let jan = timeline_day_rows(
            &mut tx,
            Some(chrono::NaiveDate::from_ymd_opt(2026, 1, 1).unwrap()),
            Some(chrono::NaiveDate::from_ymd_opt(2026, 1, 31).unwrap()),
            false,
        )
        .expect("jan range");
        for row in &jan {
            let day = row["day"].as_str().unwrap();
            assert!(
                ("2026-01-01"..="2026-01-31").contains(&day),
                "day {day} is outside January 2026"
            );
        }
        assert!(
            jan.iter().any(|r| r["day"] == jan_day.as_str()),
            "seeded January day {jan_day} missing"
        );
        assert!(
            !jan.iter().any(|r| r["day"] == feb_day.as_str()),
            "February day {feb_day} leaked into the January range"
        );

        // No bounds: the classic rolling 90-day window.
        let default_rows = timeline_day_rows(&mut tx, None, None, false).expect("default window");
        assert!(
            default_rows.iter().any(|r| r["day"] == recent_day.as_str()),
            "10-day-old conversation belongs to the 90-day window"
        );
        assert!(
            !default_rows.iter().any(|r| r["day"] == old_day.as_str()),
            "200-day-old conversation is outside the 90-day window"
        );
        // Drop tx → rollback, nothing left behind.
    }

    #[test]
    fn timeline_day_rows_return_all_groups_beyond_limit_200() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping timeline_day_rows_beyond_200: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        // 240 distinct days × 5 tools = 240 day × tool groups (one conversation
        // per day). A LIMIT 200 would silently drop 40+ of them.
        tx.execute(
            r#"
            INSERT INTO conversations (session_id, project_path, started_at, message_count, source_tool)
            SELECT gen_random_uuid(), '/fictional/tl-many',
                   TIMESTAMPTZ '2020-01-01 12:00:00+00' + make_interval(days => n),
                   1, 'tl_many_' || (n % 5)
            FROM generate_series(0, 239) AS n
            "#,
            &[],
        )
        .expect("seed 240 rows");
        let rows = timeline_day_rows(
            &mut tx,
            Some(chrono::NaiveDate::from_ymd_opt(2020, 1, 1).unwrap()),
            Some(chrono::NaiveDate::from_ymd_opt(2021, 12, 31).unwrap()),
            false,
        )
        .expect("full window");
        let expected_days: Vec<String> = tx
            .query(
                "SELECT (TIMESTAMPTZ '2020-01-01 12:00:00+00' + make_interval(days => n))::date::text
                 FROM generate_series(0, 239) AS n",
                &[],
            )
            .expect("expected days")
            .iter()
            .map(|r| r.get(0))
            .collect();
        assert_eq!(expected_days.len(), 240, "240 seeded days");
        for day in &expected_days {
            assert!(
                rows.iter().any(|r| r["day"] == day.as_str()),
                "seeded day {day} missing — a truncation cap is active"
            );
        }
        assert!(
            rows.len() > 200,
            "more than 200 day × tool rows must come back, got {}",
            rows.len()
        );
        // Drop tx → rollback, nothing left behind.
    }

    // ─── projects (criterion 3) ────────────────────────────────────────────

    #[test]
    fn project_rows_expose_last_started_at_and_keep_existing_fields() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping project_rows_last_started_at: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        tx.execute(
            "INSERT INTO projects (name, description) VALUES
             ('tl-proj-active', 'project with conversations'),
             ('tl-proj-empty', NULL)",
            &[],
        )
        .expect("seed projects");
        seed_conversation(
            &mut tx,
            "2026-03-05T12:00:00Z",
            "/fictional/tl-proj-active",
            "tl_proj",
        );
        seed_conversation(
            &mut tx,
            "2026-04-09T12:00:00Z",
            "/fictional/tl-proj-active",
            "tl_proj",
        );

        let rows = project_rows(&mut tx, false).expect("project rows");
        for row in &rows {
            for field in ["name", "description", "conversations", "status"] {
                assert!(
                    row.get(field).is_some(),
                    "existing field {field} missing on {row}"
                );
            }
            assert!(
                row.get("last_started_at").is_some(),
                "last_started_at missing on {row}"
            );
        }
        let active = rows
            .iter()
            .find(|r| r["name"] == "tl-proj-active")
            .expect("project with conversations listed");
        assert_eq!(active["conversations"], 2);
        assert_eq!(active["description"], "project with conversations");
        assert!(active["status"].is_string(), "{active}");
        let iso = active["last_started_at"]
            .as_str()
            .expect("ISO-8601 string for project with conversations");
        assert!(
            chrono::DateTime::parse_from_rfc3339(iso).is_ok(),
            "last_started_at must be ISO-8601: {iso}"
        );
        assert!(
            iso.starts_with("2026-04-09"),
            "MAX(started_at) must win over the older conversation: {iso}"
        );
        let empty = rows
            .iter()
            .find(|r| r["name"] == "tl-proj-empty")
            .expect("project without conversations listed");
        assert_eq!(empty["conversations"], 0);
        assert!(
            empty["last_started_at"].is_null(),
            "null without conversations: {empty}"
        );
        // Drop tx → rollback, nothing left behind.
    }

    #[test]
    fn project_rows_favorite_filter_and_flag() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping project_rows_favorite_filter: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        tx.execute(
            "INSERT INTO projects (name, description) VALUES
             ('tl-fav-proj-a', 'fav A'),
             ('tl-fav-proj-b', 'fav B')",
            &[],
        )
        .expect("seed projects");
        crate::favorites::add(&mut tx, crate::favorites::FavKind::Project, "tl-fav-proj-a")
            .expect("seed favorite");

        let all = project_rows(&mut tx, false).expect("project rows");
        let a = all
            .iter()
            .find(|r| r["name"] == "tl-fav-proj-a")
            .expect("project a listed");
        let b = all
            .iter()
            .find(|r| r["name"] == "tl-fav-proj-b")
            .expect("project b listed");
        assert_eq!(
            a["favorite"].as_bool(),
            Some(true),
            "favorited project: {a}"
        );
        assert_eq!(b["favorite"].as_bool(), Some(false), "plain project: {b}");

        let only = project_rows(&mut tx, true).expect("favorite project rows");
        assert!(
            only.iter().any(|r| r["name"] == "tl-fav-proj-a"),
            "favorited project kept: {only:?}"
        );
        assert!(
            !only.iter().any(|r| r["name"] == "tl-fav-proj-b"),
            "plain project filtered out: {only:?}"
        );
        // Drop tx → rollback, nothing left behind.
    }

    /// Connect to the throwaway DB (`PG*` env). Skip when unreachable so
    /// offline `cargo test --lib` stays green.
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

    fn seed_note<C: GenericClient>(client: &mut C, content: &str, category: &str) -> i64 {
        let category_type =
            crate::extract_memory::category_column_type(client).expect("category_column_type");
        client
            .query_one(
                &format!(
                    "INSERT INTO memory_chunks (source_type, content, category, confidence, status)
                     VALUES ('manual', $1, $2::text::{category_type}, 1.0, 'active') RETURNING id"
                ),
                &[&content, &category],
            )
            .expect("seed note")
            .get(0)
    }

    fn test_successor(content: &str) -> Successor<'_> {
        Successor {
            content,
            category: None,
            source_type: "ui_pin",
            kind: SupersedeKind::Update,
            valid_from: None,
            valid_from_is_date: false,
            confidence: 1.0,
            tags: Vec::new(),
            source_at: None,
        }
    }

    fn at(s: &str) -> chrono::DateTime<chrono::Utc> {
        crate::temporal::parse_instant(s).expect("test instant")
    }

    type Instant = Option<chrono::DateTime<chrono::Utc>>;

    /// (status, valid_from, valid_to) of one note.
    fn validity<C: GenericClient>(c: &mut C, id: i64) -> (String, Instant, Instant) {
        let r = c
            .query_one(
                "SELECT status, valid_from, valid_to FROM memory_chunks WHERE id = $1",
                &[&id],
            )
            .expect("validity row");
        (r.get(0), r.get(1), r.get(2))
    }

    fn seed_valid_from<C: GenericClient>(c: &mut C, from: Option<&str>) -> i64 {
        let id = seed_note(c, "valid-time seed note", "decision");
        let vf = from.map(at);
        c.execute(
            "UPDATE memory_chunks SET valid_from = $2, source_at = $3 WHERE id = $1",
            &[&id, &vf, &at("2026-01-01T00:00:00Z")],
        )
        .expect("set valid_from");
        id
    }

    #[test]
    fn supersede_update_closes_the_old_note_at_the_new_start() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping supersede_update_closes: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let a = seed_valid_from(&mut tx, Some("2026-01-01T00:00:00Z"));
        let mut s = test_successor("update successor");
        s.valid_from = Some(at("2026-03-01T00:00:00Z"));
        let (_, b) = supersede_memory(&mut tx, a, &s).expect("update");
        let (st, _, to) = validity(&mut tx, a);
        assert_eq!(st, "superseded");
        assert_eq!(to, Some(at("2026-03-01T00:00:00Z")));
        let (_, from, to) = validity(&mut tx, b);
        assert_eq!(from, Some(at("2026-03-01T00:00:00Z")));
        assert_eq!(to, None);
    }

    #[test]
    fn supersede_correction_empties_the_old_interval() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping supersede_correction: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let a = seed_valid_from(&mut tx, Some("2026-01-01T00:00:00Z"));
        let mut s = test_successor("correction successor");
        s.kind = SupersedeKind::Correction;
        let (_, b) = supersede_memory(&mut tx, a, &s).expect("correction");
        let (_, from, _) = validity(&mut tx, b);
        assert_eq!(from, Some(at("2026-01-01T00:00:00Z")));
        let (_, a_from, a_to) = validity(&mut tx, a);
        assert_eq!(a_to, a_from, "old interval must be empty");
    }

    #[test]
    fn supersede_rejects_future_and_too_early_starts() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping supersede_rejects: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let a = seed_valid_from(&mut tx, Some("2026-01-01T00:00:00Z"));
        let mut s = test_successor("rejected successor");
        s.valid_from = Some(chrono::Utc::now() + chrono::Duration::days(1));
        let err = supersede_memory(&mut tx, a, &s).expect_err("future");
        assert!(matches!(err, SupersedeError::ValidFromInFuture), "{err:?}");
        assert_eq!(
            supersede_error_response(err).status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
        s.valid_from = Some(at("2025-12-31T00:00:00Z"));
        let err = supersede_memory(&mut tx, a, &s).expect_err("too early");
        assert!(
            matches!(err, SupersedeError::ValidFromBeforePredecessor),
            "{err:?}"
        );
        assert_eq!(
            supersede_error_response(err).status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
        let (st, _, to) = validity(&mut tx, a);
        assert_eq!(st, "active");
        assert_eq!(to, None);
        let n: i64 = tx
            .query_one(
                "SELECT COUNT(*)::bigint FROM memory_chunks WHERE content = 'rejected successor'",
                &[],
            )
            .expect("count")
            .get(0);
        assert_eq!(n, 0, "nothing may be written");
    }

    #[test]
    fn supersede_from_the_old_notes_first_day_starts_at_its_start() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping supersede_from_the_old_notes_first_day: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let today = chrono::Utc::now().date_naive();
        let start = today.and_hms_opt(10, 0, 0).unwrap().and_utc();
        let a = seed_valid_from(&mut tx, Some(&start.to_rfc3339()));
        let day = today.format("%Y-%m-%d").to_string();
        let mut s = test_successor("same-day successor");
        s.valid_from = crate::temporal::parse_instant(&day);
        // Control: the bare midnight lies before the old start and is refused.
        let err = supersede_memory(&mut tx, a, &s).expect_err("midnight before the start");
        assert!(
            matches!(err, SupersedeError::ValidFromBeforePredecessor),
            "{err:?}"
        );
        s.valid_from_is_date = crate::temporal::is_plain_date(&day);
        let (_, b) = supersede_memory(&mut tx, a, &s).expect("a plain date on the first day");
        assert_eq!(
            validity(&mut tx, b).1,
            Some(start),
            "starts at the old start"
        );
        assert_eq!(validity(&mut tx, a).2, Some(start));
    }

    #[test]
    fn supersede_note_without_valid_from() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping supersede_note_without_valid_from: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let a = seed_valid_from(&mut tx, None);
        let (_, b) = supersede_memory(&mut tx, a, &test_successor("successor of undated"))
            .expect("update of a note without valid_from");
        let (_, _, to) = validity(&mut tx, a);
        assert!(to.is_some(), "old note must be closed");
        assert!(validity(&mut tx, b).1.is_some());
    }

    #[test]
    fn supersede_moves_the_star() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping supersede_moves_the_star: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let a = seed_valid_from(&mut tx, Some("2026-01-01T00:00:00Z"));
        tx.execute(
            "INSERT INTO favorites (kind, ref) VALUES ('memory', $1)",
            &[&a.to_string()],
        )
        .expect("star");
        let (_, b) =
            supersede_memory(&mut tx, a, &test_successor("starred successor")).expect("supersede");
        let n = |tx: &mut postgres::Transaction<'_>, id: i64| -> i64 {
            tx.query_one(
                "SELECT COUNT(*)::bigint FROM favorites WHERE kind = 'memory' AND ref = $1",
                &[&id.to_string()],
            )
            .expect("count")
            .get(0)
        };
        assert_eq!(n(&mut tx, b), 1);
        assert_eq!(n(&mut tx, a), 0);
    }

    #[test]
    fn supersede_treats_merged_as_superseded() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping supersede_treats_merged: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let a = seed_valid_from(&mut tx, Some("2026-01-01T00:00:00Z"));
        tx.execute(
            "UPDATE memory_chunks SET status = 'merged' WHERE id = $1",
            &[&a],
        )
        .expect("merge");
        let err = supersede_memory(&mut tx, a, &test_successor("after merge")).expect_err("merged");
        assert!(matches!(err, SupersedeError::AlreadySuperseded), "{err:?}");
    }

    #[test]
    fn supersede_happy_path_updates_both_rows_in_one_transaction() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping supersede_happy_path: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let old_id = seed_note(
            &mut tx,
            "m10 supersede old: lexical retrieval stays the baseline.",
            "decision",
        );
        let (got_old, new_id) = supersede_memory(
            &mut tx,
            old_id,
            &test_successor("m10 supersede new: hybrid retrieval is the baseline now."),
        )
        .expect("supersede happy path");
        assert_eq!(got_old, old_id, "old_id must round-trip");
        assert_ne!(new_id, old_id, "new note needs a fresh id");

        let old_row = tx
            .query_one(
                "SELECT status, superseded_by, superseded_at, content FROM memory_chunks WHERE id = $1",
                &[&old_id],
            )
            .expect("old row");
        assert_eq!(old_row.get::<_, String>(0), "superseded");
        assert_eq!(old_row.get::<_, Option<i64>>(1), Some(new_id));
        assert!(
            old_row
                .get::<_, Option<chrono::DateTime<chrono::Utc>>>(2)
                .is_some(),
            "superseded_at must be stamped"
        );

        let new_row = tx
            .query_one(
                "SELECT status, category::text, content, superseded_by FROM memory_chunks WHERE id = $1",
                &[&new_id],
            )
            .expect("new row");
        assert_eq!(new_row.get::<_, String>(0), "active");
        // Category defaults to the predecessor's.
        assert_eq!(new_row.get::<_, String>(1), "decision");
        assert_eq!(
            new_row.get::<_, String>(2),
            "m10 supersede new: hybrid retrieval is the baseline now."
        );
        assert_eq!(new_row.get::<_, Option<i64>>(3), None);

        // Chain is visible both ways.
        let back = tx
            .query_one(
                "SELECT superseded_by FROM memory_chunks WHERE id = $1",
                &[&old_id],
            )
            .expect("back link");
        assert_eq!(back.get::<_, Option<i64>>(0), Some(new_id));

        // One transaction: exactly the two rows of this test exist for this pair.
        let count_before: i64 = tx
            .query_one(
                "SELECT COUNT(*)::bigint FROM memory_chunks WHERE id = ANY($1)",
                &[&vec![old_id, new_id]],
            )
            .expect("count")
            .get(0);
        assert_eq!(count_before, 2, "exactly the two rows of this test");

        // Drop tx → rollback, nothing left behind (prod-DB test policy:
        // no committed test rows).
    }

    #[test]
    fn supersede_unknown_id_is_404() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping supersede_unknown_id: no PostgreSQL reachable");
            return;
        };
        let err = supersede_memory(
            &mut client,
            999_999_999,
            &test_successor("orphan replacement"),
        )
        .expect_err("unknown id must fail");
        assert!(matches!(err, SupersedeError::NotFound), "{err:?}");
        assert_eq!(
            supersede_error_response(err).status(),
            StatusCode::NOT_FOUND
        );
    }

    #[test]
    fn supersede_twice_is_409() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping supersede_twice: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let old_id = seed_note(
            &mut tx,
            "m10 double-supersede target: first version of the fact.",
            "insight",
        );
        let (got_old, _new_id) = supersede_memory(
            &mut tx,
            old_id,
            &test_successor("m10 double-supersede: second version of the fact."),
        )
        .expect("first supersede");
        assert_eq!(got_old, old_id);

        let err = supersede_memory(
            &mut tx,
            old_id,
            &test_successor("m10 double-supersede: third version of the fact."),
        )
        .expect_err("second supersede must be rejected");
        assert!(matches!(err, SupersedeError::AlreadySuperseded), "{err:?}");
        assert_eq!(supersede_error_response(err).status(), StatusCode::CONFLICT);

        // The 409 must not have created another successor.
        let n: i64 = tx
            .query_one(
                "SELECT COUNT(*)::bigint FROM memory_chunks WHERE superseded_by IS NOT NULL AND id = $1",
                &[&old_id],
            )
            .expect("count")
            .get(0);
        assert_eq!(n, 1, "exactly one supersession recorded");
        // Drop tx → rollback, nothing left behind.
    }

    #[test]
    fn memory_create_insert_with_ui_pin_in_transaction() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping memory_create_insert: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let category_type =
            crate::extract_memory::category_column_type(&mut tx).expect("category_column_type");
        let row = tx
            .query_one(
                &format!(
                    r#"
                    INSERT INTO memory_chunks (source_type, content, category, project_name, confidence, status, source_at)
                    VALUES ('ui_pin', 'm21x test decision note', 'decision'::text::{category_type}, NULL, 1.0, 'active', now())
                    RETURNING id
                    "#
                ),
                &[],
            )
            .expect("insert with ui_pin");
        let id: i64 = row.get(0);
        assert!(id > 0, "inserted row must have an id");
        // Drop tx → rollback, nothing left behind.
    }

    /// The UI pre-fills "Replace" from the list. A shortened list text would
    /// replace a long note with its first lines.
    #[test]
    fn memory_list_returns_the_whole_note() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping memory_list_returns_the_whole_note: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let long = format!("{tag} {}", "a long note line. ".repeat(40));
        tx.execute(
            "INSERT INTO memory_chunks (source_type, content, category, status)
             VALUES ('manual', $1, 'decision', 'active')",
            &[&long],
        )
        .expect("seed note");
        let (_, rows) = list_memory_rows(
            &mut tx,
            Some(tag),
            None,
            None,
            false,
            10,
            0,
            "DESC",
            None,
            None,
        )
        .expect("list");
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].content.len(), long.len());
    }

    // ─── as-of queries (valid time / known time) ───────────────────────────

    /// Insert a note with a fixed valid_from; content is `{tag} {label}`.
    fn seed_dated<C: GenericClient>(client: &mut C, tag: &str, label: &str, from: &str) -> i64 {
        let category_type =
            crate::extract_memory::category_column_type(client).expect("category_column_type");
        let content = format!("{tag} {label}");
        let from = at(from);
        client
            .query_one(
                &format!(
                    "INSERT INTO memory_chunks (source_type, content, category, confidence, status, valid_from)
                     VALUES ('manual', $1, 'decision'::text::{category_type}, 1.0, 'active', $2) RETURNING id"
                ),
                &[&content, &from],
            )
            .expect("seed dated note")
            .get(0)
    }

    /// Ids that the list returns for `tag` at the given reference dates, sorted.
    fn listed_ids<C: GenericClient>(
        client: &mut C,
        tag: &str,
        valid_at: Instant,
        known_at: Instant,
    ) -> Vec<i64> {
        let (_, rows) = list_memory_rows(
            client,
            Some(tag.to_string()),
            None,
            None,
            false,
            50,
            0,
            "DESC",
            valid_at,
            known_at,
        )
        .expect("list");
        let mut ids: Vec<i64> = rows.iter().map(|r| r.id).collect();
        ids.sort();
        ids
    }

    fn update_to<C: GenericClient>(client: &mut C, old: i64, label: &str, from: &str) -> i64 {
        let tag_content = label.to_string();
        let mut s = test_successor(&tag_content);
        s.valid_from = Some(at(from));
        supersede_memory(client, old, &s).expect("update").1
    }

    #[test]
    fn as_of_update_chain() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping as_of_update_chain: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let a = seed_dated(&mut tx, &tag, "A", "2026-01-01");
        let b = update_to(&mut tx, a, &format!("{tag} B"), "2026-03-01");
        assert_eq!(
            listed_ids(&mut tx, &tag, Some(at("2026-02-15")), None),
            vec![a]
        );
        assert_eq!(
            listed_ids(&mut tx, &tag, Some(at("2026-03-15")), None),
            vec![b]
        );
        assert_eq!(listed_ids(&mut tx, &tag, None, None), vec![b]);
    }

    #[test]
    fn as_of_correction_chain() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping as_of_correction_chain: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let a = seed_dated(&mut tx, &tag, "A", "2026-01-01");
        let content = format!("{tag} B");
        let mut s = test_successor(&content);
        s.kind = SupersedeKind::Correction;
        let b = supersede_memory(&mut tx, a, &s).expect("correct").1;
        // inside one transaction now() is constant: set the order of events by hand
        tx.execute(
            "UPDATE memory_chunks SET created_at = '2026-01-01T00:00:00Z' WHERE id = $1",
            &[&a],
        )
        .unwrap();
        tx.execute(
            "UPDATE memory_chunks SET superseded_at = '2026-06-01T00:00:00Z' WHERE id = $1",
            &[&a],
        )
        .unwrap();
        tx.execute(
            "UPDATE memory_chunks SET created_at = '2026-06-01T00:00:00Z' WHERE id = $1",
            &[&b],
        )
        .unwrap();
        assert_eq!(
            listed_ids(&mut tx, &tag, Some(at("2026-02-15")), None),
            vec![b]
        );
        let before = Some(at("2026-03-01"));
        assert_eq!(listed_ids(&mut tx, &tag, None, before), vec![a]);
        // what we knew in March about February: the old note, still believed
        assert_eq!(
            listed_ids(&mut tx, &tag, Some(at("2026-02-15")), before),
            vec![a]
        );
    }

    #[test]
    fn backdated_update_never_overlaps() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping backdated_update_never_overlaps: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let a = seed_dated(&mut tx, &tag, "A", "2026-01-01");
        let b = update_to(&mut tx, a, &format!("{tag} B"), "2026-02-01");
        let mut day = at("2026-01-01");
        let end = at("2026-04-30");
        while day <= end {
            let ids = listed_ids(&mut tx, &tag, Some(day), None);
            assert!(ids.len() <= 1, "overlap on {day}: {ids:?}");
            assert_eq!(
                ids,
                vec![if day < at("2026-02-01") { a } else { b }],
                "{day}"
            );
            day += chrono::Duration::days(1);
        }
    }

    #[test]
    fn as_of_respects_expiry_and_forgetting() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping as_of_respects_expiry: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let c = seed_dated(&mut tx, &tag, "C", "2026-01-01");
        let d = seed_dated(&mut tx, &tag, "D", "2026-01-01");
        tx.execute(
            "UPDATE memory_chunks SET expires_at = '2026-02-01T00:00:00Z' WHERE id = $1",
            &[&c],
        )
        .unwrap();
        tx.execute(
            "UPDATE memory_chunks SET status = 'forgotten' WHERE id = $1",
            &[&d],
        )
        .unwrap();
        assert_eq!(
            listed_ids(&mut tx, &tag, Some(at("2026-01-15")), None),
            vec![c]
        );
        assert!(listed_ids(&mut tx, &tag, Some(at("2026-02-15")), None).is_empty());
        assert!(!listed_ids(&mut tx, &tag, None, Some(at("2026-09-01"))).contains(&d));
        // without a reference date the list keeps its status filter: D is gone, C stays
        assert_eq!(listed_ids(&mut tx, &tag, None, None), vec![c]);
    }

    #[test]
    fn as_of_a_plain_date_covers_the_whole_day() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping as_of_a_plain_date_covers_the_whole_day: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let a = seed_dated(&mut tx, &tag, "A", "2026-01-01");
        let b = update_to(&mut tx, a, &format!("{tag} B"), "2026-03-01");
        let day = |s: &str| parse_as_of(Some(s), None).expect("valid_at").0;
        assert_eq!(listed_ids(&mut tx, &tag, day("2026-02-15"), None), vec![a]);
        assert_eq!(listed_ids(&mut tx, &tag, day("2026-03-15"), None), vec![b]);
        // A note made now counts for today, on both axes.
        let fresh = seed_note(&mut tx, &format!("{tag} fresh"), "decision");
        let today = chrono::Utc::now().format("%Y-%m-%d").to_string();
        let (valid_at, known_at) = parse_as_of(Some(&today), Some(&today)).expect("today");
        assert!(
            listed_ids(&mut tx, &tag, valid_at, None).contains(&fresh),
            "valid_at={today} must include a note made today"
        );
        assert!(
            listed_ids(&mut tx, &tag, None, known_at).contains(&fresh),
            "known_at={today} must include a note made today"
        );
    }

    #[test]
    fn supersede_keeps_a_forgotten_note_forgotten() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping supersede_keeps_a_forgotten_note_forgotten: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let a = seed_dated(&mut tx, &tag, "A", "2026-01-01");
        tx.execute(
            "UPDATE memory_chunks SET status = 'forgotten' WHERE id = $1",
            &[&a],
        )
        .unwrap();
        let b = update_to(&mut tx, a, &format!("{tag} B"), "2026-03-01");
        let (st, _, to) = validity(&mut tx, a);
        assert_eq!(st, "forgotten", "a forgotten predecessor stays forgotten");
        assert_eq!(to, Some(at("2026-03-01")));
        let by: Option<i64> = tx
            .query_one(
                "SELECT superseded_by FROM memory_chunks WHERE id = $1",
                &[&a],
            )
            .unwrap()
            .get(0);
        assert_eq!(by, Some(b));
        assert_eq!(validity(&mut tx, b).0, "active");
        let now = Some(chrono::Utc::now());
        assert!(listed_ids(&mut tx, &tag, Some(at("2026-02-15")), None).is_empty());
        assert_eq!(listed_ids(&mut tx, &tag, now, None), vec![b]);
        assert_eq!(listed_ids(&mut tx, &tag, None, now), vec![b]);
        let (_, history) = list_memory_rows(
            &mut tx,
            Some(tag),
            None,
            Some("history".into()),
            false,
            50,
            0,
            "DESC",
            None,
            None,
        )
        .expect("history");
        assert!(
            history.iter().all(|r| r.id != a),
            "a forgotten note stays out of History"
        );
    }

    #[test]
    fn as_of_combines_with_filters() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping as_of_combines_with_filters: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let keep = seed_dated(&mut tx, &tag, "keep", "2026-01-01");
        let other_cat = seed_dated(&mut tx, &tag, "other", "2026-01-01");
        let not_fav = seed_dated(&mut tx, &tag, "plain", "2026-01-01");
        let late = seed_dated(&mut tx, &tag, "late", "2026-05-01");
        let category_type = crate::extract_memory::category_column_type(&mut tx).unwrap();
        tx.execute(
            &format!(
                "UPDATE memory_chunks SET category = 'insight'::text::{category_type} WHERE id = $1"
            ),
            &[&other_cat],
        )
        .unwrap();
        for id in [keep, other_cat, late] {
            tx.execute(
                "INSERT INTO favorites (kind, ref) VALUES ('memory', $1)",
                &[&id.to_string()],
            )
            .unwrap();
        }
        let _ = not_fav;
        let (_, rows) = list_memory_rows(
            &mut tx,
            Some(tag),
            Some("decision".into()),
            None,
            true,
            50,
            0,
            "DESC",
            Some(at("2026-02-15")),
            None,
        )
        .expect("list");
        let ids: Vec<i64> = rows.iter().map(|r| r.id).collect();
        assert_eq!(ids, vec![keep]);
    }

    #[test]
    fn memory_list_rejects_bad_dates() {
        let err = parse_as_of(Some("gestern"), None).expect_err("bad valid_at");
        assert_eq!(err.0, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(err.1, "validation_error");
        assert!(err.2.contains("valid_at"), "{}", err.2);
        let err = parse_as_of(None, Some("2026-02-30")).expect_err("bad known_at");
        assert_eq!(err.1, "validation_error");
        assert!(err.2.contains("known_at"), "{}", err.2);
        let ok = parse_as_of(Some("2026-03-01"), Some("  ")).expect("ok");
        assert!(ok.0.is_some() && ok.1.is_none());
    }
}
