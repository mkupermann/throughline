//! Source-agnostic conversation types, matching the Python `Normalised*` contract.

use chrono::{DateTime, Utc};
use serde_json::Value;

/// One row of `messages`, source-agnostic.
#[derive(Debug, Clone, Default)]
pub struct NormalisedMessage {
    /// `user` | `assistant` | `system` | `tool_result`
    pub role: String,
    pub content: String,
    pub content_blocks: Option<Value>,
    pub tool_calls: Option<Value>,
    pub tool_name: Option<String>,
    pub created_at: Option<DateTime<Utc>>,
    pub model: Option<String>,
    pub token_count: Option<i64>,
    pub is_sidechain: bool,
    pub parent_uuid: Option<uuid::Uuid>,
    pub uuid: Option<uuid::Uuid>,
    pub metadata: Value,
}

/// One row of `conversations`.
#[derive(Debug, Clone)]
pub struct NormalisedConversation {
    pub session_id: uuid::Uuid,
    pub project_path: Option<String>,
    pub model: Option<String>,
    pub entrypoint: Option<String>,
    pub git_branch: Option<String>,
    pub started_at: DateTime<Utc>,
    pub ended_at: Option<DateTime<Utc>>,
    pub messages: Vec<NormalisedMessage>,
    /// Widened to i64 to match `token_count_in`/`token_count_out` BIGINT
    /// (migration 005 in the Python package).
    pub token_count_in: Option<i64>,
    pub token_count_out: Option<i64>,
    pub summary: Option<String>,
    pub metadata: Value,
    /// Which tool produced this conversation (adapter name).
    pub source_tool: Option<String>,
}

#[derive(Debug, Clone, Default)]
pub struct IngestSummary {
    pub adapter: String,
    pub files_seen: usize,
    pub ingested: usize,
    pub refreshed: usize,
    pub skipped: usize,
    pub errors: usize,
    pub messages_written: usize,
    pub self_referential: usize,
}

impl IngestSummary {
    pub fn new(adapter: impl Into<String>) -> Self {
        Self {
            adapter: adapter.into(),
            ..Default::default()
        }
    }
}
