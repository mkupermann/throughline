//! Vibe (Mistral) `~/.vibe/logs/session/session_*` adapter.

use super::{stable_uuid_str, Adapter};
use crate::types::{NormalisedConversation, NormalisedMessage};
use anyhow::Result;
use chrono::{DateTime, TimeZone, Utc};
use regex::Regex;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use uuid::Uuid;

const NS: Uuid = Uuid::from_bytes([
    0xa1, 0xb2, 0xc3, 0xd4, 0xe5, 0xf6, 0x4a, 0x7b, 0x8c, 0x9d, 0x0e, 0x1f, 0x2a, 0x3b, 0x4c, 0x5d,
]);

pub struct VibeAdapter {
    home: PathBuf,
}

impl Default for VibeAdapter {
    fn default() -> Self {
        Self {
            home: dirs::home_dir()
                .unwrap_or_default()
                .join(".vibe/logs/session"),
        }
    }
}

impl VibeAdapter {
    pub fn new(home: PathBuf) -> Self {
        Self { home }
    }
}

fn session_dir_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^session_\d{8}_\d{6}_[a-f0-9]+$").unwrap())
}

fn clean_ansi(s: &str) -> String {
    let re = Regex::new(r"\x1b\[[0-9;]*m").unwrap();
    re.replace_all(s, "").replace('\x1b', "")
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

impl Adapter for VibeAdapter {
    fn name(&self) -> &str {
        "vibe"
    }
    fn home(&self) -> PathBuf {
        self.home.clone()
    }

    fn discover(&self) -> Result<Vec<PathBuf>> {
        if !self.home.exists() {
            return Ok(Vec::new());
        }
        let mut out = Vec::new();
        for entry in std::fs::read_dir(&self.home)? {
            let path = entry?.path();
            let is_session = path.is_dir()
                && path
                    .file_name()
                    .map(|n| session_dir_re().is_match(&n.to_string_lossy()))
                    .unwrap_or(false);
            if is_session && path.join("messages.jsonl").is_file() {
                out.push(path.join("messages.jsonl"));
            }
        }
        out.sort();
        Ok(out)
    }

    fn parse(&self, path: &Path) -> Result<Option<NormalisedConversation>> {
        let raw = std::fs::read_to_string(path)?;
        let session_dir = path.parent().map(|p| p.to_path_buf());
        let meta: Value = session_dir
            .as_ref()
            .map(|d| {
                std::fs::read_to_string(d.join("meta.json"))
                    .ok()
                    .and_then(|s| serde_json::from_str(&s).ok())
                    .unwrap_or(Value::Null)
            })
            .unwrap_or(Value::Null);

        let dir_name = session_dir
            .as_ref()
            .and_then(|p| p.file_name())
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_default();
        let raw_session_id = meta
            .get("session_id")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or(dir_name.clone());
        let session_id = stable_uuid_str(&NS, Some(&raw_session_id), "vibe:session");

        let mut messages = Vec::new();
        let mut seen_ids: std::collections::HashSet<String> = Default::default();
        for (idx, line) in raw.lines().enumerate() {
            if line.trim().is_empty() {
                continue;
            }
            let Ok(msg) = serde_json::from_str::<Value>(line) else {
                continue;
            };
            // Vibe sometimes repeats message_id; first occurrence wins.
            let mid = msg
                .get("message_id")
                .or_else(|| msg.get("id"))
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());
            if let Some(m) = &mid {
                if !seen_ids.insert(m.clone()) {
                    continue;
                }
            }
            let role = match msg
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
            let content = clean_ansi(
                &msg.get("content")
                    .and_then(|c| c.as_str())
                    .unwrap_or_default()
                    .to_string(),
            );
            let uuid = stable_uuid_str(
                &NS,
                mid.as_deref(),
                &format!("vibe:{raw_session_id}:msg:{idx}"),
            );
            messages.push(NormalisedMessage {
                role: role.to_string(),
                content,
                created_at: parse_ts(msg.get("timestamp").unwrap_or(&Value::Null)),
                model: msg
                    .get("model")
                    .and_then(|m| m.as_str())
                    .map(|s| s.to_string()),
                tool_calls: msg.get("tool_calls").filter(|t| !t.is_null()).cloned(),
                uuid: Some(uuid),
                metadata: json!({}),
                ..Default::default()
            });
        }

        if messages.is_empty() {
            return Ok(None);
        }

        let started = parse_ts(meta.get("start_time").unwrap_or(&Value::Null))
            .or_else(|| parse_ts(meta.get("started_at").unwrap_or(&Value::Null)))
            .unwrap_or_else(Utc::now);
        Ok(Some(NormalisedConversation {
            session_id,
            project_path: meta
                .get("environment")
                .and_then(|e| e.get("cwd"))
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
                .or_else(|| Some("vibe".into())),
            model: meta
                .get("model")
                .and_then(|m| m.as_str())
                .map(|s| s.to_string()),
            entrypoint: Some("vibe".into()),
            git_branch: None,
            started_at: started,
            ended_at: parse_ts(meta.get("end_time").unwrap_or(&Value::Null)),
            messages,
            token_count_in: meta
                .get("stats")
                .and_then(|s| s.get("prompt_tokens"))
                .and_then(|v| v.as_i64()),
            token_count_out: meta
                .get("stats")
                .and_then(|s| s.get("completion_tokens"))
                .and_then(|v| v.as_i64()),
            summary: None,
            metadata: json!({"source": "vibe"}),
            source_tool: Some("vibe".into()),
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Datelike;
    use std::io::Write;

    /// Zeitgrundsatz: an entry without a timestamp must not resolve to import
    /// time (Utc::now) or the epoch. The stored value is
    /// `created_at.unwrap_or(conv.started_at)` (writer.rs ingest semantics).
    #[test]
    fn missing_timestamp_keeps_source_time() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("session_20260102_100000_abc123");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("meta.json"),
            r#"{"start_time":"2026-01-02T10:00:00Z"}"#,
        )
        .unwrap();
        let path = dir.join("messages.jsonl");
        let mut f = std::fs::File::create(&path).unwrap();
        writeln!(
            f,
            r#"{{"role":"user","content":"hello vibe","timestamp":"2026-01-02T10:00:00Z","message_id":"m1"}}"#
        )
        .unwrap();
        writeln!(
            f,
            r#"{{"role":"assistant","content":"no timestamp here","message_id":"m2"}}"#
        )
        .unwrap();
        writeln!(
            f,
            r#"{{"role":"user","content":"third","timestamp":"2026-01-02T10:00:02Z","message_id":"m3"}}"#
        )
        .unwrap();
        let adapter = VibeAdapter::new(tmp.path().to_path_buf());
        let conv = adapter.parse(&path).unwrap().expect("parsed");
        assert_eq!(conv.messages.len(), 3);

        let ts = conv.messages[1].created_at.unwrap_or(conv.started_at);
        let age = Utc::now() - ts;
        assert!(
            age > chrono::Duration::days(100) || age < chrono::Duration::days(-100),
            "missing timestamp must not be import time (age={age})"
        );
        assert_ne!(
            ts.year(),
            1970,
            "missing timestamp must not fall back to the epoch"
        );
    }
}
