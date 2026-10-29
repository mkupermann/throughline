//! Aider adapter — `.aider.chat.history.md` in project roots + `~/.aider`.
//!
//! Aider writes a Markdown transcript per project. We materialise each as a
//! conversation of user/assistant turns split on role headers.

use super::{stable_uuid_str, Adapter};
use crate::types::{NormalisedConversation, NormalisedMessage};
use anyhow::Result;
use chrono::{DateTime, TimeZone, Utc};
use serde_json::json;
use std::path::{Path, PathBuf};
use uuid::Uuid;

const NS: Uuid = Uuid::from_bytes([
    0x3b, 0x5d, 0x7f, 0x91, 0xa2, 0xb3, 0xc4, 0xd5, 0xe6, 0xf7, 0x08, 0x19, 0x2a, 0x3b, 0x4c, 0x5d,
]);

pub struct AiderAdapter {
    homes: Vec<PathBuf>,
}

impl Default for AiderAdapter {
    fn default() -> Self {
        let home = dirs::home_dir().unwrap_or_default();
        Self {
            homes: vec![
                home.clone(),
                home.join("Documents/GitHub"),
                home.join("src"),
            ],
        }
    }
}

impl AiderAdapter {
    pub fn new(homes: Vec<PathBuf>) -> Self {
        Self { homes }
    }

    fn is_history(path: &Path) -> bool {
        matches!(
            path.file_name().and_then(|n| n.to_str()),
            Some(".aider.chat.history.md") | Some("aider.chat.history.md")
        )
    }
}

/// Split a Markdown chat history into turns.
fn parse_history(
    content: &str,
    session_key: &str,
    fallback_at: Option<DateTime<Utc>>,
) -> Option<NormalisedConversation> {
    let mut messages = Vec::new();
    let mut role = "user";
    let mut buf = String::new();
    let mut idx = 0usize;

    let flush =
        |messages: &mut Vec<NormalisedMessage>, role: &str, buf: &mut String, idx: &mut usize| {
            let text = buf.trim().to_string();
            buf.clear();
            if text.is_empty() {
                return;
            }
            messages.push(NormalisedMessage {
                role: role.to_string(),
                content: text,
                uuid: Some(stable_uuid_str(
                    &NS,
                    None,
                    &format!("aider:{session_key}:msg:{idx}"),
                )),
                metadata: json!({}),
                ..Default::default()
            });
            *idx += 1;
        };

    for line in content.lines() {
        let t = line.trim();
        if t.eq_ignore_ascii_case("#### User") || t.eq_ignore_ascii_case("## User") {
            flush(&mut messages, role, &mut buf, &mut idx);
            role = "user";
            continue;
        }
        if t.eq_ignore_ascii_case("#### Assistant")
            || t.eq_ignore_ascii_case("## Assistant")
            || t.eq_ignore_ascii_case("#### Aider")
        {
            flush(&mut messages, role, &mut buf, &mut idx);
            role = "assistant";
            continue;
        }
        buf.push_str(line);
        buf.push('\n');
    }
    flush(&mut messages, role, &mut buf, &mut idx);

    if messages.is_empty() {
        return None;
    }
    let started = parse_aider_started_at(content)
        .or(fallback_at)
        .unwrap_or_else(Utc::now);
    let title =
        extract_aider_title(content).unwrap_or_else(|| format!("Aider session {session_key}"));
    Some(NormalisedConversation {
        session_id: stable_uuid_str(&NS, Some(session_key), "aider:session"),
        project_path: Some(session_key.to_string()),
        model: None,
        entrypoint: Some("aider".into()),
        git_branch: None,
        started_at: started,
        ended_at: Some(started),
        messages,
        token_count_in: None,
        token_count_out: None,
        summary: Some(title),
        metadata: json!({"source": "aider"}),
        source_tool: Some("aider".into()),
    })
}

/// `# aider chat started at 2026-08-25 18:50:00` is the real session time.
fn parse_aider_started_at(content: &str) -> Option<DateTime<Utc>> {
    for line in content.lines() {
        let t = line.trim();
        if let Some(rest) = t
            .strip_prefix("# aider chat started at ")
            .or_else(|| t.strip_prefix("# aider chat started at"))
        {
            let stamp = rest.trim();
            // "2026-08-25 18:50:00" (local-naive treated as UTC for storage)
            if let Ok(naive) = chrono::NaiveDateTime::parse_from_str(stamp, "%Y-%m-%d %H:%M:%S") {
                return Some(Utc.from_utc_datetime(&naive));
            }
            if let Ok(dt) = DateTime::parse_from_rfc3339(stamp) {
                return Some(dt.with_timezone(&Utc));
            }
        }
    }
    None
}

fn extract_aider_title(content: &str) -> Option<String> {
    // Prefer the first substantive user heading if present
    for line in content.lines() {
        let t = line.trim();
        if t.starts_with("#### ") {
            let title = t.trim_start_matches("#").trim();
            if !title.is_empty() && !title.starts_with("Ziel") {
                let s: String = title.chars().take(120).collect();
                return Some(s);
            }
        }
    }
    None
}

impl Adapter for AiderAdapter {
    fn name(&self) -> &str {
        "aider"
    }
    fn home(&self) -> PathBuf {
        self.homes
            .first()
            .cloned()
            .unwrap_or_else(|| dirs::home_dir().unwrap_or_default())
    }

    fn discover(&self) -> Result<Vec<PathBuf>> {
        let mut out = Vec::new();
        for root in &self.homes {
            if !root.exists() {
                continue;
            }
            for entry in walkdir::WalkDir::new(root)
                .max_depth(4)
                .into_iter()
                .filter_entry(|e| {
                    let n = e.file_name().to_string_lossy();
                    !matches!(
                        n.as_ref(),
                        "node_modules" | ".git" | "target" | "dist" | "build"
                    )
                })
                .filter_map(|e| e.ok())
            {
                let path = entry.path();
                if path.is_file() && Self::is_history(path) {
                    out.push(path.to_path_buf());
                }
            }
        }
        out.sort();
        out.dedup();
        Ok(out)
    }

    fn parse(&self, path: &Path) -> Result<Option<NormalisedConversation>> {
        let content = std::fs::read_to_string(path)?;
        let session_key = path
            .parent()
            .and_then(|p| p.to_str())
            .unwrap_or_default()
            .to_string();
        let fallback = std::fs::metadata(path)
            .ok()
            .and_then(|m| m.modified().ok())
            .map(DateTime::<Utc>::from);
        Ok(parse_history(&content, &session_key, fallback))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_user_and_assistant_turns() {
        let hist =
            "#### User\nfix the bug\n\n#### Assistant\nfixed it in foo.rs\n\n#### User\nthanks\n";
        let conv = parse_history(hist, "/proj", None).expect("parsed");
        assert_eq!(conv.messages.len(), 3);
        assert_eq!(conv.messages[0].role, "user");
        assert_eq!(conv.messages[1].role, "assistant");
        assert_eq!(conv.messages[1].content.trim(), "fixed it in foo.rs");
    }

    #[test]
    fn uses_chat_started_at_not_ingest_time() {
        let hist = "# aider chat started at 2026-08-25 18:50:00\n\n#### User\nhello\n";
        let conv = parse_history(hist, "/proj", None).expect("parsed");
        assert_eq!(conv.started_at.format("%Y-%m-%d").to_string(), "2026-08-25");
    }
}
