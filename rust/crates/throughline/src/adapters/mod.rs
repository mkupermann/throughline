//! Adapter contract and the built-in implementations.

pub mod aider;
pub mod claude_code;
pub mod cline;
pub mod codex;
pub mod continue_dev;
pub mod cursor;
pub mod hermes;
pub mod opencode;
pub mod registry;
pub mod vibe;
pub mod windsurf;
pub mod zed;

use crate::types::NormalisedConversation;
use anyhow::Result;
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use uuid::Uuid;

pub trait Adapter {
    fn name(&self) -> &str;
    fn home(&self) -> PathBuf;
    fn is_present(&self) -> bool {
        self.home().exists()
    }
    /// Every ingestible file, in stable order.
    fn discover(&self) -> Result<Vec<PathBuf>>;
    /// Why a candidate is excluded, or `None` to ingest it.
    fn excluded_reason(&self, path: &Path) -> Option<&'static str> {
        let _ = path;
        None
    }
    /// Parse one file into zero or one conversation.
    fn parse(&self, path: &Path) -> Result<Option<NormalisedConversation>>;

    /// Parse one file into every conversation it holds (SQLite corpora).
    /// Default: wrap `parse`. Override when a file holds many sessions.
    fn parse_many(&self, path: &Path) -> Result<Vec<NormalisedConversation>> {
        Ok(self.parse(path)?.into_iter().collect())
    }
}

pub fn sha256_file(path: &Path) -> Result<String> {
    let bytes = std::fs::read(path)?;
    let mut hasher = Sha256::new();
    hasher.update(&bytes);
    Ok(format!("{:x}", hasher.finalize()))
}

/// Deterministic message ids for sources that number their messages
/// (`msg_1`, `m1`, …) instead of minting UUIDs. Postgres rejects a non-UUID
/// in the `uuid` column and the whole session is dropped, so hash the raw id
/// into a stable UUID: re-ingesting the same file yields the same id.
pub fn stable_uuid(namespace: &Uuid, raw: Option<&serde_json::Value>, fallback: &str) -> Uuid {
    let Some(raw) = raw else {
        return Uuid::new_v5(namespace, fallback.as_bytes());
    };
    let s = match raw {
        serde_json::Value::Null => return Uuid::new_v5(namespace, fallback.as_bytes()),
        serde_json::Value::String(s) if s.is_empty() => {
            return Uuid::new_v5(namespace, fallback.as_bytes())
        }
        serde_json::Value::String(s) => s.clone(),
        other => other.to_string(),
    };
    // A raw id that already is a UUID is kept so ids stay stable for tools
    // that supply real ones.
    if let Ok(u) = Uuid::parse_str(&s) {
        return u;
    }
    Uuid::new_v5(namespace, format!("{fallback}:{s}").as_bytes())
}

/// Convenience for adapters that hold ids as strings.
pub fn stable_uuid_str(namespace: &Uuid, raw: Option<&str>, fallback: &str) -> Uuid {
    match raw {
        Some(s) if !s.is_empty() => stable_uuid(
            namespace,
            Some(&serde_json::Value::String(s.to_string())),
            fallback,
        ),
        _ => stable_uuid(namespace, None, fallback),
    }
}

/// One instance of every built-in adapter, in registry order.
pub fn default_adapters() -> Vec<Box<dyn Adapter>> {
    registry::built_ins()
}
