//! Source-agnostic conversation types, matching the Python `Normalised*` contract.

use chrono::{DateTime, Utc};
use serde_json::Value;

/// A value of the PostgreSQL enum `message_role`. `Document` has no
/// speaker: the message is a section of a file, a commit or a page.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    User,
    Assistant,
    System,
    ToolResult,
    Document,
}

impl Role {
    pub const ALL: [Role; 5] = [
        Role::User,
        Role::Assistant,
        Role::System,
        Role::ToolResult,
        Role::Document,
    ];

    pub const fn as_str(self) -> &'static str {
        match self {
            Role::User => "user",
            Role::Assistant => "assistant",
            Role::System => "system",
            Role::ToolResult => "tool_result",
            Role::Document => "document",
        }
    }
}

impl std::fmt::Display for Role {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

impl std::str::FromStr for Role {
    type Err = anyhow::Error;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        Role::ALL
            .into_iter()
            .find(|r| r.as_str() == s)
            .ok_or_else(|| anyhow::anyhow!("unknown message role {s:?}"))
    }
}

/// `source_tool` of the conversations that are documents, not sessions:
/// "Recent sessions" in the context pack and the handoff leave them out,
/// their notes still count.
pub const DOCUMENT_SOURCES: &[&str] = &["markdown", "git", "web"];

/// One row of `messages`, source-agnostic.
#[derive(Debug, Clone, Default)]
pub struct NormalisedMessage {
    /// A [`Role`] as text: `user` | `assistant` | `system` | `tool_result` | `document`
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
    /// Items excluded by ingest ignore rules (path + project).
    pub ignored: usize,
    /// Files skipped by a `path:` rule (never read, parsed, or logged).
    pub ignored_path: usize,
    /// Conversations dropped by a `project:` rule (nothing persisted).
    pub ignored_project: usize,
}

impl IngestSummary {
    pub fn new(adapter: impl Into<String>) -> Self {
        Self {
            adapter: adapter.into(),
            ..Default::default()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn role_document_parses_and_prints() {
        assert_eq!("document".parse::<Role>().unwrap(), Role::Document);
        assert_eq!(Role::Document.to_string(), "document");
        assert_eq!(Role::ToolResult.as_str(), "tool_result");
        for role in Role::ALL {
            assert_eq!(role.as_str().parse::<Role>().unwrap(), role);
            let json = serde_json::to_string(&role).unwrap();
            assert_eq!(json, format!("\"{role}\""));
            assert_eq!(serde_json::from_str::<Role>(&json).unwrap(), role);
        }
        let err = "speaker".parse::<Role>().unwrap_err().to_string();
        assert!(err.contains("speaker"), "{err}");
        assert_eq!(DOCUMENT_SOURCES, ["markdown", "git", "web"]);
    }
}
