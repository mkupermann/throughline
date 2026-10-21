//! Zed (`~/.zed/data/sessions`) adapter.

use super::{stable_uuid_str, Adapter};
use crate::types::{NormalisedConversation, NormalisedMessage};
use anyhow::Result;
use chrono::{DateTime, TimeZone, Utc};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use uuid::Uuid;

const NS: Uuid = Uuid::from_bytes([
    0x8f, 0x4e, 0x2d, 0x1c, 0x7b, 0x9a, 0x4e, 0x3d, 0x8c, 0x6b, 0x1f, 0x2e, 0x3d, 0x4c, 0x5b, 0x6a,
]);

pub struct ZedAdapter {
    home: PathBuf,
}

impl Default for ZedAdapter {
    fn default() -> Self {
        Self {
            home: dirs::home_dir().unwrap_or_default().join(".zed/data/sessions"),
        }
    }
}

impl ZedAdapter {
    pub fn new(home: PathBuf) -> Self {
        Self { home }
    }
}

fn parse_ts(value: &Value) -> Option<DateTime<Utc>> {
    if value.is_null() {
        return None;
    }
    if let Some(n) = value.as_f64() {
        // Zed uses ms since epoch
        let secs = if n > 1e12 { (n / 1000.0) as i64 } else { n as i64 };
        return Utc.timestamp_opt(secs, 0).single();
    }
    if let Some(s) = value.as_str() {
        if let Ok(dt) = DateTime::parse_from_rfc3339(&s.replace('Z', "+00:00")) {
            return Some(dt.with_timezone(&Utc));
        }
    }
    None
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

impl Adapter for ZedAdapter {
    fn name(&self) -> &str {
        "zed"
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
            if matches!(
                path.extension().and_then(|e| e.to_str()),
                Some("json") | Some("jsonl")
            ) {
                out.push(path.to_path_buf());
            }
        }
        out.sort();
        Ok(out)
    }

    fn parse(&self, path: &Path) -> Result<Option<NormalisedConversation>> {
        let raw = std::fs::read_to_string(path)?;
        // Zed writes either JSONL messages or a single JSON document.
        let mut rows: Vec<Value> = Vec::new();
        if let Ok(v) = serde_json::from_str::<Value>(&raw) {
            if let Some(msgs) = v.get("messages").and_then(|m| m.as_array()) {
                rows = msgs.clone();
            } else if v.get("role").is_some() {
                rows = vec![v];
            }
        } else {
            for line in raw.lines() {
                if line.trim().is_empty() {
                    continue;
                }
                if let Ok(v) = serde_json::from_str::<Value>(line) {
                    rows.push(v);
                }
            }
        }
        if rows.is_empty() {
            return Ok(None);
        }

        let session_raw = rows[0]
            .get("session_id")
            .or_else(|| rows[0].get("id"))
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| {
                path.file_stem()
                    .map(|s| s.to_string_lossy().to_string())
                    .unwrap_or_default()
            });
        let session_id = stable_uuid_str(&NS, Some(&session_raw), "zed:session");

        let mut messages = Vec::new();
        let mut started = None;
        let mut ended = None;
        let mut model = None;

        for (idx, msg) in rows.iter().enumerate() {
            if model.is_none() {
                model = msg.get("model").and_then(|v| v.as_str()).map(|s| s.to_string());
            }
            if let Some(ts) = parse_ts(msg.get("timestamp").unwrap_or(&Value::Null)) {
                started = Some(started.map_or(ts, |s: DateTime<Utc>| s.min(ts)));
                ended = Some(ended.map_or(ts, |s: DateTime<Utc>| s.max(ts)));
            }
            let role = msg
                .get("role")
                .and_then(|r| r.as_str())
                .unwrap_or("user")
                .to_string();
            let role = match role.to_lowercase().as_str() {
                "assistant" => "assistant",
                "system" => "system",
                "tool" | "tool_result" => "tool_result",
                _ => "user",
            };
            let mid = msg.get("message_id").or_else(|| msg.get("id")).and_then(|v| v.as_str());
            let uuid = stable_uuid_str(&NS, mid, &format!("zed:{session_raw}:msg:{idx}"));
            messages.push(NormalisedMessage {
                role: role.to_string(),
                content: extract_content(msg),
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
            project_path: Some("zed".into()),
            model,
            entrypoint: Some("zed".into()),
            git_branch: None,
            started_at,
            ended_at: Some(ended.unwrap_or(started_at)),
            messages,
            token_count_in: None,
            token_count_out: None,
            summary: None,
            metadata: json!({"source": "zed"}),
            source_tool: Some("zed".into()),
        }))
    }
}
