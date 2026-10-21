//! OpenAI Codex (`~/.codex/sessions/rollout-*.jsonl`) adapter.

use super::{stable_uuid_str, Adapter};
use crate::types::{NormalisedConversation, NormalisedMessage};
use anyhow::Result;
use chrono::{DateTime, TimeZone, Utc};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use uuid::Uuid;

const NS: Uuid = Uuid::from_bytes([
    0x0c, 0x7f, 0x9a, 0x3a, 0x1d, 0x8b, 0x4b, 0x2e, 0x9c, 0x4a, 0x1b, 0x2d, 0x3e, 0x4f, 0x5a, 0x6b,
]);

pub struct CodexAdapter {
    home: PathBuf,
}

impl Default for CodexAdapter {
    fn default() -> Self {
        Self {
            home: dirs::home_dir().unwrap_or_default().join(".codex/sessions"),
        }
    }
}

impl CodexAdapter {
    pub fn new(home: PathBuf) -> Self {
        Self { home }
    }
}

fn parse_ts(value: &Value) -> Option<DateTime<Utc>> {
    if value.is_null() {
        return None;
    }
    if let Some(n) = value.as_f64() {
        let n = if n > 1_000_000_000_000.0 { n / 1000.0 } else { n };
        return Utc.timestamp_opt(n as i64, 0).single();
    }
    if let Some(s) = value.as_str() {
        if let Ok(dt) = DateTime::parse_from_rfc3339(&s.replace('Z', "+00:00")) {
            return Some(dt.with_timezone(&Utc));
        }
    }
    None
}

fn stringify(content: &Value) -> String {
    if content.is_null() {
        return String::new();
    }
    if let Some(s) = content.as_str() {
        return s.to_string();
    }
    if let Some(list) = content.as_array() {
        let mut parts = Vec::new();
        for block in list {
            match block.get("type").and_then(|t| t.as_str()) {
                Some("text") | Some("input_text") | Some("output_text") => {
                    parts.push(
                        block
                            .get("text")
                            .and_then(|t| t.as_str())
                            .unwrap_or_default()
                            .to_string(),
                    );
                }
                Some("input_image") | Some("encrypted_content") => {}
                _ => {
                    let s = serde_json::to_string(block).unwrap_or_default();
                    parts.push(s.chars().take(500).collect());
                }
            }
        }
        return parts.join("\n");
    }
    let s = serde_json::to_string(content).unwrap_or_default();
    s.chars().take(2000).collect()
}

impl Adapter for CodexAdapter {
    fn name(&self) -> &str {
        "codex"
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
                if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
                    if name.starts_with("rollout-") {
                        out.push(path.to_path_buf());
                    }
                }
            }
        }
        out.sort();
        Ok(out)
    }

    fn parse(&self, path: &Path) -> Result<Option<NormalisedConversation>> {
        let raw = std::fs::read_to_string(path)?;
        let mut events: Vec<Value> = Vec::new();
        for line in raw.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Ok(v) = serde_json::from_str::<Value>(line) {
                events.push(v);
            }
        }
        if events.is_empty() {
            return Ok(None);
        }

        let mut meta_event = Value::Null;
        let mut meta_outer = Value::Null;
        let mut meta_is_nested = false;
        for ev in &events {
            let t = ev.get("type").and_then(|v| v.as_str()).unwrap_or("");
            if matches!(t, "session_meta" | "session" | "config") {
                meta_outer = ev.clone();
                if let Some(payload) = ev.get("payload").filter(|p| p.is_object()) {
                    meta_is_nested = true;
                    meta_event = payload.clone();
                } else {
                    meta_event = ev.clone();
                }
                break;
            }
        }
        if meta_event.is_null() {
            meta_outer = events[0].clone();
            meta_event = events[0].clone();
        }

        let mut turn_context = Value::Null;
        for ev in &events {
            if ev.get("type").and_then(|v| v.as_str()) == Some("turn_context") {
                if let Some(p) = ev.get("payload").filter(|p| p.is_object()) {
                    turn_context = p.clone();
                    break;
                }
            }
        }

        let get = |v: &Value, key: &str| v.get(key).and_then(|x| x.as_str()).map(|s| s.to_string());

        let raw_session_id = if meta_is_nested {
            get(&meta_event, "id")
                .or_else(|| get(&meta_event, "session_id"))
                .unwrap_or_else(|| {
                    path.file_stem()
                        .map(|s| s.to_string_lossy().to_string())
                        .unwrap_or_default()
                })
        } else {
            get(&meta_event, "session_id")
                .or_else(|| get(&meta_event, "id"))
                .unwrap_or_else(|| {
                    path.file_stem()
                        .map(|s| s.to_string_lossy().to_string())
                        .unwrap_or_default()
                })
        };
        let session_id = stable_uuid_str(&NS, Some(&raw_session_id), "codex:session");

        let model = get(&meta_event, "model")
            .or_else(|| get(&meta_event, "model_name"))
            .or_else(|| get(&turn_context, "model"));
        let cwd = get(&meta_event, "cwd")
            .or_else(|| get(&meta_event, "working_directory"))
            .or_else(|| get(&turn_context, "cwd"));
        let project_path = Some(cwd.unwrap_or_else(|| "codex".into()));

        let started = parse_ts(
            meta_event
                .get("started_at")
                .or_else(|| meta_event.get("timestamp"))
                .unwrap_or(&Value::Null),
        )
        .or_else(|| parse_ts(meta_outer.get("timestamp").unwrap_or(&Value::Null)))
        .or_else(|| parse_ts(events[0].get("timestamp").unwrap_or(&Value::Null)))
        .unwrap_or_else(Utc::now);
        let ended = parse_ts(meta_event.get("ended_at").unwrap_or(&Value::Null))
            .or_else(|| parse_ts(events.last().unwrap().get("timestamp").unwrap_or(&Value::Null)))
            .unwrap_or(started);

        let mut messages = Vec::new();
        let mut tool_names: HashMap<String, String> = HashMap::new();

        for (idx, ev) in events.iter().enumerate() {
            let ev_type = ev
                .get("type")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_lowercase();
            let ts = parse_ts(ev.get("timestamp").unwrap_or(&Value::Null)).unwrap_or(started);
            let uuid = stable_uuid_str(
                &NS,
                Some(&format!("codex:{raw_session_id}:msg:{idx}")),
                "codex:msg",
            );

            if ev_type == "response_item" {
                let Some(payload) = ev.get("payload").filter(|p| p.is_object()) else {
                    continue;
                };
                let item_type = payload
                    .get("type")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_lowercase();
                match item_type.as_str() {
                    "message" => {
                        let role = payload
                            .get("role")
                            .and_then(|v| v.as_str())
                            .unwrap_or("user")
                            .to_string();
                        if role != "user" && role != "assistant" {
                            continue;
                        }
                        let blocks = payload.get("content").cloned();
                        let content = stringify(blocks.as_ref().unwrap_or(&Value::Null));
                        let model_for_msg = get(payload, "model").or_else(|| {
                            (role == "assistant").then(|| model.clone()).flatten()
                        });
                        messages.push(NormalisedMessage {
                            role,
                            content,
                            content_blocks: blocks,
                            created_at: Some(ts),
                            model: model_for_msg,
                            uuid: Some(uuid),
                            metadata: json!({}),
                            ..Default::default()
                        });
                    }
                    "agent_message" => {
                        let blocks = payload.get("content").cloned();
                        let content = stringify(blocks.as_ref().unwrap_or(&Value::Null));
                        messages.push(NormalisedMessage {
                            role: "assistant".into(),
                            content,
                            content_blocks: blocks,
                            created_at: Some(ts),
                            model: model.clone(),
                            is_sidechain: true,
                            uuid: Some(uuid),
                            metadata: json!({}),
                            ..Default::default()
                        });
                    }
                    "custom_tool_call" | "function_call" => {
                        let name = get(payload, "name").unwrap_or_else(|| "?".into());
                        if let Some(call_id) = get(payload, "call_id") {
                            tool_names.insert(call_id, name.clone());
                        }
                        let args = payload
                            .get("input")
                            .cloned()
                            .or_else(|| payload.get("arguments").cloned())
                            .unwrap_or(Value::Null);
                        messages.push(NormalisedMessage {
                            role: "assistant".into(),
                            content: format!("[Tool: {name}] {}", stringify(&args).chars().take(500).collect::<String>()),
                            tool_calls: Some(json!([{ "tool_name": name, "input": args }])),
                            tool_name: Some(name),
                            created_at: Some(ts),
                            model: model.clone(),
                            uuid: Some(uuid),
                            metadata: json!({}),
                            ..Default::default()
                        });
                    }
                    "custom_tool_call_output" | "function_call_output" => {
                        let call_id = get(payload, "call_id").unwrap_or_default();
                        let output = payload
                            .get("output")
                            .or_else(|| payload.get("content"))
                            .cloned()
                            .unwrap_or(Value::Null);
                        messages.push(NormalisedMessage {
                            role: "tool_result".into(),
                            content: stringify(&output),
                            tool_name: tool_names.get(&call_id).cloned(),
                            created_at: Some(ts),
                            uuid: Some(uuid),
                            metadata: json!({}),
                            ..Default::default()
                        });
                    }
                    _ => {}
                }
            } else if ev_type == "user_message" {
                // Legacy flat CLI format
                let content = stringify(ev.get("message").unwrap_or(&Value::Null));
                messages.push(NormalisedMessage {
                    role: "user".into(),
                    content,
                    created_at: Some(ts),
                    uuid: Some(uuid),
                    metadata: json!({}),
                    ..Default::default()
                });
            } else if ev_type == "assistant_message" {
                let content = stringify(ev.get("message").unwrap_or(&Value::Null));
                messages.push(NormalisedMessage {
                    role: "assistant".into(),
                    content,
                    created_at: Some(ts),
                    model: get(ev, "model").or_else(|| model.clone()),
                    uuid: Some(uuid),
                    metadata: json!({}),
                    ..Default::default()
                });
            }
        }

        if messages.is_empty() {
            return Ok(None);
        }

        Ok(Some(NormalisedConversation {
            session_id,
            project_path,
            model,
            entrypoint: Some("codex".into()),
            git_branch: get(&meta_event, "git_branch"),
            started_at: started,
            ended_at: Some(ended),
            messages,
            token_count_in: meta_event
                .get("tokens_used")
                .and_then(|v| v.as_i64())
                .map(|v| v as i64),
            token_count_out: None,
            summary: None,
            metadata: json!({"source": "codex"}),
            source_tool: Some("codex".into()),
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn parses_rollout_response_items() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("rollout-1.jsonl");
        let mut f = std::fs::File::create(&path).unwrap();
        writeln!(
            f,
            r#"{{"type":"session_meta","timestamp":"2026-01-02T10:00:00Z","payload":{{"id":"abc","cwd":"/work/proj","model":"gpt-5"}}}}"#
        )
        .unwrap();
        writeln!(
            f,
            r#"{{"type":"response_item","timestamp":"2026-01-02T10:00:01Z","payload":{{"type":"message","role":"user","content":[{{"type":"text","text":"hello codex"}}]}}}}"#
        )
        .unwrap();
        writeln!(
            f,
            r#"{{"type":"response_item","timestamp":"2026-01-02T10:00:02Z","payload":{{"type":"message","role":"assistant","content":[{{"type":"text","text":"hi"}}]}}}}"#
        )
        .unwrap();
        let adapter = CodexAdapter::new(tmp.path().to_path_buf());
        let conv = adapter.parse(&path).unwrap().expect("parsed");
        assert_eq!(conv.project_path.as_deref(), Some("/work/proj"));
        assert_eq!(conv.messages.len(), 2);
        assert_eq!(conv.messages[0].role, "user");
        assert_eq!(conv.messages[0].content, "hello codex");
    }

    #[test]
    fn discover_finds_rollouts() {
        let tmp = tempfile::tempdir().unwrap();
        let nested = tmp.path().join("2026-01-02");
        std::fs::create_dir_all(&nested).unwrap();
        std::fs::write(nested.join("rollout-x.jsonl"), "{}\n").unwrap();
        std::fs::write(nested.join("other.jsonl"), "{}\n").unwrap();
        let adapter = CodexAdapter::new(tmp.path().to_path_buf());
        let found = adapter.discover().unwrap();
        assert_eq!(found.len(), 1);
    }
}
