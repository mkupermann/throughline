//! Agent memory store → Throughline `memory_chunks`.
//!
//! Reads the agent memory tree (`~/.local/share/agent memory root/**`) and
//! upserts durable notes as `source_type='manual'` chunks tagged `agent-memory`.
//! Category is inferred from the heading so Knowlege stays queryable.

use anyhow::{Context, Result};
use postgres::GenericClient;
use std::path::{Path, PathBuf};

pub const TAG: &str = "agent-memory";

fn memory_root() -> PathBuf {
    if let Ok(p) = std::env::var("THROUGHLINE_AGENT_MEMORY_DIR") {
        return PathBuf::from(p);
    }
    dirs::home_dir()
        .unwrap_or_default()
        .join(".local/share/mimocode/memory")
}

fn collect_md_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_md_files(&path, out);
        } else if path.extension().and_then(|e| e.to_str()) == Some("md") {
            out.push(path);
        }
    }
}

fn classify(heading: &str, body: &str) -> &'static str {
    let h = heading.to_ascii_lowercase();
    let b = body.to_ascii_lowercase();
    if h.contains("rule") || h.contains("directive") {
        return "preference";
    }
    if h.contains("decision") || h.contains("architecture") {
        return "decision";
    }
    if h.contains("error") || h.contains("fix") {
        return "error_solution";
    }
    if h.contains("pattern") || h.contains("recipe") {
        return "pattern";
    }
    if h.contains("project") || h.contains("context") {
        return "project_context";
    }
    if h.contains("workflow") || h.contains("process") {
        return "workflow";
    }
    if b.contains("prefer") || b.contains("always ") || b.contains("never ") {
        return "preference";
    }
    "insight"
}

/// Split markdown into heading + body sections (## or # headings).
fn sections(content: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    let mut heading = String::from("(preamble)");
    let mut body = String::new();
    for line in content.lines() {
        if line.starts_with('#') {
            if !body.trim().is_empty() {
                out.push((heading.clone(), body.trim().to_string()));
            }
            heading = line.trim_start_matches('#').trim().to_string();
            body = String::new();
        } else {
            body.push_str(line);
            body.push('\n');
        }
    }
    if !body.trim().is_empty() {
        out.push((heading, body.trim().to_string()));
    }
    out
}

fn project_from_path(path: &Path) -> Option<String> {
    // .../memory/<scope>/<scope_id>/<key>.md  → scope_id
    let mut parts: Vec<String> = path
        .components()
        .filter_map(|c| c.as_os_str().to_str().map(|s| s.to_string()))
        .collect();
    parts.reverse();
    // key.md, scope_id, scope, memory, mimocode, share, .local
    parts
        .iter()
        .skip(2)
        .find(|p| !p.is_empty() && *p != "memory")
        .cloned()
}

/// Ingest every Agent memory markdown file. Returns (files_scanned, chunks_upserted).
pub fn ingest_agent_memory<C: GenericClient>(client: &mut C) -> Result<(usize, usize)> {
    let root = memory_root();
    if !root.exists() {
        anyhow::bail!(
            "Agent memory dir not found: {} (set THROUGHLINE_AGENT_MEMORY_DIR)",
            root.display()
        );
    }
    let mut files = Vec::new();
    collect_md_files(&root, &mut files);
    let files_scanned = files.len();
    let mut upserted = 0usize;

    for path in &files {
        let text =
            std::fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
        if text.trim().is_empty() {
            continue;
        }
        let project = project_from_path(path);
        let file_stem = path
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("agent-memory")
            .to_string();
        // Stable per-file tag so re-ingest replaces instead of duplicating.
        let file_tag = format!("file:{}", path.display());
        // Zeitgrundsatz: source_at is the file's own modification time, never
        // the import time.
        let mtime = std::fs::metadata(path)
            .and_then(|m| m.modified())
            .map(chrono::DateTime::<chrono::Utc>::from)
            .unwrap_or_else(|_| chrono::Utc::now());
        client
            .execute(
                "DELETE FROM memory_chunks WHERE source_type = 'manual' AND $1 = ANY(tags)",
                &[&file_tag],
            )
            .context("replace agent memory file")?;

        for (heading, body) in sections(&text) {
            if body.len() < 40 {
                continue;
            }
            let category = classify(&heading, &body);
            let content = format!("[{file_stem}] {heading}\n{body}");
            let tags = vec![TAG.to_string(), file_stem.clone(), file_tag.clone()];
            let category_type = crate::extract_memory::category_column_type(client)
                .context("category_column_type")?;
            client
                .execute(
                    &format!(
                        r#"
                    INSERT INTO memory_chunks
                        (source_type, content, category, project_name, tags, confidence, status, source_at)
                    VALUES ('manual', $1, $2::text::{category_type}, $3, $4::text[], 0.85, 'active', $5)
                    "#
                    ),
                    &[&content, &category, &project, &tags, &mtime],
                )
                .context("insert agent memory chunk")?;
            upserted += 1;
        }
    }
    Ok((files_scanned, upserted))
}

/// List agent-memory (and other) chunks for the GUI.
pub fn list_memory<C: GenericClient>(
    client: &mut C,
    q: Option<String>,
    category: Option<String>,
    limit: i32,
    offset: i32,
) -> Result<(
    i64,
    Vec<(
        i64,
        String,
        String,
        Option<String>,
        chrono::DateTime<chrono::Utc>,
    )>,
)> {
    let q_pat = q.map(|s| format!("%{}%", s.replace('%', "\\%")));
    let total: i64 = client
        .query_one(
            "SELECT COUNT(*)::bigint FROM memory_chunks
             WHERE status = 'active'
               AND ($1::text IS NULL OR content ILIKE $1 OR COALESCE(project_name,'') ILIKE $1)
               AND ($2::text IS NULL OR category::text = $2)",
            &[&q_pat, &category],
        )?
        .get(0);
    let rows = client.query(
        "SELECT id, left(content, 280), category::text, project_name, created_at
         FROM memory_chunks
         WHERE status = 'active'
           AND ($1::text IS NULL OR content ILIKE $1 OR COALESCE(project_name,'') ILIKE $1)
           AND ($2::text IS NULL OR category::text = $2)
         ORDER BY created_at DESC
         LIMIT $3 OFFSET $4",
        &[&q_pat, &category, &(limit as i64), &(offset as i64)],
    )?;
    let items = rows
        .into_iter()
        .map(|r| (r.get(0), r.get(1), r.get(2), r.get(3), r.get(4)))
        .collect();
    Ok((total, items))
}

/// Full chunk body for detail view.
pub fn get_memory<C: GenericClient>(
    client: &mut C,
    id: i64,
) -> Result<
    Option<(
        String,
        String,
        Option<String>,
        chrono::DateTime<chrono::Utc>,
    )>,
> {
    let rows = client.query(
        "SELECT content, category::text, project_name, created_at
         FROM memory_chunks WHERE id = $1",
        &[&id],
    )?;
    Ok(rows
        .into_iter()
        .next()
        .map(|r| (r.get(0), r.get(1), r.get(2), r.get(3))))
}
