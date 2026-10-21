//! Export the corpus as a Markdown vault.

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use postgres::GenericClient;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Default)]
pub struct ExportSummary {
    pub projects: usize,
    pub sessions: usize,
    pub messages: usize,
    pub files_written: usize,
}

/// Write one folder per project, one file per session (or dated parts).
pub fn export_markdown<C: GenericClient>(
    client: &mut C,
    out_root: &Path,
    project_filter: Option<&str>,
    include_generated: bool,
    redact: bool,
) -> Result<ExportSummary> {
    let mut summary = ExportSummary::default();
    fs::create_dir_all(out_root).with_context(|| format!("create {}", out_root.display()))?;

    let rows = client.query(
        r#"
        SELECT id, session_id, project_name, source_tool, model,
               started_at, ended_at, summary, generated_by
        FROM conversations
        WHERE ($1::text IS NULL OR project_name = $1)
          AND ($2 OR generated_by IS NULL)
        ORDER BY project_name NULLS LAST, started_at ASC
        "#,
        &[&project_filter, &include_generated],
    )?;

    let mut current_project = String::new();
    let mut project_dir = PathBuf::new();

    for row in rows {
        let id: i64 = row.get(0);
        let session_id: uuid::Uuid = row.get(1);
        let project_name: Option<String> = row.get(2);
        let source_tool: Option<String> = row.get(3);
        let model: Option<String> = row.get(4);
        let started_at: DateTime<Utc> = row.get(5);
        let ended_at: Option<DateTime<Utc>> = row.get(6);
        let summary_text: Option<String> = row.get(7);

        let project = project_name.unwrap_or_else(|| "unknown".into());
        if project != current_project {
            project_dir = out_root.join(sanitize(&project));
            fs::create_dir_all(&project_dir)?;
            current_project = project.clone();
            summary.projects += 1;
        }

        let file_name = format!(
            "{}-{}.md",
            started_at.format("%Y-%m-%d"),
            &session_id.to_string()[..8]
        );
        let path = project_dir.join(file_name);
        let mut md = String::new();
        md.push_str(&format!("# Session {session_id}\n\n"));
        md.push_str(&format!(
            "- **Project:** {project}\n- **Tool:** {}\n- **Model:** {}\n- **Started:** {}\n",
            source_tool.as_deref().unwrap_or("unknown"),
            model.as_deref().unwrap_or("—"),
            started_at.to_rfc3339()
        ));
        if let Some(end) = ended_at {
            md.push_str(&format!("- **Ended:** {}\n", end.to_rfc3339()));
        }
        if let Some(s) = summary_text {
            md.push_str(&format!("\n> {s}\n"));
        }
        md.push_str("\n---\n\n");

        let msgs = client.query(
            r#"
            SELECT role::text, COALESCE(content, ''), model, created_at
            FROM messages
            WHERE conversation_id = $1
            ORDER BY created_at ASC, id ASC
            "#,
            &[&id],
        )?;
        for m in msgs {
            let role: String = m.get(0);
            let content: String = m.get(1);
            let msg_model: Option<String> = m.get(2);
            let created: DateTime<Utc> = m.get(3);
            let content = if redact {
                crate::pii::redact(&content).0
            } else {
                content
            };
            md.push_str(&format!(
                "## {} {}\n\n_{}_\n\n{}\n\n",
                role_label(&role),
                msg_model.as_deref().map(|m| format!("· `{m}`")).unwrap_or_default(),
                created.to_rfc3339(),
                content.trim()
            ));
            summary.messages += 1;
        }

        fs::write(&path, md).with_context(|| format!("write {}", path.display()))?;
        summary.files_written += 1;
        summary.sessions += 1;
    }

    // Manifest so re-runs can tell what this export owns.
    let manifest = format!(
        "throughline-export\nsessions={}\nmessages={}\n",
        summary.sessions, summary.messages
    );
    fs::write(out_root.join("EXPORT_MANIFEST.txt"), manifest)?;
    Ok(summary)
}

fn sanitize(name: &str) -> String {
    name.chars()
        .map(|c| if c.is_alphanumeric() || c == '-' || c == '_' { c } else { '_' })
        .collect()
}

fn role_label(role: &str) -> &str {
    match role {
        "user" => "Prompt",
        "assistant" => "Answer",
        "tool_result" => "Execution",
        "system" => "System",
        other => other,
    }
}
