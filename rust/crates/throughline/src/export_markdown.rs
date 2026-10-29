//! Export the corpus as a Markdown vault.

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use postgres::GenericClient;
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Default)]
pub struct ExportSummary {
    pub projects: usize,
    pub sessions: usize,
    pub messages: usize,
    pub files_written: usize,
}

/// Provenance manifest written as `evidence-manifest.json` when `--evidence` is on.
///
/// Field order is the contract: two runs over the same corpus must differ only
/// in `generated_at`. Prompts stay out of the manifest; the Markdown files they
/// ended up in are referenced by path and SHA-256 instead.
#[derive(Debug, Serialize)]
pub struct EvidenceManifest {
    pub generated_at: DateTime<Utc>,
    pub tool: String,
    pub tool_version: String,
    pub scope: EvidenceScope,
    pub conversations: Vec<EvidenceConversation>,
    pub files: Vec<EvidenceFile>,
    pub totals: EvidenceTotals,
}

#[derive(Debug, Serialize)]
pub struct EvidenceScope {
    pub projects: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct EvidenceConversation {
    pub session_id: String,
    pub source_tool: Option<String>,
    pub model: Option<String>,
    pub project_name: String,
    pub started_at: DateTime<Utc>,
    pub ended_at: Option<DateTime<Utc>>,
    pub message_count: i64,
}

#[derive(Debug, Serialize)]
pub struct EvidenceFile {
    pub path: String,
    pub sha256: String,
    pub bytes: u64,
}

#[derive(Debug, Serialize)]
pub struct EvidenceTotals {
    pub conversations: i64,
    pub messages: i64,
    pub memory_chunks: i64,
}

/// Assemble the manifest with the required stable orderings.
pub fn build_evidence_manifest(
    generated_at: DateTime<Utc>,
    mut projects: Vec<String>,
    mut conversations: Vec<EvidenceConversation>,
    mut files: Vec<EvidenceFile>,
    totals: EvidenceTotals,
) -> EvidenceManifest {
    projects.sort();
    projects.dedup();
    conversations.sort_by(|a, b| {
        a.started_at
            .cmp(&b.started_at)
            .then_with(|| a.session_id.cmp(&b.session_id))
    });
    files.sort_by(|a, b| a.path.cmp(&b.path));
    EvidenceManifest {
        generated_at,
        tool: "throughline".to_string(),
        tool_version: env!("CARGO_PKG_VERSION").to_string(),
        scope: EvidenceScope { projects },
        conversations,
        files,
        totals,
    }
}

fn sha256_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    format!("{:x}", hasher.finalize())
}

/// Write one folder per project, one file per session (or dated parts).
///
/// `only_ids`, when set, restricts the export to those conversation ids — used
/// by retention to archive exactly the conversations a policy hits.
pub fn export_markdown<C: GenericClient>(
    client: &mut C,
    out_root: &Path,
    project_filter: Option<&str>,
    include_generated: bool,
    redact: bool,
    evidence: bool,
    only_ids: Option<&[i64]>,
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
          AND ($3::bigint[] IS NULL OR id = ANY($3))
        ORDER BY project_name NULLS LAST, started_at ASC
        "#,
        &[
            &project_filter,
            &include_generated,
            &only_ids.map(|s| s.to_vec()),
        ],
    )?;

    let mut current_project = String::new();
    let mut project_dir = PathBuf::new();
    let mut ev_conversations: Vec<EvidenceConversation> = Vec::new();
    let mut ev_files: Vec<EvidenceFile> = Vec::new();

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
        let mut conv_messages = 0i64;
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
                msg_model
                    .as_deref()
                    .map(|m| format!("· `{m}`"))
                    .unwrap_or_default(),
                created.to_rfc3339(),
                content.trim()
            ));
            summary.messages += 1;
            conv_messages += 1;
        }

        let content_hash = if evidence {
            Some(sha256_hex(md.as_bytes()))
        } else {
            None
        };
        fs::write(&path, &md).with_context(|| format!("write {}", path.display()))?;
        summary.files_written += 1;
        summary.sessions += 1;

        if let Some(sha256) = content_hash {
            let rel = path
                .strip_prefix(out_root)
                .unwrap_or(&path)
                .to_string_lossy()
                .replace('\\', "/");
            let bytes = md.len() as u64;
            ev_files.push(EvidenceFile {
                path: rel,
                sha256,
                bytes,
            });
            ev_conversations.push(EvidenceConversation {
                session_id: session_id.to_string(),
                source_tool,
                model,
                project_name: project,
                started_at,
                ended_at,
                message_count: conv_messages,
            });
        }
    }

    // Manifest so re-runs can tell what this export owns.
    let manifest = format!(
        "throughline-export\nsessions={}\nmessages={}\n",
        summary.sessions, summary.messages
    );
    fs::write(out_root.join("EXPORT_MANIFEST.txt"), manifest)?;

    if evidence {
        let memory_chunks: i64 = client
            .query_one(
                r#"
                SELECT COUNT(*) FROM memory_chunks
                WHERE ($1::text IS NULL OR project_name = $1)
                  AND ($2::bigint[] IS NULL OR (source_type = 'conversation' AND source_id = ANY($2)))
                "#,
                &[&project_filter, &only_ids.map(|s| s.to_vec())],
            )?
            .get(0);
        let mut projects: Vec<String> = ev_conversations
            .iter()
            .map(|c| c.project_name.clone())
            .collect();
        projects.sort();
        projects.dedup();
        let totals = EvidenceTotals {
            conversations: ev_conversations.len() as i64,
            messages: summary.messages as i64,
            memory_chunks,
        };
        let manifest =
            build_evidence_manifest(Utc::now(), projects, ev_conversations, ev_files, totals);
        let json =
            serde_json::to_string_pretty(&manifest).context("serialize evidence-manifest.json")?;
        fs::write(out_root.join("evidence-manifest.json"), format!("{json}\n")).with_context(
            || {
                format!(
                    "write {}",
                    out_root.join("evidence-manifest.json").display()
                )
            },
        )?;
    }

    Ok(summary)
}

fn sanitize(name: &str) -> String {
    name.chars()
        .map(|c| {
            if c.is_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
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

#[cfg(test)]
mod tests {
    use super::*;

    fn conv(id: &str, started: &str, project: &str, messages: i64) -> EvidenceConversation {
        EvidenceConversation {
            session_id: id.to_string(),
            source_tool: Some("claude_code".into()),
            model: Some("m1".into()),
            project_name: project.to_string(),
            started_at: started.parse().unwrap(),
            ended_at: None,
            message_count: messages,
        }
    }

    fn file(path: &str) -> EvidenceFile {
        EvidenceFile {
            path: path.to_string(),
            sha256: sha256_hex(path.as_bytes()),
            bytes: path.len() as u64,
        }
    }

    #[test]
    fn evidence_manifest_has_required_fields() {
        let m = build_evidence_manifest(
            "2026-01-01T00:00:00Z".parse().unwrap(),
            vec!["Atlas-demo".into()],
            vec![conv("s1", "2026-01-02T00:00:00Z", "Atlas-demo", 4)],
            vec![file("Atlas-demo/2026-01-02-s1.md")],
            EvidenceTotals {
                conversations: 1,
                messages: 4,
                memory_chunks: 2,
            },
        );
        let v: serde_json::Value = serde_json::to_value(&m).unwrap();
        for key in [
            "generated_at",
            "tool",
            "tool_version",
            "scope",
            "conversations",
            "files",
            "totals",
        ] {
            assert!(v.get(key).is_some(), "missing top-level key {key}");
        }
        assert_eq!(v["tool"], "throughline");
        assert!(v["tool_version"].is_string());
        assert!(v["scope"]["projects"].is_array());
        let c = &v["conversations"][0];
        for key in [
            "session_id",
            "source_tool",
            "model",
            "project_name",
            "started_at",
            "ended_at",
            "message_count",
        ] {
            assert!(c.get(key).is_some(), "missing conversation key {key}");
        }
        let f = &v["files"][0];
        for key in ["path", "sha256", "bytes"] {
            assert!(f.get(key).is_some(), "missing file key {key}");
        }
        for key in ["conversations", "messages", "memory_chunks"] {
            assert!(v["totals"].get(key).is_some(), "missing totals key {key}");
        }
    }

    #[test]
    fn evidence_manifest_sorts_stably() {
        let m = build_evidence_manifest(
            "2026-01-01T00:00:00Z".parse().unwrap(),
            vec!["Zeta".into(), "Atlas".into(), "Atlas".into()],
            vec![
                conv("late", "2026-03-01T00:00:00Z", "Zeta", 1),
                conv("early", "2026-01-01T00:00:00Z", "Atlas", 2),
                conv("mid", "2026-02-01T00:00:00Z", "Atlas", 3),
            ],
            vec![file("zeta/b.md"), file("atlas/a.md"), file("atlas/b.md")],
            EvidenceTotals {
                conversations: 3,
                messages: 6,
                memory_chunks: 0,
            },
        );
        let ids: Vec<&str> = m
            .conversations
            .iter()
            .map(|c| c.session_id.as_str())
            .collect();
        assert_eq!(ids, vec!["early", "mid", "late"]);
        let paths: Vec<&str> = m.files.iter().map(|f| f.path.as_str()).collect();
        assert_eq!(paths, vec!["atlas/a.md", "atlas/b.md", "zeta/b.md"]);
        assert_eq!(
            m.scope.projects,
            vec!["Atlas".to_string(), "Zeta".to_string()]
        );
    }

    #[test]
    fn evidence_manifest_differs_only_in_generated_at() {
        let build = || {
            build_evidence_manifest(
                Utc::now(),
                vec!["P".into()],
                vec![conv("s", "2026-01-01T00:00:00Z", "P", 1)],
                vec![file("P/a.md")],
                EvidenceTotals {
                    conversations: 1,
                    messages: 1,
                    memory_chunks: 0,
                },
            )
        };
        let a = build();
        let b = build();
        let mut ja = serde_json::to_value(&a).unwrap();
        let mut jb = serde_json::to_value(&b).unwrap();
        ja["generated_at"] = serde_json::Value::Null;
        jb["generated_at"] = serde_json::Value::Null;
        assert_eq!(ja, jb);
    }
}
