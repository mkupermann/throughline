//! Continue.dev (`~/.continue/sessions`) adapter — both historical layouts.

use super::{stable_uuid_str, Adapter};
use crate::types::{NormalisedConversation, NormalisedMessage};
use anyhow::Result;
use chrono::{DateTime, TimeZone, Utc};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use uuid::Uuid;

const NS: Uuid = Uuid::from_bytes([
    0x5b, 0x3f, 0x2e, 0x8d, 0x7a, 0x4c, 0x4d, 0x9b, 0x9c, 0x1e, 0x8f, 0x7a, 0x6d, 0x5c, 0x4b, 0x3e,
]);

pub struct ContinueAdapter {
    home: PathBuf,
}

impl Default for ContinueAdapter {
    fn default() -> Self {
        Self {
            home: dirs::home_dir()
                .unwrap_or_default()
                .join(".continue/sessions"),
        }
    }
}

impl ContinueAdapter {
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

fn stringify(content: &Value) -> (String, Option<Value>) {
    if content.is_null() {
        return (String::new(), None);
    }
    if let Some(s) = content.as_str() {
        return (s.to_string(), None);
    }
    if let Some(list) = content.as_array() {
        let mut parts = Vec::new();
        for block in list {
            if let Some(s) = block.as_str() {
                parts.push(s.to_string());
            } else if block.get("type").and_then(|t| t.as_str()) == Some("text") {
                parts.push(
                    block
                        .get("text")
                        .and_then(|t| t.as_str())
                        .unwrap_or_default()
                        .to_string(),
                );
            } else if let Some(s) = block.get("content").and_then(|c| c.as_str()) {
                parts.push(s.to_string());
            }
        }
        return (parts.join("\n"), Some(content.clone()));
    }
    (
        serde_json::to_string(content)
            .unwrap_or_default()
            .chars()
            .take(2000)
            .collect(),
        None,
    )
}

fn session_from_dict(d: &Value, source_path: &Path) -> Option<NormalisedConversation> {
    let raw_session_id = d
        .get("sessionId")
        .or_else(|| d.get("id"))
        .or_else(|| d.get("title"))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| {
            source_path
                .file_stem()
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or_default()
        });
    let history = d
        .get("history")
        .or_else(|| d.get("messages"))
        .and_then(|h| h.as_array())?;
    if history.is_empty() {
        return None;
    }
    let mut messages = Vec::new();
    for (idx, turn) in history.iter().enumerate() {
        let role = match turn
            .get("role")
            .and_then(|r| r.as_str())
            .unwrap_or("user")
            .to_lowercase()
            .as_str()
        {
            "assistant" => "assistant",
            "system" => "system",
            "tool" | "function" => "tool_result",
            _ => "user",
        };
        let (content, blocks) = stringify(turn.get("content").unwrap_or(&Value::Null));
        let uuid = stable_uuid_str(&NS, None, &format!("continue:{raw_session_id}:{idx}"));
        messages.push(NormalisedMessage {
            role: role.to_string(),
            content,
            content_blocks: blocks,
            created_at: parse_ts(turn.get("timestamp").unwrap_or(&Value::Null)),
            uuid: Some(uuid),
            metadata: json!({}),
            ..Default::default()
        });
    }
    let started = history
        .first()
        .and_then(|t| parse_ts(t.get("timestamp").unwrap_or(&Value::Null)))
        .unwrap_or_else(Utc::now);
    Some(NormalisedConversation {
        session_id: stable_uuid_str(&NS, Some(&raw_session_id), "continue:session"),
        project_path: d
            .get("workspaceDirectory")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .or_else(|| Some("continue".into())),
        model: d
            .get("model")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string()),
        entrypoint: Some("continue".into()),
        git_branch: None,
        started_at: started,
        ended_at: history
            .last()
            .and_then(|t| parse_ts(t.get("timestamp").unwrap_or(&Value::Null))),
        messages,
        token_count_in: None,
        token_count_out: None,
        summary: d
            .get("title")
            .and_then(|t| t.as_str())
            .map(|s| s.to_string()),
        metadata: json!({"source": "continue_dev"}),
        source_tool: Some("continue_dev".into()),
    })
}

impl Adapter for ContinueAdapter {
    fn name(&self) -> &str {
        "continue_dev"
    }
    fn home(&self) -> PathBuf {
        self.home.clone()
    }

    fn discover(&self) -> Result<Vec<PathBuf>> {
        if !self.home.exists() {
            return Ok(Vec::new());
        }
        let mut out = Vec::new();
        for entry in walkdir::WalkDir::new(&self.home)
            .into_iter()
            .filter_map(|e| e.ok())
        {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) == Some("json") {
                out.push(path.to_path_buf());
            }
        }
        out.sort();
        Ok(out)
    }

    fn parse(&self, path: &Path) -> Result<Option<NormalisedConversation>> {
        let raw = std::fs::read_to_string(path)?;
        let value: Value = serde_json::from_str(&raw)?;
        // sessions.json is an array; per-session files are objects.
        if let Some(arr) = value.as_array() {
            // Legacy multi-session file: return the first non-empty session
            // (the writer handles one conversation per parse call).
            for item in arr {
                if let Some(c) = session_from_dict(item, path) {
                    return Ok(Some(c));
                }
            }
            return Ok(None);
        }
        Ok(session_from_dict(&value, path))
    }
}
