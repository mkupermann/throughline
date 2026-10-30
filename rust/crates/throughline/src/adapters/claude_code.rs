//! Claude Code (`~/.claude/projects/<slug>/*.jsonl`) adapter.
//!
//! Mirrors the Python adapter's contract: subagent transcripts and title-generator
//! sessions are excluded; `cwd` wins over the hyphen-mangled directory name.

use super::Adapter;
use crate::types::{NormalisedConversation, NormalisedMessage};
use anyhow::Result;
use chrono::{DateTime, Utc};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use uuid::Uuid;

pub struct ClaudeCodeAdapter {
    home: PathBuf,
}

impl Default for ClaudeCodeAdapter {
    fn default() -> Self {
        Self::new(crate::config::claude_projects_dir())
    }
}

impl ClaudeCodeAdapter {
    pub fn new(home: PathBuf) -> Self {
        Self { home }
    }

    fn is_title_generator_session(entries: &[Value]) -> bool {
        const MARKER: &str = "Du bekommst einen Auszug aus einer Claude Code Session.";
        for e in entries {
            let Some(msg) = e.get("message").and_then(|m| m.as_object()) else {
                continue;
            };
            if msg.get("role").and_then(|r| r.as_str()) != Some("user") {
                continue;
            }
            let text = extract_text(msg.get("content").unwrap_or(&Value::Null));
            return text
                .map(|t| t.trim_start().starts_with(MARKER))
                .unwrap_or(false);
        }
        false
    }
}

fn extract_text(content: &Value) -> Option<String> {
    if let Some(s) = content.as_str() {
        return Some(s.to_string());
    }
    if let Some(list) = content.as_array() {
        for block in list {
            if block.get("type").and_then(|t| t.as_str()) == Some("text") {
                if let Some(t) = block.get("text").and_then(|t| t.as_str()) {
                    return Some(t.to_string());
                }
            }
        }
    }
    None
}

/// Summarise a tool_use input so the turn is readable in lists/detail.
fn tool_use_summary(block: &Value) -> String {
    let name = block.get("name").and_then(|n| n.as_str()).unwrap_or("?");
    let input = block.get("input").unwrap_or(&Value::Null);
    let mut hints: Vec<String> = Vec::new();
    for key in [
        "command",
        "cmd",
        "file_path",
        "path",
        "pattern",
        "query",
        "url",
        "todos",
    ] {
        if let Some(v) = input.get(key) {
            let s = match v {
                Value::String(s) => s.chars().take(160).collect::<String>(),
                other => other.to_string(),
            };
            if !s.trim().is_empty() {
                hints.push(format!("{key}={s}"));
            }
        }
    }
    if hints.is_empty() {
        format!("[Tool: {name}]")
    } else {
        format!("[Tool: {name}] {}", hints.join(" "))
    }
}

fn extract_content(message: &Value) -> String {
    let content = message.get("content").unwrap_or(&Value::Null);
    if let Some(s) = content.as_str() {
        return s.to_string();
    }
    if let Some(list) = content.as_array() {
        let mut parts: Vec<String> = Vec::new();
        for block in list {
            match block.get("type").and_then(|t| t.as_str()) {
                Some("text") => {
                    if let Some(t) = block.get("text").and_then(|t| t.as_str()) {
                        parts.push(t.to_string());
                    }
                }
                Some("tool_use") => {
                    parts.push(tool_use_summary(block));
                }
                Some("tool_result") => {
                    let tc = block.get("content").cloned().unwrap_or(Value::Null);
                    let s = match tc {
                        Value::String(s) => s.chars().take(2000).collect::<String>(),
                        other => other.to_string(),
                    };
                    parts.push(s);
                }
                // Thinking used to vanish entirely, leaving empty assistant rows.
                Some("thinking") | Some("reasoning") => {
                    let t = block
                        .get("thinking")
                        .or_else(|| block.get("text"))
                        .or_else(|| block.get("reasoning"))
                        .and_then(|t| t.as_str())
                        .unwrap_or("")
                        .chars()
                        .take(400)
                        .collect::<String>();
                    if !t.trim().is_empty() {
                        parts.push(format!("[thinking] {t}"));
                    }
                }
                Some("image") | Some("input_image") | Some("image_url") => {
                    parts.push("[Image]".to_string());
                }
                _ => {}
            }
        }
        // Empty thinking / empty blocks are not useful in the UI — omit them
        // entirely rather than storing placeholder noise.
        return parts.join("\n").trim().to_string();
    }
    content.to_string().chars().take(2000).collect()
}

fn extract_tool_calls(message: &Value) -> Option<Value> {
    let content = message.get("content")?.as_array()?;
    let mut calls = Vec::new();
    for block in content {
        if block.get("type").and_then(|t| t.as_str()) == Some("tool_use") {
            calls.push(json!({
                "tool_name": block.get("name").and_then(|n| n.as_str()).unwrap_or(""),
                "input": block.get("input").cloned().unwrap_or(Value::Object(Default::default())),
            }));
        }
    }
    if calls.is_empty() {
        None
    } else {
        Some(Value::Array(calls))
    }
}

fn map_role(entry: &Value) -> &'static str {
    let entry_type = entry.get("type").and_then(|t| t.as_str()).unwrap_or("");
    let role = entry
        .get("message")
        .and_then(|m| m.get("role"))
        .and_then(|r| r.as_str())
        .unwrap_or("");
    if entry_type == "user" || role == "user" {
        if let Some(list) = entry
            .get("message")
            .and_then(|m| m.get("content"))
            .and_then(|c| c.as_array())
        {
            for block in list {
                if block.get("type").and_then(|t| t.as_str()) == Some("tool_result") {
                    return "tool_result";
                }
            }
        }
        return "user";
    }
    if entry_type == "assistant" || role == "assistant" {
        return "assistant";
    }
    if entry_type == "system" || role == "system" {
        return "system";
    }
    "user"
}

fn try_parse_timestamp(ts: Option<&str>) -> Option<DateTime<Utc>> {
    let s = ts?;
    DateTime::parse_from_rfc3339(s)
        .ok()
        .or_else(|| DateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f%z").ok())
        .map(|d| d.with_timezone(&Utc))
}

fn parse_timestamp_or(ts: Option<&str>, fallback: DateTime<Utc>) -> DateTime<Utc> {
    try_parse_timestamp(ts).unwrap_or(fallback)
}

fn first_cwd(entries: &[Value]) -> Option<String> {
    for e in entries {
        if let Some(cwd) = e.get("cwd").and_then(|c| c.as_str()) {
            if !cwd.trim().is_empty() {
                return Some(cwd.to_string());
            }
        }
    }
    None
}

fn per_message_total(message: &Value) -> Option<i64> {
    let usage = message.get("usage")?;
    let inp = usage
        .get("input_tokens")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    let out = usage
        .get("output_tokens")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    let total = inp + out;
    if total > 0 {
        Some(total)
    } else {
        None
    }
}

fn sum_usage(entries: &[Value]) -> (Option<i64>, Option<i64>) {
    // Claude Code writes one JSONL entry per content block; all blocks of one
    // API response share `message.id` and the same `usage`. Count each id once.
    let mut seen: HashSet<String> = HashSet::new();
    let mut tin = 0i64;
    let mut tout = 0i64;
    for e in entries {
        let Some(msg) = e.get("message") else {
            continue;
        };
        let Some(usage) = msg.get("usage") else {
            continue;
        };
        if let Some(id) = msg.get("id").and_then(|v| v.as_str()) {
            if !seen.insert(id.to_string()) {
                continue;
            }
        }
        tin += usage
            .get("input_tokens")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        tout += usage
            .get("output_tokens")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
    }
    ((tin > 0).then_some(tin), (tout > 0).then_some(tout))
}

fn parse_uuid_loose(raw: Option<&Value>) -> Option<Uuid> {
    let s = raw?.as_str()?;
    Uuid::parse_str(s).ok()
}

impl Adapter for ClaudeCodeAdapter {
    fn name(&self) -> &str {
        "claude_code"
    }

    fn home(&self) -> PathBuf {
        self.home.clone()
    }

    fn discover(&self) -> Result<Vec<PathBuf>> {
        if !self.home.exists() {
            return Ok(Vec::new());
        }
        let mut out = Vec::new();
        for proj in std::fs::read_dir(&self.home)? {
            let proj = proj?.path();
            if !proj.is_dir() {
                continue;
            }
            for entry in walkdir::WalkDir::new(&proj)
                .into_iter()
                .filter_map(|e| e.ok())
            {
                let path = entry.path();
                if path.extension().and_then(|e| e.to_str()) == Some("jsonl")
                    && self.excluded_reason(path).is_none()
                {
                    out.push(path.to_path_buf());
                }
            }
        }
        out.sort();
        Ok(out)
    }

    fn excluded_reason(&self, path: &Path) -> Option<&'static str> {
        let rel = path.strip_prefix(&self.home).unwrap_or(path);
        if rel.components().any(|c| c.as_os_str() == "subagents") {
            return Some("subagent transcript");
        }
        // Resolve symlinks: a linked file can point into subagents/ without
        // carrying that segment in its own path.
        if let (Ok(real_path), Ok(real_home)) = (
            std::fs::canonicalize(path),
            std::fs::canonicalize(&self.home),
        ) {
            let real_rel = real_path.strip_prefix(&real_home).unwrap_or(&real_path);
            if real_rel.components().any(|c| c.as_os_str() == "subagents") {
                return Some("subagent transcript");
            }
        }
        None
    }

    fn parse(&self, path: &Path) -> Result<Option<NormalisedConversation>> {
        let raw = std::fs::read_to_string(path)?;
        let mut entries: Vec<Value> = Vec::new();
        for line in raw.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Ok(v) = serde_json::from_str::<Value>(line) {
                entries.push(v);
            }
        }
        if entries.is_empty() {
            return Ok(None);
        }

        // Conversation turns only. Claude Code also logs request payloads
        // (`api-request-blob`) with a message object but no uuid; storing them
        // filled search and extraction with copies of the system prompt.
        let msg_entries: Vec<&Value> = entries
            .iter()
            .filter(|e| e.get("message").map(|m| m.is_object()).unwrap_or(false))
            .filter(|e| {
                matches!(
                    e.get("type").and_then(|t| t.as_str()),
                    None | Some("user" | "assistant" | "system")
                )
            })
            .collect();
        if msg_entries.is_empty() {
            return Ok(None);
        }
        if Self::is_title_generator_session(&entries) {
            return Ok(None);
        }

        let first = msg_entries[0];
        let Some(session_id) = parse_uuid_loose(first.get("sessionId")) else {
            return Ok(None);
        };

        let project_path = first_cwd(&entries).or_else(|| {
            path.parent()
                .and_then(|p| p.file_name())
                .and_then(|n| n.to_str())
                .map(|n| n.replace('-', "/"))
        });

        let model = msg_entries.iter().find_map(|e| {
            let msg = e.get("message")?;
            if msg.get("role").and_then(|r| r.as_str()) == Some("assistant") {
                msg.get("model")
                    .and_then(|m| m.as_str())
                    .map(|s| s.to_string())
            } else {
                None
            }
        });

        let entrypoint = first
            .get("entrypoint")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| "claude-code".into());
        let git_branch = first
            .get("gitBranch")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .filter(|s| !s.is_empty());

        let started_at = entries
            .iter()
            .find_map(|e| try_parse_timestamp(e.get("timestamp").and_then(|t| t.as_str())))
            .or_else(|| {
                std::fs::metadata(path)
                    .ok()
                    .and_then(|m| m.modified().ok())
                    .map(DateTime::<Utc>::from)
            })
            .unwrap_or_else(|| DateTime::<Utc>::from(std::time::UNIX_EPOCH));
        let (tin, tout) = sum_usage(&entries);

        let mut last_ts = started_at;
        let mut messages = Vec::new();
        let mut seen_ids: HashSet<String> = HashSet::new();
        for e in &msg_entries {
            let msg = e.get("message").cloned().unwrap_or(Value::Null);
            let tool_calls = extract_tool_calls(&msg);
            let tool_name = tool_calls.as_ref().and_then(|calls| {
                calls
                    .as_array()
                    .and_then(|a| a.first())
                    .and_then(|c| c.get("tool_name"))
                    .and_then(|n| n.as_str())
                    .map(|s| s.to_string())
            });
            let content_blocks = match msg.get("content") {
                Some(Value::Array(_)) => msg.get("content").cloned(),
                _ => None,
            };
            let ts = parse_timestamp_or(e.get("timestamp").and_then(|t| t.as_str()), last_ts);
            last_ts = ts;
            // One API response can span several JSONL entries (one per content
            // block) sharing `message.id` and `usage`. Only the first entry of
            // an id carries the token total; the rest get None.
            let first_of_id = match msg.get("id").and_then(|v| v.as_str()) {
                Some(id) => seen_ids.insert(id.to_string()),
                None => true,
            };
            messages.push(NormalisedMessage {
                role: map_role(e).to_string(),
                content: extract_content(&msg),
                content_blocks,
                tool_calls,
                tool_name,
                created_at: Some(ts),
                model: msg
                    .get("model")
                    .and_then(|m| m.as_str())
                    .map(|s| s.to_string()),
                token_count: if first_of_id {
                    per_message_total(&msg)
                } else {
                    None
                },
                is_sidechain: e
                    .get("isSidechain")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false),
                parent_uuid: parse_uuid_loose(e.get("parentUuid")),
                uuid: parse_uuid_loose(e.get("uuid")),
                metadata: json!({}),
            });
        }

        let ended_at = messages
            .iter()
            .filter_map(|m| m.created_at)
            .fold(started_at, |acc, ts| acc.max(ts));

        Ok(Some(NormalisedConversation {
            session_id,
            project_path,
            model,
            entrypoint: Some(entrypoint),
            git_branch,
            started_at,
            ended_at: Some(ended_at),
            messages,
            token_count_in: tin,
            token_count_out: tout,
            summary: None,
            metadata: json!({"source": "claude_code"}),
            source_tool: Some("claude_code".into()),
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn write_session(dir: &Path, name: &str, lines: &[Value]) -> PathBuf {
        let proj = dir.join("my-project");
        std::fs::create_dir_all(&proj).unwrap();
        let path = proj.join(name);
        let mut f = std::fs::File::create(&path).unwrap();
        for line in lines {
            writeln!(f, "{line}").unwrap();
        }
        path
    }

    #[test]
    fn skips_api_request_blobs() {
        // Claude Code also logs request payloads with a `message` object and no
        // uuid; one of them can hold the whole system prompt.
        let tmp = tempfile::tempdir().unwrap();
        let session = Uuid::new_v4();
        let entries = vec![
            json!({"type": "user", "uuid": Uuid::new_v4(), "sessionId": session,
                   "timestamp": "2026-01-02T10:00:00Z",
                   "message": {"role": "user", "content": "real question"}}),
            json!({"type": "api-request-blob", "hash": "abc", "sessionId": session,
                   "message": {"role": "system", "content": "the whole system prompt"}}),
            json!({"type": "assistant", "uuid": Uuid::new_v4(), "sessionId": session,
                   "timestamp": "2026-01-02T10:00:05Z",
                   "message": {"role": "assistant", "content": "real answer"}}),
        ];
        let path = write_session(tmp.path(), "blobs.jsonl", &entries);
        let conv = ClaudeCodeAdapter::new(tmp.path().to_path_buf())
            .parse(&path)
            .unwrap()
            .expect("parsed");
        let contents: Vec<&str> = conv.messages.iter().map(|m| m.content.as_str()).collect();
        assert_eq!(contents, ["real question", "real answer"]);
    }

    #[test]
    fn parses_a_claude_code_jsonl_session() {
        let tmp = tempfile::tempdir().unwrap();
        let session = Uuid::new_v4();
        let entries = vec![
            json!({
                "sessionId": session,
                "cwd": "/home/dev/my-project",
                "entrypoint": "cli",
                "gitBranch": "main",
                "timestamp": "2026-01-02T10:00:00Z",
                "message": {"role": "user", "content": "hello rust"}
            }),
            json!({
                "sessionId": session,
                "timestamp": "2026-01-02T10:00:05Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-x",
                    "content": [
                        {"type": "text", "text": "hi there"},
                        {"type": "tool_use", "name": "Bash", "input": {"cmd": "ls"}}
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 20}
                }
            }),
        ];
        let path = write_session(tmp.path(), "a.jsonl", &entries);
        let adapter = ClaudeCodeAdapter::new(tmp.path().to_path_buf());
        let conv = adapter.parse(&path).unwrap().expect("parsed");

        assert_eq!(conv.session_id, session);
        assert_eq!(conv.project_path.as_deref(), Some("/home/dev/my-project"));
        assert_eq!(conv.source_tool.as_deref(), Some("claude_code"));
        assert_eq!(conv.messages.len(), 2);
        assert_eq!(conv.messages[0].role, "user");
        assert_eq!(conv.messages[1].role, "assistant");
        assert_eq!(conv.messages[1].tool_name.as_deref(), Some("Bash"));
        assert_eq!(conv.token_count_in, Some(10));
        assert_eq!(conv.token_count_out, Some(20));
    }

    #[test]
    fn usage_is_counted_once_per_api_message() {
        // Claude Code writes one JSONL entry per content block; all three share
        // message.id and usage, so the conversation totals must count 10/20 once
        // and exactly one message carries token_count Some(30).
        let tmp = tempfile::tempdir().unwrap();
        let session = Uuid::new_v4();
        let entries = vec![
            json!({
                "sessionId": session,
                "timestamp": "2026-01-02T10:00:00Z",
                "message": {
                    "id": "msg_same",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "block one"}],
                    "usage": {"input_tokens": 10, "output_tokens": 20}
                }
            }),
            json!({
                "sessionId": session,
                "timestamp": "2026-01-02T10:00:01Z",
                "message": {
                    "id": "msg_same",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "block two"}],
                    "usage": {"input_tokens": 10, "output_tokens": 20}
                }
            }),
            json!({
                "sessionId": session,
                "timestamp": "2026-01-02T10:00:02Z",
                "message": {
                    "id": "msg_same",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "block three"}],
                    "usage": {"input_tokens": 10, "output_tokens": 20}
                }
            }),
        ];
        let path = write_session(tmp.path(), "dup-usage.jsonl", &entries);
        let adapter = ClaudeCodeAdapter::new(tmp.path().to_path_buf());
        let conv = adapter.parse(&path).unwrap().expect("parsed");

        assert_eq!(conv.token_count_in, Some(10));
        assert_eq!(conv.token_count_out, Some(20));
        assert_eq!(conv.messages.len(), 3);
        let counts: Vec<Option<i64>> = conv.messages.iter().map(|m| m.token_count).collect();
        assert_eq!(
            counts.iter().filter(|c| **c == Some(30)).count(),
            1,
            "exactly one message carries the token total"
        );
        assert_eq!(counts, vec![Some(30), None, None]);
    }

    #[test]
    fn excludes_subagent_transcripts() {
        let tmp = tempfile::tempdir().unwrap();
        let deep = tmp.path().join("proj").join("sess").join("subagents");
        std::fs::create_dir_all(&deep).unwrap();
        let path = deep.join("agent-1.jsonl");
        std::fs::write(&path, "{}\n").unwrap();
        let adapter = ClaudeCodeAdapter::new(tmp.path().to_path_buf());
        assert_eq!(adapter.excluded_reason(&path), Some("subagent transcript"));
    }

    #[test]
    fn discover_skips_subagents_but_finds_sessions() {
        let tmp = tempfile::tempdir().unwrap();
        let good = write_session(
            tmp.path(),
            "s.jsonl",
            &[json!({
                "sessionId": Uuid::new_v4(),
                "cwd": "/x",
                "timestamp": "2026-01-02T10:00:00Z",
                "message": {"role": "user", "content": "hi"}
            })],
        );
        let deep = tmp.path().join("p").join("s").join("subagents");
        std::fs::create_dir_all(&deep).unwrap();
        std::fs::write(deep.join("agent-1.jsonl"), "{}\n").unwrap();

        let adapter = ClaudeCodeAdapter::new(tmp.path().to_path_buf());
        let found = adapter.discover().unwrap();
        assert_eq!(found, vec![good]);
    }

    #[test]
    fn missing_timestamp_falls_back_to_previous_entry() {
        let tmp = tempfile::tempdir().unwrap();
        let session = Uuid::new_v4();
        let entries = vec![
            json!({
                "sessionId": session,
                "cwd": "/home/dev/my-project",
                "timestamp": "2026-01-02T10:00:00Z",
                "message": {"role": "user", "content": "first"}
            }),
            json!({
                "sessionId": session,
                "message": {"role": "assistant", "content": "middle, no timestamp"}
            }),
            json!({
                "sessionId": session,
                "timestamp": "2026-01-02T10:00:10Z",
                "message": {"role": "user", "content": "third"}
            }),
        ];
        let path = write_session(tmp.path(), "fallback.jsonl", &entries);
        let adapter = ClaudeCodeAdapter::new(tmp.path().to_path_buf());
        let conv = adapter.parse(&path).unwrap().expect("parsed");

        let first_ts = conv.messages[0].created_at.expect("first has ts");
        let middle_ts = conv.messages[1].created_at.expect("middle has ts");
        assert_eq!(middle_ts, first_ts, "middle inherits previous timestamp");
        let age = Utc::now() - middle_ts;
        assert!(
            age > chrono::Duration::days(100),
            "timestamp must not be import time (age={age})"
        );
    }

    #[test]
    fn ended_at_is_max_even_when_last_entry_lacks_timestamp() {
        let tmp = tempfile::tempdir().unwrap();
        let session = Uuid::new_v4();
        let entries = vec![
            json!({
                "sessionId": session,
                "cwd": "/home/dev/my-project",
                "timestamp": "2026-01-02T10:00:00Z",
                "message": {"role": "user", "content": "first"}
            }),
            json!({
                "sessionId": session,
                "timestamp": "2026-01-02T10:00:10Z",
                "message": {"role": "assistant", "content": "second"}
            }),
            json!({
                "sessionId": session,
                "message": {"role": "user", "content": "third, no timestamp"}
            }),
        ];
        let path = write_session(tmp.path(), "ended.jsonl", &entries);
        let adapter = ClaudeCodeAdapter::new(tmp.path().to_path_buf());
        let conv = adapter.parse(&path).unwrap().expect("parsed");

        let expected = DateTime::parse_from_rfc3339("2026-01-02T10:00:10Z")
            .unwrap()
            .with_timezone(&Utc);
        assert_eq!(conv.ended_at.unwrap(), expected);
    }
}
