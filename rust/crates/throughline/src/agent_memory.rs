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
    ingest_agent_memory_from(client, &memory_root())
}

fn ingest_agent_memory_from<C: GenericClient>(
    client: &mut C,
    root: &Path,
) -> Result<(usize, usize)> {
    if !root.exists() {
        anyhow::bail!(
            "Agent memory dir not found: {} (set THROUGHLINE_AGENT_MEMORY_DIR)",
            root.display()
        );
    }
    let mut files = Vec::new();
    collect_md_files(root, &mut files);
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
        // Stable per-file tag: a re-import finds this file's notes again.
        let file_tag = format!("file:{}", path.display());
        // Zeitgrundsatz: source_at is the file's own modification time, never
        // the import time.
        let mtime = std::fs::metadata(path)
            .and_then(|m| m.modified())
            .map(chrono::DateTime::<chrono::Utc>::from)
            .unwrap_or_else(|_| chrono::Utc::now());

        // Match each section to the note it produced last time (by heading,
        // oldest first) and update that row, so its id, status and stars
        // survive. New sections are inserted, vanished ones deleted, all in
        // one transaction per file.
        let mut tx = client.transaction().context("begin agent memory file")?;
        let category_type =
            crate::extract_memory::category_column_type(&mut tx).context("category_column_type")?;
        let mut previous: Vec<(i64, String)> = tx
            .query(
                "SELECT id, content FROM memory_chunks \
                 WHERE source_type = 'manual' AND $1 = ANY(tags) ORDER BY id",
                &[&file_tag],
            )
            .context("load agent memory notes")?
            .iter()
            .map(|r| (r.get(0), r.get(1)))
            .collect();
        for (heading, body) in sections(&text) {
            if body.len() < 40 {
                continue;
            }
            let category = classify(&heading, &body);
            let prefix = format!("[{file_stem}] {heading}\n");
            let content = format!("{prefix}{body}");
            let tags = vec![TAG.to_string(), file_stem.clone(), file_tag.clone()];
            match previous.iter().position(|(_, c)| c.starts_with(&prefix)) {
                Some(i) => {
                    let (id, _) = previous.remove(i);
                    tx.execute(
                        &format!(
                            r#"
                        UPDATE memory_chunks
                           SET content = $2, category = $3::text::{category_type},
                               project_name = $4, tags = $5::text[], source_at = $6,
                               updated_at = now()
                         WHERE id = $1
                        "#
                        ),
                        &[&id, &content, &category, &project, &tags, &mtime],
                    )
                    .context("update agent memory chunk")?;
                }
                None => {
                    tx.execute(
                        &format!(
                            r#"
                        INSERT INTO memory_chunks
                            (source_type, content, category, project_name, tags, confidence, status, source_at, valid_from)
                        VALUES ('manual', $1, $2::text::{category_type}, $3, $4::text[], 0.85, 'active', $5, $5)
                        "#
                        ),
                        &[&content, &category, &project, &tags, &mtime],
                    )
                    .context("insert agent memory chunk")?;
                }
            }
            upserted += 1;
        }
        for (id, _) in previous {
            tx.execute("DELETE FROM memory_chunks WHERE id = $1", &[&id])
                .context("drop vanished agent memory chunk")?;
        }
        tx.commit().context("commit agent memory file")?;
    }
    Ok((files_scanned, upserted))
}

/// List agent-memory (and other) chunks for the GUI.
#[allow(clippy::type_complexity)]
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
#[allow(clippy::type_complexity)]
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

#[cfg(test)]
mod tests {
    use super::*;

    /// Connect to the database (`PG*` env). Skip when unreachable so offline
    /// `cargo test --lib` stays green. Mirrors the server.rs test helper.
    fn test_db() -> Option<postgres::Client> {
        use postgres::NoTls;
        let host = std::env::var("PGHOST").unwrap_or_else(|_| "localhost".into());
        let user = std::env::var("PGUSER")
            .unwrap_or_else(|_| std::env::var("USERNAME").unwrap_or_else(|_| "postgres".into()));
        let dbname = std::env::var("PGDATABASE").unwrap_or_else(|_| "throughline".into());
        let mut cfg = postgres::Config::new();
        cfg.host(&host)
            .port(
                std::env::var("PGPORT")
                    .ok()
                    .and_then(|p| p.parse().ok())
                    .unwrap_or(5432),
            )
            .user(&user)
            .dbname(&dbname);
        if let Ok(pw) = std::env::var("PGPASSWORD") {
            cfg.password(pw);
        }
        cfg.connect(NoTls).ok()
    }

    fn notes<C: GenericClient>(client: &mut C, file: &Path) -> Vec<(i64, String, String)> {
        let tag = format!("file:{}", file.display());
        client
            .query(
                "SELECT id, status, content FROM memory_chunks \
                 WHERE source_type = 'manual' AND $1 = ANY(tags) ORDER BY id",
                &[&tag],
            )
            .unwrap()
            .iter()
            .map(|r| (r.get(0), r.get(1), r.get(2)))
            .collect()
    }

    #[test]
    fn re_import_keeps_note_ids_and_status() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping re_import_keeps_note_ids_and_status: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("project").join("atlas").join("notes.md");
        std::fs::create_dir_all(file.parent().unwrap()).unwrap();
        let decision = "# Decision\nUse PostgreSQL with pgvector for every project store.\n";
        let pattern = "# Pattern\nWrite the failing test first, then the smallest change.\n";
        std::fs::write(&file, format!("{decision}{pattern}")).unwrap();
        ingest_agent_memory_from(&mut tx, dir.path()).expect("first import");
        let first = notes(&mut tx, &file);
        assert_eq!(first.len(), 2);
        tx.execute(
            "UPDATE memory_chunks SET status = 'forgotten' WHERE id = $1",
            &[&first[1].0],
        )
        .unwrap();

        // The decision gets a new body; the pattern stays.
        let decision2 = "# Decision\nUse PostgreSQL 16 with pgvector for every project store.\n";
        std::fs::write(&file, format!("{decision2}{pattern}")).unwrap();
        ingest_agent_memory_from(&mut tx, dir.path()).expect("second import");
        let second = notes(&mut tx, &file);
        let ids = |v: &[(i64, String, String)]| v.iter().map(|n| n.0).collect::<Vec<_>>();
        assert_eq!(
            ids(&second),
            ids(&first),
            "notes keep their ids, so stars stay attached"
        );
        assert!(
            second[0].2.contains("PostgreSQL 16"),
            "the changed section is updated"
        );
        assert_eq!(second[1].1, "forgotten", "a forgotten note stays forgotten");

        // A section removed from the file goes away.
        std::fs::write(&file, decision2).unwrap();
        ingest_agent_memory_from(&mut tx, dir.path()).expect("third import");
        assert_eq!(ids(&notes(&mut tx, &file)), vec![first[0].0]);
    }
}
