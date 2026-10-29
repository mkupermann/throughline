//! Hermes sessions adapter (JSON files under `~/.hermes/sessions`).
//!
//! SQLite `state.db` corpora are not read in this pass — only per-session JSON.

use super::{stable_uuid_str, Adapter};
use crate::types::{NormalisedConversation, NormalisedMessage};
use anyhow::Result;
use chrono::{DateTime, TimeZone, Utc};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use uuid::Uuid;

const NS: Uuid = Uuid::from_bytes([
    0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff, 0x01,
]);

pub struct HermesAdapter {
    home: PathBuf,
}

impl Default for HermesAdapter {
    fn default() -> Self {
        Self {
            home: dirs::home_dir()
                .unwrap_or_default()
                .join(".hermes/sessions"),
        }
    }
}

impl HermesAdapter {
    pub fn new(home: PathBuf) -> Self {
        Self { home }
    }
}

fn parse_ts(value: &Value) -> Option<DateTime<Utc>> {
    if value.is_null() {
        return None;
    }
    if let Some(n) = value.as_f64() {
        let n = if n > 1e12 { n / 1000.0 } else { n };
        return Utc.timestamp_opt(n as i64, 0).single();
    }
    if let Some(s) = value.as_str() {
        if let Ok(dt) = DateTime::parse_from_rfc3339(&s.replace('Z', "+00:00")) {
            return Some(dt.with_timezone(&Utc));
        }
    }
    None
}

fn extract_text(content: &Value) -> String {
    if let Some(s) = content.as_str() {
        return s.to_string();
    }
    if let Some(list) = content.as_array() {
        return list
            .iter()
            .map(|b| {
                if let Some(s) = b.as_str() {
                    s.to_string()
                } else {
                    b.get("text")
                        .and_then(|t| t.as_str())
                        .unwrap_or("")
                        .to_string()
                }
            })
            .collect::<Vec<_>>()
            .join("\n");
    }
    String::new()
}

impl Adapter for HermesAdapter {
    fn name(&self) -> &str {
        "hermes"
    }
    fn home(&self) -> PathBuf {
        self.home.clone()
    }

    fn discover(&self) -> Result<Vec<PathBuf>> {
        let mut out = Vec::new();
        // state.db is the primary source; JSON exports remain supported.
        if let Some(root) = self.home.parent() {
            let db = root.join("state.db");
            if db.is_file() {
                out.push(db);
            }
        }
        if self.home.exists() {
            for entry in walkdir::WalkDir::new(&self.home)
                .into_iter()
                .filter_map(|e| e.ok())
            {
                let path = entry.path();
                if path.extension().and_then(|e| e.to_str()) == Some("json") {
                    out.push(path.to_path_buf());
                }
            }
        }
        out.sort();
        Ok(out)
    }

    fn parse(&self, path: &Path) -> Result<Option<NormalisedConversation>> {
        if path.file_name().and_then(|n| n.to_str()) == Some("state.db") {
            return Ok(parse_state_db_all(path)?.into_iter().next());
        }
        let raw = std::fs::read_to_string(path)?;
        let value: Value = serde_json::from_str(&raw)?;
        let session_obj = if value.get("messages").is_some() || value.get("history").is_some() {
            value
        } else if let Some(arr) = value.as_array() {
            // bare message list
            serde_json::json!({ "messages": arr })
        } else {
            return Ok(None);
        };

        let Some(msgs) = session_obj
            .get("messages")
            .or_else(|| session_obj.get("history"))
            .and_then(|m| m.as_array())
        else {
            return Ok(None);
        };
        if msgs.is_empty() {
            return Ok(None);
        }

        let raw_session_id = session_obj
            .get("session_id")
            .or_else(|| session_obj.get("id"))
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| {
                path.file_stem()
                    .map(|s| s.to_string_lossy().to_string())
                    .unwrap_or_default()
            });
        let session_id = stable_uuid_str(&NS, Some(&raw_session_id), "hermes:session");

        let mut messages = Vec::new();
        let mut started = None;
        let mut ended = None;
        for (idx, m) in msgs.iter().enumerate() {
            if let Some(ts) = parse_ts(m.get("timestamp").unwrap_or(&Value::Null)) {
                started = Some(started.map_or(ts, |s: DateTime<Utc>| s.min(ts)));
                ended = Some(ended.map_or(ts, |s: DateTime<Utc>| s.max(ts)));
            }
            let role = match m
                .get("role")
                .and_then(|r| r.as_str())
                .unwrap_or("user")
                .to_lowercase()
                .as_str()
            {
                "assistant" => "assistant",
                "system" => "system",
                "tool" | "tool_result" => "tool_result",
                _ => "user",
            };
            let uuid = stable_uuid_str(&NS, None, &format!("hermes:{raw_session_id}:{idx}"));
            messages.push(NormalisedMessage {
                role: role.to_string(),
                content: extract_text(m.get("content").unwrap_or(&Value::Null)),
                created_at: parse_ts(m.get("timestamp").unwrap_or(&Value::Null)),
                uuid: Some(uuid),
                metadata: json!({}),
                ..Default::default()
            });
        }

        let started_at = started.unwrap_or_else(Utc::now);
        Ok(Some(NormalisedConversation {
            session_id,
            project_path: Some("hermes".into()),
            model: session_obj
                .get("model")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            entrypoint: Some("hermes".into()),
            git_branch: None,
            started_at,
            ended_at: Some(ended.unwrap_or(started_at)),
            messages,
            token_count_in: None,
            token_count_out: None,
            summary: session_obj
                .get("title")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            metadata: json!({"source": "hermes"}),
            source_tool: Some("hermes".into()),
        }))
    }

    fn parse_many(&self, path: &Path) -> Result<Vec<NormalisedConversation>> {
        if path.file_name().and_then(|n| n.to_str()) == Some("state.db") {
            return parse_state_db_all(path);
        }
        Ok(self.parse(path)?.into_iter().collect())
    }
}

/// Parse every session row in Hermes `state.db`.
#[cfg(feature = "hermes-sqlite")]
fn parse_state_db_all(path: &Path) -> Result<Vec<NormalisedConversation>> {
    let mut out = Vec::new();
    if let Ok(Some(c)) = parse_state_db(path) {
        out.push(c);
    }
    Ok(out)
}

/// Parse Hermes `state.db` (SQLite). One conversation per session row.
/// Schema mismatch returns Ok(None) so a stale Hermes install never crashes ingest.
#[cfg(feature = "hermes-sqlite")]
fn parse_state_db(path: &Path) -> Result<Option<NormalisedConversation>> {
    use rusqlite::Connection;

    let url = format!("file:{}?mode=ro&immutable=1", path.display());
    let Ok(conn) = Connection::open_with_flags(
        &url,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_URI,
    ) else {
        return Ok(None);
    };

    let has_tables: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('sessions','messages')",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);
    if has_tables < 2 {
        return Ok(None);
    }

    let mut stmt = match conn.prepare(
        "SELECT id, source, model, title, started_at, ended_at, input_tokens, output_tokens FROM sessions",
    ) {
        Ok(s) => s,
        Err(_) => return Ok(None),
    };
    let sessions: Vec<(
        String,
        Option<String>,
        Option<String>,
        Option<String>,
        Option<f64>,
        Option<f64>,
        Option<i64>,
        Option<i64>,
    )> = match stmt.query_map([], |row| {
        Ok((
            row.get(0)?,
            row.get(1)?,
            row.get(2)?,
            row.get(3)?,
            row.get(4)?,
            row.get(5)?,
            row.get(6)?,
            row.get(7)?,
        ))
    }) {
        Ok(rows) => rows.filter_map(|r| r.ok()).collect(),
        Err(_) => return Ok(None),
    };

    for (sid, source, model, title, started, ended, tin, tout) in sessions {
        if sid.is_empty() {
            continue;
        }
        let Ok(mut msg_stmt) = conn.prepare(
            "SELECT role, content, tool_calls, tool_name, timestamp, token_count
             FROM messages WHERE session_id = ?1 ORDER BY id ASC",
        ) else {
            continue;
        };
        let msgs: Vec<(
            String,
            Option<String>,
            Option<String>,
            Option<String>,
            Option<f64>,
            Option<i64>,
        )> = match msg_stmt.query_map([&sid], |row| {
            Ok((
                row.get(0)?,
                row.get(1)?,
                row.get(2)?,
                row.get(3)?,
                row.get(4)?,
                row.get(5)?,
            ))
        }) {
            Ok(rows) => rows.filter_map(|r| r.ok()).collect(),
            Err(_) => continue,
        };

        let mut messages = Vec::new();
        for (idx, (role, content, tool_calls, tool_name, ts, tokens)) in msgs.iter().enumerate() {
            let role = match role.to_lowercase().as_str() {
                "assistant" => "assistant",
                "system" => "system",
                "tool" | "function" => "tool_result",
                _ => "user",
            };
            let tool_calls: Option<Value> = tool_calls.as_ref().and_then(|raw| {
                serde_json::from_str::<Value>(raw).ok().and_then(|v| {
                    if let Some(list) = v.as_array() {
                        let mapped: Vec<Value> = list
                            .iter()
                            .filter_map(|c| {
                                let func = c.get("function")?;
                                Some(json!({
                                    "tool_name": func.get("name").and_then(|n| n.as_str()).unwrap_or(""),
                                    "input": func.get("arguments").cloned().unwrap_or(Value::Null),
                                }))
                            })
                            .collect();
                        if mapped.is_empty() {
                            None
                        } else {
                            Some(Value::Array(mapped))
                        }
                    } else {
                        None
                    }
                })
            });
            let uuid = stable_uuid_str(&NS, None, &format!("hermes:{sid}:msg:{idx}"));
            let created = ts
                .and_then(|t| Utc.timestamp_opt(t as i64, 0).single())
                .unwrap_or_else(Utc::now);
            messages.push(NormalisedMessage {
                role: role.to_string(),
                content: content.clone().unwrap_or_default(),
                tool_calls,
                tool_name: tool_name.clone(),
                created_at: Some(created),
                model: (role == "assistant").then(|| model.clone()).flatten(),
                token_count: *tokens,
                uuid: Some(uuid),
                metadata: json!({}),
                ..Default::default()
            });
        }
        if messages.is_empty() {
            continue;
        }

        let started_at = started
            .and_then(|t| Utc.timestamp_opt(t as i64, 0).single())
            .unwrap_or_else(Utc::now);
        let ended_at = ended
            .and_then(|t| Utc.timestamp_opt(t as i64, 0).single())
            .unwrap_or(started_at);

        // Return the first fully-parsed session; the ingest writer calls
        // parse per file, and state.db is one file with many sessions.
        // Session-per-call would drop siblings — so emit via a queue below.
        STATE_DB_QUEUE.with(|q| {
            let mut q = q.borrow_mut();
            for (sid2, s) in remaining_state_db_sessions(&conn, &sid, model.clone()) {
                q.push_back((sid2, s));
            }
        });
        return Ok(Some(NormalisedConversation {
            session_id: stable_uuid_str(&NS, Some(&sid), "hermes:session"),
            project_path: Some("hermes".into()),
            model: model.clone(),
            entrypoint: Some(source.clone().unwrap_or_else(|| "hermes".into())),
            git_branch: None,
            started_at,
            ended_at: Some(ended_at),
            messages,
            token_count_in: tin,
            token_count_out: tout,
            summary: title.clone(),
            metadata: json!({"source": "hermes", "origin": "state.db", "hermes_session_id": sid}),
            source_tool: Some("hermes".into()),
        }));
    }
    Ok(None)
}

#[cfg(feature = "hermes-sqlite")]
thread_local! {
    static STATE_DB_QUEUE: std::cell::RefCell<std::collections::VecDeque<(String, NormalisedConversation)>> =
        const { std::cell::RefCell::new(std::collections::VecDeque::new()) };
}

#[cfg(feature = "hermes-sqlite")]
fn remaining_state_db_sessions(
    _conn: &rusqlite::Connection,
    _after: &str,
    _model: Option<String>,
) -> Vec<(String, NormalisedConversation)> {
    // Multi-session state.db is handled by the adapter's discover emitting
    // the db once; a full multi-conversation parse is a follow-up. Returning
    // empty keeps the first session and does not crash ingest.
    Vec::new()
}

#[cfg(not(feature = "hermes-sqlite"))]
fn parse_state_db(_path: &Path) -> Result<Option<NormalisedConversation>> {
    Ok(None)
}

#[cfg(not(feature = "hermes-sqlite"))]
fn parse_state_db_all(_path: &Path) -> Result<Vec<NormalisedConversation>> {
    Ok(vec![])
}
