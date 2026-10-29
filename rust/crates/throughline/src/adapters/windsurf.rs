//! Windsurf plans (`~/.windsurf/plans`) — one Markdown plan per conversation.

use super::{stable_uuid_str, Adapter};
use crate::types::{NormalisedConversation, NormalisedMessage};
use anyhow::Result;
use chrono::Utc;
use serde_json::json;
use std::path::{Path, PathBuf};
use uuid::Uuid;

fn ns() -> Uuid {
    Uuid::new_v5(&Uuid::NAMESPACE_URL, b"throughline:windsurf")
}

pub struct WindsurfAdapter {
    home: PathBuf,
}

impl Default for WindsurfAdapter {
    fn default() -> Self {
        Self {
            home: dirs::home_dir().unwrap_or_default().join(".windsurf/plans"),
        }
    }
}

impl WindsurfAdapter {
    pub fn new(home: PathBuf) -> Self {
        Self { home }
    }
}

fn extract_title(content: &str, filename: &str) -> String {
    for line in content.lines() {
        if let Some(rest) = line.strip_prefix("# ") {
            return rest.trim().chars().take(200).collect();
        }
    }
    let stem = Path::new(filename)
        .file_stem()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_default();
    let cleaned: String = stem
        .replace(['-', '_'], " ")
        .split_whitespace()
        .map(|w| {
            let mut c = w.chars();
            match c.next() {
                Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ");
    cleaned.chars().take(200).collect()
}

impl Adapter for WindsurfAdapter {
    fn name(&self) -> &str {
        "windsurf"
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
            if matches!(
                path.extension().and_then(|e| e.to_str()),
                Some("md") | Some("txt")
            ) {
                out.push(path);
            }
        }
        out.sort();
        Ok(out)
    }

    fn parse(&self, path: &Path) -> Result<Option<NormalisedConversation>> {
        let content = std::fs::read_to_string(path)?;
        if content.trim().len() < 50 {
            return Ok(None);
        }
        let filename = path
            .file_name()
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_default();
        let title = extract_title(&content, &filename);
        let session_id = stable_uuid_str(&ns(), Some(&filename), &format!("windsurf:{filename}"));
        let started = std::fs::metadata(path)
            .and_then(|m| m.modified())
            .ok()
            .map(|t| t.into())
            .unwrap_or_else(Utc::now);

        let messages = vec![NormalisedMessage {
            role: "user".into(),
            content,
            created_at: Some(started),
            uuid: Some(stable_uuid_str(
                &ns(),
                None,
                &format!("windsurf:{filename}:msg:0"),
            )),
            metadata: json!({}),
            ..Default::default()
        }];

        Ok(Some(NormalisedConversation {
            session_id,
            project_path: Some("windsurf".into()),
            model: None,
            entrypoint: Some("windsurf".into()),
            git_branch: None,
            started_at: started,
            ended_at: Some(started),
            messages,
            token_count_in: None,
            token_count_out: None,
            summary: Some(title),
            metadata: json!({"source": "windsurf"}),
            source_tool: Some("windsurf".into()),
        }))
    }
}
