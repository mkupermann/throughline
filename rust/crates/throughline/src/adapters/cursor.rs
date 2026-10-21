//! Cursor (`~/.cursor/sessions`) JSONL adapter.

use super::{stable_uuid_str, Adapter};
use crate::types::{NormalisedConversation, NormalisedMessage};
use anyhow::Result;
use chrono::{DateTime, TimeZone, Utc};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use uuid::Uuid;

const NS: Uuid = Uuid::from_bytes([
    0x5e, 0x3f, 0x7a, 0x8b, 0x2c, 0x1d, 0x4e, 0x9f, 0x8a, 0x6b, 0x0c, 0x5d, 0x2e, 0x4f, 0x3a, 0x7b,
]);

pub struct CursorAdapter {
    home: PathBuf,
}

impl Default for CursorAdapter {
    fn default() -> Self {
        Self {
            home: dirs::home_dir().unwrap_or_default().join(".cursor/sessions"),
        }
    }
}

impl CursorAdapter {
    pub fn new(home: PathBuf) -> Self {
        Self { home }
    }
}

fn parse_ts(value: &Value) -> Option<DateTime<Utc>> {
    if value.is_null() {
        return None;
    }
    if let Some(n) = value.as_f64() {
        return Utc.timestamp_opt(n as i64, 0).single();
    }
    if let Some(s) = value.as_str() {
        if let Ok(dt) = DateTime::parse_from_rfc3339(&s.replace('Z', "+00:00")) {
            return Some(dt.with_timezone(&Utc));
        }
    }
    None
}

fn map_role(role: Option<&str>) -> &'static str {
    match role.map(|r| r.to_lowercase()).as_deref() {
        Some("assistant") => "assistant",
        Some("system") => "system",
        Some("tool") | Some("tool_result") => "tool_result",
        _ => "user",
    }
}

fn extract_content(message: &Value) -> String {
    let content = message.get("content").unwrap_or(&Value::Null);
    if let Some(s) = content.as_str() {
        return s.chars().take(10_000).collect();
    }
    if let Some(list) = content.as_array() {
        let mut parts = Vec::new();
        for block in list {
            if let Some(s) = block.as_str() {
                parts.push(s.to_string());
            } else if let Some(t) = block.get("type").and_then(|t| t.as_str()) {
                if t == "text" || t == "code" {
                    parts.push(
                        block
                            .get("text")
                            .and_then(|t| t.as_str())
                            .unwrap_or_default()
                            .to_string(),
                    );
                }
            }
        }
        return parts.join("\n").chars().take(10_000).collect();
    }
    String::new()
}

impl Adapter for CursorAdapter {
    fn name(&self) -> &str {
        "cursor"
    }
    fn home(&self) -> PathBuf {
        self.home.clone()
    }

    fn discover(&self) -> Result<Vec<PathBuf>> {
        if !self.home.exists() {
            return Ok(Vec::new());
        }
        let mut out = Vec::new();
        for entry in walkdir::WalkDir::new(&self.home).into_iter().filter_map(|e| e.ok()) {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) == Some("jsonl") {
                out.push(path.to_path_buf());
            }
        }
        out.sort();
        Ok(out)
    }

    fn parse(&self, path: &Path) -> Result<Option<NormalisedConversation>> {
        let raw = std::fs::read_to_string(path)?;
        let mut rows: Vec<Value> = Vec::new();
        for line in raw.lines() {
            if line.trim().is_empty() {
                continue;
            }
            if let Ok(v) = serde_json::from_str::<Value>(line) {
                rows.push(v);
            }
        }
        if rows.is_empty() {
            return Ok(None);
        }
        let first = &rows[0];
        let session_hash = first
            .get("session_hash")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| {
                path.file_stem()
                    .map(|s| s.to_string_lossy().to_string())
                    .unwrap_or_default()
            });
        let session_id = stable_uuid_str(&NS, Some(&session_hash), "cursor:session");

        let mut model = None;
        let mut started = None;
        let mut ended = None;
        let mut messages = Vec::new();

        for (idx, msg) in rows.iter().enumerate() {
            if model.is_none() {
                model = msg.get("model").and_then(|v| v.as_str()).map(|s| s.to_string());
            }
            if let Some(ts) = parse_ts(msg.get("timestamp").unwrap_or(&Value::Null)) {
                started = Some(started.map_or(ts, |s: DateTime<Utc>| s.min(ts)));
                ended = Some(ended.map_or(ts, |s: DateTime<Utc>| s.max(ts)));
            }
            let mid = msg.get("message_id").and_then(|v| v.as_str());
            let uuid = stable_uuid_str(
                &NS,
                mid,
                &format!("cursor:{session_hash}:msg:{idx}"),
            );
            let content = extract_content(msg);
            let tool_calls = msg.get("tool_calls").filter(|t| t.is_array()).cloned();
            let tool_name = tool_calls
                .as_ref()
                .and_then(|t| t.get(0))
                .and_then(|c| c.get("function"))
                .and_then(|f| f.get("name"))
                .and_then(|n| n.as_str())
                .map(|s| s.to_string());
            messages.push(NormalisedMessage {
                role: map_role(msg.get("role").and_then(|r| r.as_str())).to_string(),
                content,
                tool_calls,
                tool_name,
                created_at: parse_ts(msg.get("timestamp").unwrap_or(&Value::Null)),
                model: msg.get("model").and_then(|m| m.as_str()).map(|s| s.to_string()),
                uuid: Some(uuid),
                metadata: json!({}),
                ..Default::default()
            });
        }

        if messages.is_empty() {
            return Ok(None);
        }
        let started_at = started.unwrap_or_else(Utc::now);
        Ok(Some(NormalisedConversation {
            session_id,
            project_path: Some("cursor".into()),
            model,
            entrypoint: Some("cursor".into()),
            git_branch: None,
            started_at,
            ended_at: Some(ended.unwrap_or(started_at)),
            messages,
            token_count_in: None,
            token_count_out: None,
            summary: None,
            metadata: json!({"source": "cursor", "cursor_session_hash": session_hash}),
            source_tool: Some("cursor".into()),
        }))
    }
}
