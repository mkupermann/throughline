//! Cline (VS Code `saoudrizwan.claude-dev` extension tasks) adapter.

use super::{stable_uuid_str, Adapter};
use crate::types::{NormalisedConversation, NormalisedMessage};
use anyhow::Result;
use chrono::{DateTime, TimeZone, Utc};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use uuid::Uuid;

const NS: Uuid = Uuid::from_bytes([
    0x8a, 0x7b, 0x6c, 0x5d, 0x4e, 0x3f, 0x4a, 0x2b, 0x9c, 0x8d, 0x7e, 0x6f, 0x5a, 0x4b, 0x3c, 0x2d,
]);

pub struct ClineAdapter {
    homes: Vec<PathBuf>,
}

impl Default for ClineAdapter {
    fn default() -> Self {
        let home = dirs::home_dir().unwrap_or_default();
        Self {
            homes: vec![
                home.join("Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/tasks"),
                home.join("Library/Application Support/Cursor/User/globalStorage/saoudrizwan.claude-dev/tasks"),
                home.join(".cline/data/tasks"),
                home.join(".cline/tasks"),
                home.join(".config/Code/User/globalStorage/saoudrizwan.claude-dev/tasks"),
                home.join("AppData/Roaming/Code/User/globalStorage/saoudrizwan.claude-dev/tasks"),
            ],
        }
    }
}

impl ClineAdapter {
    pub fn new(homes: Vec<PathBuf>) -> Self {
        Self { homes }
    }

    fn first_existing(&self) -> Option<PathBuf> {
        self.homes.iter().find(|p| p.exists()).cloned()
    }
}

fn parse_ts_ms(value: &Value) -> Option<DateTime<Utc>> {
    if let Some(n) = value.as_f64() {
        return Utc.timestamp_opt((n / 1000.0) as i64, 0).single();
    }
    if let Some(s) = value.as_str() {
        if let Ok(dt) = DateTime::parse_from_rfc3339(&s.replace('Z', "+00:00")) {
            return Some(dt.with_timezone(&Utc));
        }
    }
    None
}

fn extract_content(content: &Value) -> String {
    if let Some(s) = content.as_str() {
        return s.to_string();
    }
    if let Some(list) = content.as_array() {
        let mut parts = Vec::new();
        for block in list {
            if let Some(s) = block.as_str() {
                parts.push(s.to_string());
            } else {
                match block.get("type").and_then(|t| t.as_str()) {
                    Some("text") => parts.push(
                        block
                            .get("text")
                            .and_then(|t| t.as_str())
                            .unwrap_or_default()
                            .to_string(),
                    ),
                    Some("tool_use") => {
                        let name = block.get("name").and_then(|n| n.as_str()).unwrap_or("?");
                        parts.push(format!("[Tool: {name}]"));
                    }
                    Some("tool_result") => {
                        let tc = block.get("content").cloned().unwrap_or(Value::Null);
                        let s = if let Some(s) = tc.as_str() {
                            s.chars().take(500).collect::<String>()
                        } else {
                            tc.to_string()
                        };
                        parts.push(s);
                    }
                    _ => {}
                }
            }
        }
        return parts.join("\n");
    }
    String::new()
}

fn role_of(msg: &Value) -> &'static str {
    match msg.get("role").and_then(|r| r.as_str()).unwrap_or("user") {
        "assistant" => "assistant",
        "system" => "system",
        "tool" | "function" => "tool_result",
        _ => {
            // tool_result blocks inside a user message
            if let Some(list) = msg.get("content").and_then(|c| c.as_array()) {
                if list
                    .iter()
                    .any(|b| b.get("type").and_then(|t| t.as_str()) == Some("tool_result"))
                {
                    return "tool_result";
                }
            }
            "user"
        }
    }
}

impl Adapter for ClineAdapter {
    fn name(&self) -> &str {
        "cline"
    }

    fn home(&self) -> PathBuf {
        self.first_existing()
            .unwrap_or_else(|| self.homes[0].clone())
    }

    fn is_present(&self) -> bool {
        self.first_existing().is_some()
    }

    fn discover(&self) -> Result<Vec<PathBuf>> {
        let Some(root) = self.first_existing() else {
            return Ok(Vec::new());
        };
        let mut out = Vec::new();
        for task_dir in std::fs::read_dir(&root)? {
            let task_dir = task_dir?.path();
            if !task_dir.is_dir() {
                continue;
            }
            for name in ["api_conversation_history.json", "ui_messages.json"] {
                let p = task_dir.join(name);
                if p.is_file() {
                    out.push(p);
                    break;
                }
            }
        }
        out.sort();
        Ok(out)
    }

    fn parse(&self, path: &Path) -> Result<Option<NormalisedConversation>> {
        let raw = std::fs::read_to_string(path)?;
        let rows: Vec<Value> = serde_json::from_str(&raw)?;
        if rows.is_empty() {
            return Ok(None);
        }
        let task_dir = path.parent().map(|p| p.to_path_buf());
        let task_id = task_dir
            .as_ref()
            .and_then(|p| p.file_name())
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_default();
        let session_id = stable_uuid_str(&NS, Some(&task_id), "cline:session");

        let meta: Value = task_dir
            .as_ref()
            .map(|d| {
                std::fs::read_to_string(d.join("task_metadata.json"))
                    .ok()
                    .and_then(|s| serde_json::from_str(&s).ok())
                    .unwrap_or(Value::Null)
            })
            .unwrap_or(Value::Null);

        // Prefer the task's own timestamp, then the history file's mtime.
        // Never stamp ingest time as the session date — that made every
        // re-import look like it happened "today".
        let started = parse_ts_ms(meta.get("ts").unwrap_or(&Value::Null))
            .or_else(|| {
                path.metadata()
                    .ok()
                    .and_then(|m| m.modified().ok())
                    .map(DateTime::<Utc>::from)
            })
            .or_else(|| {
                task_dir.as_ref().and_then(|d| {
                    d.metadata()
                        .ok()
                        .and_then(|m| m.modified().ok())
                        .map(DateTime::<Utc>::from)
                })
            })
            .unwrap_or_else(Utc::now);
        let mut messages = Vec::new();
        for (idx, msg) in rows.iter().enumerate() {
            let uuid = stable_uuid_str(&NS, None, &format!("cline:{task_id}:msg:{idx}"));
            messages.push(NormalisedMessage {
                role: role_of(msg).to_string(),
                content: extract_content(msg.get("content").unwrap_or(&Value::Null)),
                created_at: Some(started),
                uuid: Some(uuid),
                metadata: json!({}),
                ..Default::default()
            });
        }

        Ok(Some(NormalisedConversation {
            session_id,
            project_path: Some("cline".into()),
            model: None,
            entrypoint: Some("cline".into()),
            git_branch: None,
            started_at: started,
            ended_at: Some(started),
            messages,
            token_count_in: None,
            token_count_out: None,
            summary: meta
                .get("task")
                .and_then(|t| t.as_str())
                .map(|s| s.chars().take(200).collect()),
            metadata: json!({"source": "cline"}),
            source_tool: Some("cline".into()),
        }))
    }
}
