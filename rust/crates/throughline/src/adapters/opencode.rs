//! OpenCode (`~/.local/share/opencode`, `~/.opencode`) session adapter.

use super::{stable_uuid_str, Adapter};
use crate::types::{NormalisedConversation, NormalisedMessage};
use anyhow::Result;
use chrono::{DateTime, TimeZone, Utc};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use uuid::Uuid;

const NS: Uuid = Uuid::from_bytes([
    0x2a, 0x4c, 0x6e, 0x8f, 0xa0, 0xb1, 0xc2, 0xd3, 0xe4, 0xf5, 0x06, 0x17, 0x28, 0x39, 0x4a, 0x5b,
]);

pub struct OpenCodeAdapter {
    home: PathBuf,
}

impl Default for OpenCodeAdapter {
    fn default() -> Self {
        let home = dirs::home_dir().unwrap_or_default();
        let candidates = [
            home.join(".local/share/opencode"),
            home.join(".opencode"),
            home.join("AppData/Local/opencode"),
        ];
        let home = candidates
            .into_iter()
            .find(|p| p.exists())
            .unwrap_or_else(|| home.join(".local/share/opencode"));
        Self { home }
    }
}

impl OpenCodeAdapter {
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

impl Adapter for OpenCodeAdapter {
    fn name(&self) -> &str {
        "opencode"
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
            .max_depth(6)
            .into_iter()
            .filter_map(|e| e.ok())
        {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }
            let is_session = path
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| e == "json" || e == "jsonl")
                .unwrap_or(false)
                || path
                    .file_name()
                    .and_then(|n| n.to_str())
                    .map(|n| n.contains("session") || n.contains("message") || n.contains("part"))
                    .unwrap_or(false);
            if is_session {
                out.push(path.to_path_buf());
            }
        }
        out.sort();
        out.dedup();
        Ok(out)
    }

    fn parse(&self, path: &Path) -> Result<Option<NormalisedConversation>> {
        let raw = std::fs::read_to_string(path)?;
        let mut rows: Vec<Value> = Vec::new();
        if let Ok(v) = serde_json::from_str::<Value>(&raw) {
            if let Some(arr) = v.as_array() {
                rows = arr.clone();
            } else if v.get("messages").is_some() || v.get("parts").is_some() || v.get("role").is_some()
            {
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

        // OpenCode storage layout: {id, role, content} or {type:"message", ...}
        let mut messages = Vec::new();
        let mut session_raw = path
            .file_stem()
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_default();
        let mut started = None;
        let mut ended = None;

        for (idx, msg) in rows.iter().enumerate() {
            if session_raw.is_empty() {
                if let Some(id) = msg.get("sessionID").or_else(|| msg.get("session_id")) {
                    if let Some(s) = id.as_str() {
                        session_raw = s.to_string();
                    }
                }
            }
            let role = msg
                .get("role")
                .and_then(|r| r.as_str())
                .unwrap_or("user")
                .to_lowercase();
            let role = match role.as_str() {
                "assistant" | "ai" | "model" => "assistant",
                "system" => "system",
                "tool" | "tool_result" | "tool-result" => "tool_result",
                _ => "user",
            };
            let content = msg
                .get("content")
                .or_else(|| msg.get("text"))
                .and_then(|c| {
                    if let Some(s) = c.as_str() {
                        Some(s.to_string())
                    } else if let Some(list) = c.as_array() {
                        Some(
                            list.iter()
                                .filter_map(|b| {
                                    b.get("text")
                                        .and_then(|t| t.as_str())
                                        .map(|s| s.to_string())
                                        .or_else(|| b.as_str().map(|s| s.to_string()))
                                })
                                .collect::<Vec<_>>()
                                .join("\n"),
                        )
                    } else {
                        None
                    }
                })
                .unwrap_or_default();
            if content.trim().is_empty() && role != "tool_result" {
                continue;
            }
            if let Some(ts) = parse_ts(
                msg.get("time")
                    .or_else(|| msg.get("created_at"))
                    .or_else(|| msg.get("timestamp"))
                    .unwrap_or(&Value::Null),
            ) {
                started = Some(started.map_or(ts, |s: DateTime<Utc>| s.min(ts)));
                ended = Some(ended.map_or(ts, |s: DateTime<Utc>| s.max(ts)));
            }
            let mid = msg.get("id").and_then(|v| v.as_str());
            messages.push(NormalisedMessage {
                role: role.to_string(),
                content,
                created_at: parse_ts(
                    msg.get("time")
                        .or_else(|| msg.get("created_at"))
                        .unwrap_or(&Value::Null),
                ),
                uuid: Some(stable_uuid_str(
                    &NS,
                    mid,
                    &format!("opencode:{session_raw}:{idx}"),
                )),
                metadata: json!({}),
                ..Default::default()
            });
        }

        if messages.is_empty() {
            return Ok(None);
        }
        let started_at = started.unwrap_or_else(Utc::now);
        Ok(Some(NormalisedConversation {
            session_id: stable_uuid_str(&NS, Some(&session_raw), "opencode:session"),
            project_path: Some("opencode".into()),
            model: None,
            entrypoint: Some("opencode".into()),
            git_branch: None,
            started_at,
            ended_at: Some(ended.unwrap_or(started_at)),
            messages,
            token_count_in: None,
            token_count_out: None,
            summary: None,
            metadata: json!({"source": "opencode"}),
            source_tool: Some("opencode".into()),
        }))
    }
}
