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

/// A note an earlier import of this file produced, with the live end of its
/// supersession chain (the row without `superseded_by`).
struct Produced {
    id: i64,
    content: String,
    category: String,
    /// Replaced by another note (UI, MCP, reflect or a later import).
    replaced: bool,
    end_id: i64,
    end_status: String,
    end_from: chrono::DateTime<chrono::Utc>,
}

/// The notes this file produced, one per chain: the newest file row in it,
/// so an older text the file has since replaced does not count. A chain that
/// ends in a closed file row (a vanished section) is left out; that section
/// comes back as a new note.
fn produced_notes<C: GenericClient>(client: &mut C, file_tag: &str) -> Result<Vec<Produced>> {
    let rows = client.query(
        "WITH RECURSIVE chain AS ( \
             SELECT id AS root, id, superseded_by, 0 AS depth FROM memory_chunks \
              WHERE source_type = 'manual' AND $1 = ANY(tags) \
             UNION ALL \
             SELECT c.root, m.id, m.superseded_by, c.depth + 1 \
               FROM chain c JOIN memory_chunks m ON m.id = c.superseded_by \
              WHERE c.depth < 1000) \
         SELECT f.id, f.content, f.category::text, f.superseded_by IS NOT NULL, \
                e.id, COALESCE(e.status, 'active'), \
                COALESCE(e.valid_from, e.source_at, e.created_at) \
           FROM chain c \
           JOIN memory_chunks f ON f.id = c.root \
           JOIN memory_chunks e ON e.id = c.id \
          WHERE c.superseded_by IS NULL \
          ORDER BY f.id",
        &[&file_tag],
    )?;
    let mut out: Vec<Produced> = Vec::new();
    for r in rows {
        let p = Produced {
            id: r.get(0),
            content: r.get(1),
            category: r.get(2),
            replaced: r.get(3),
            end_id: r.get(4),
            end_status: r.get(5),
            end_from: r.get(6),
        };
        // Rows come by id: a later file row of the same chain is newer.
        out.retain(|o| o.end_id != p.end_id);
        out.push(p);
    }
    out.retain(|p| p.replaced || !matches!(p.end_status.as_str(), "superseded" | "merged"));
    Ok(out)
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

        // Match each section to the note it produced last time (by heading:
        // the same text first, then the oldest live note, then the newest
        // replaced one). An unchanged section is left alone, even when the
        // note was replaced in the UI or over MCP since. A changed one
        // replaces the live end of the chain (the old notes stay as history,
        // a star moves along): the newer change wins. A new one is inserted
        // and a vanished one is closed, never deleted. All in one transaction
        // per file.
        let mut tx = client.transaction().context("begin agent memory file")?;
        let category_type =
            crate::extract_memory::category_column_type(&mut tx).context("category_column_type")?;
        // A file's mtime can lie in the future (clock skew, copied files).
        let now = chrono::Utc::now();
        let mtime_clamped = mtime.min(now);
        let mut previous = produced_notes(&mut tx, &file_tag).context("load agent memory notes")?;
        for (heading, body) in sections(&text) {
            if body.len() < 40 {
                continue;
            }
            let category = classify(&heading, &body);
            let prefix = format!("[{file_stem}] {heading}\n");
            let content = format!("{prefix}{body}");
            let tags = vec![TAG.to_string(), file_stem.clone(), file_tag.clone()];
            let pick = previous
                .iter()
                .position(|p| p.content == content)
                .or_else(|| {
                    previous
                        .iter()
                        .position(|p| !p.replaced && p.content.starts_with(&prefix))
                })
                .or_else(|| {
                    previous
                        .iter()
                        .rposition(|p| p.replaced && p.content.starts_with(&prefix))
                });
            let live_end = match pick.map(|i| previous.remove(i)) {
                Some(p) if p.content == content && (p.replaced || p.category == category) => {
                    upserted += 1;
                    continue;
                }
                // A chain closed without a successor cannot be replaced.
                Some(p) if !matches!(p.end_status.as_str(), "superseded" | "merged") => {
                    Some((p.end_id, p.end_from))
                }
                _ => None,
            };
            match live_end {
                Some((id, old_from)) => {
                    let successor = crate::server::Successor {
                        content: &content,
                        category: Some(category),
                        source_type: "manual",
                        kind: crate::server::SupersedeKind::Update,
                        valid_from: Some(mtime.max(old_from).min(now)),
                        valid_from_is_date: false,
                        confidence: 0.85,
                        tags,
                        source_at: Some(mtime),
                    };
                    crate::server::supersede_memory(&mut tx, id, &successor)
                        .map_err(|e| anyhow::anyhow!("{e}"))
                        .context("supersede agent memory chunk")?;
                }
                None => {
                    tx.execute(
                        &format!(
                            r#"
                        INSERT INTO memory_chunks
                            (source_type, content, category, project_name, tags, confidence, status, source_at, valid_from)
                        VALUES ('manual', $1, $2::text::{category_type}, $3, $4::text[], 0.85, 'active', $5, $6)
                        "#
                        ),
                        &[&content, &category, &project, &tags, &mtime, &mtime_clamped],
                    )
                    .context("insert agent memory chunk")?;
                }
            }
            upserted += 1;
        }
        // Sections that left the file: close their validity, keep the rows.
        // Only a file note that is still the live end of its chain is closed;
        // a replacement from the UI or MCP stays. A forgotten note stays
        // forgotten.
        let vanished: Vec<i64> = previous
            .iter()
            .filter(|p| !p.replaced)
            .map(|p| p.id)
            .collect();
        if !vanished.is_empty() {
            tx.execute(
                "UPDATE memory_chunks \
                    SET status = 'superseded', superseded_at = now(), updated_at = now(), \
                        valid_to = GREATEST(COALESCE(valid_from, source_at, created_at), $2) \
                  WHERE id = ANY($1) AND COALESCE(status, 'active') <> 'forgotten'",
                &[&vanished, &mtime_clamped],
            )
            .context("close vanished agent memory chunks")?;
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
    fn re_import_supersedes_changed_sections_and_keeps_history() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping re_import_supersedes_changed_sections_and_keeps_history: no PostgreSQL reachable"
            );
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
        let (decision_id, pattern_id) = (first[0].0, first[1].0);
        tx.execute(
            "INSERT INTO favorites (kind, ref) VALUES ('memory', $1::bigint::text)",
            &[&decision_id],
        )
        .unwrap();
        tx.execute(
            "UPDATE memory_chunks SET status = 'forgotten' WHERE id = $1",
            &[&pattern_id],
        )
        .unwrap();
        let pattern_updated: chrono::DateTime<chrono::Utc> = tx
            .query_one(
                "SELECT updated_at FROM memory_chunks WHERE id = $1",
                &[&pattern_id],
            )
            .unwrap()
            .get(0);

        // The decision gets a new body; the pattern stays.
        let decision2 = "# Decision\nUse PostgreSQL 16 with pgvector for every project store.\n";
        std::fs::write(&file, format!("{decision2}{pattern}")).unwrap();
        ingest_agent_memory_from(&mut tx, dir.path()).expect("second import");
        let second = notes(&mut tx, &file);
        assert_eq!(second.len(), 3, "the changed section adds a row");
        let pattern_row = second.iter().find(|n| n.0 == pattern_id).unwrap();
        assert_eq!(
            pattern_row.1, "forgotten",
            "an unchanged note stays untouched"
        );
        let pattern_updated2: chrono::DateTime<chrono::Utc> = tx
            .query_one(
                "SELECT updated_at FROM memory_chunks WHERE id = $1",
                &[&pattern_id],
            )
            .unwrap()
            .get(0);
        assert_eq!(
            pattern_updated2, pattern_updated,
            "no UPDATE for unchanged rows"
        );
        let old = tx
            .query_one(
                "SELECT status, superseded_by, valid_to FROM memory_chunks WHERE id = $1",
                &[&decision_id],
            )
            .unwrap();
        let old_status: String = old.get(0);
        let successor_id: Option<i64> = old.get(1);
        let old_valid_to: Option<chrono::DateTime<chrono::Utc>> = old.get(2);
        assert_eq!(old_status, "superseded");
        let successor_id = successor_id.expect("old decision points at its successor");
        let new = tx
            .query_one(
                "SELECT content, status, valid_from, tags FROM memory_chunks WHERE id = $1",
                &[&successor_id],
            )
            .unwrap();
        let new_content: String = new.get(0);
        let new_status: String = new.get(1);
        let new_valid_from: chrono::DateTime<chrono::Utc> = new.get(2);
        let new_tags: Vec<String> = new.get(3);
        assert!(new_content.contains("PostgreSQL 16"));
        assert_eq!(new_status, "active");
        assert_eq!(
            old_valid_to,
            Some(new_valid_from),
            "old interval ends where the new starts"
        );
        assert!(new_tags.contains(&format!("file:{}", file.display())));
        let star: i64 = tx
            .query_one(
                "SELECT count(*) FROM favorites WHERE kind = 'memory' AND ref = $1::bigint::text",
                &[&successor_id],
            )
            .unwrap()
            .get(0);
        assert_eq!(star, 1, "the star moved to the successor");

        // A section removed from the file stays as history.
        std::fs::write(&file, decision2).unwrap();
        ingest_agent_memory_from(&mut tx, dir.path()).expect("third import");
        let third = notes(&mut tx, &file);
        assert!(third.len() >= second.len(), "no row is deleted");
        let gone = tx
            .query_one(
                "SELECT status, valid_to, superseded_by FROM memory_chunks WHERE id = $1",
                &[&pattern_id],
            )
            .unwrap();
        let gone_status: String = gone.get(0);
        let gone_valid_to: Option<chrono::DateTime<chrono::Utc>> = gone.get(1);
        let gone_by: Option<i64> = gone.get(2);
        // The pattern note was forgotten: forgotten appears in no answer, so
        // the vanished section must not bring it back as superseded history.
        assert_eq!(gone_status, "forgotten");
        assert_eq!(gone_valid_to, None);
        assert_eq!(gone_by, None);
    }

    /// Active rows among this file's notes plus `extra` (a successor without
    /// the file tag), sorted.
    fn active_ids<C: GenericClient>(client: &mut C, file: &Path, extra: i64) -> Vec<i64> {
        let tag = format!("file:{}", file.display());
        client
            .query(
                "SELECT id FROM memory_chunks \
                 WHERE ($1 = ANY(tags) OR id = $2) AND status = 'active' ORDER BY id",
                &[&tag, &extra],
            )
            .unwrap()
            .iter()
            .map(|r| r.get(0))
            .collect()
    }

    #[test]
    fn re_import_keeps_a_ui_replacement_until_the_file_changes() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping re_import_keeps_a_ui_replacement_until_the_file_changes: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("project").join("atlas").join("notes.md");
        std::fs::create_dir_all(file.parent().unwrap()).unwrap();
        let decision = "# Decision\nUse PostgreSQL with pgvector for every project store.\n";
        std::fs::write(&file, decision).unwrap();
        ingest_agent_memory_from(&mut tx, dir.path()).expect("first import");
        let imported = notes(&mut tx, &file)[0].0;
        let ui_text = "Use SQLite for the local store, PostgreSQL only on the server.";
        let (_, ui) = crate::server::supersede_memory(
            &mut tx,
            imported,
            &crate::server::Successor {
                content: ui_text,
                category: None,
                source_type: "ui_pin",
                kind: crate::server::SupersedeKind::Update,
                valid_from: None,
                valid_from_is_date: false,
                confidence: 1.0,
                tags: Vec::new(),
                source_at: None,
            },
        )
        .expect("replace in the UI");

        // (a) The file did not change: the UI replacement stays the answer.
        ingest_agent_memory_from(&mut tx, dir.path()).expect("re-import, file unchanged");
        assert_eq!(notes(&mut tx, &file).len(), 1, "no new row from the file");
        assert_eq!(active_ids(&mut tx, &file, ui), vec![ui]);

        // (b) The file changed: the newer file text replaces the UI note.
        let decision2 = "# Decision\nUse PostgreSQL 16 with pgvector for every project store.\n";
        std::fs::write(&file, decision2).unwrap();
        ingest_agent_memory_from(&mut tx, dir.path()).expect("re-import, file changed");
        let row = tx
            .query_one(
                "SELECT status, superseded_by FROM memory_chunks WHERE id = $1",
                &[&ui],
            )
            .unwrap();
        assert_eq!(row.get::<_, String>(0), "superseded");
        let successor: i64 = row
            .get::<_, Option<i64>>(1)
            .expect("the UI note points at the file's new text");
        let new = tx
            .query_one(
                "SELECT source_type, content, status FROM memory_chunks WHERE id = $1",
                &[&successor],
            )
            .unwrap();
        assert_eq!(new.get::<_, String>(0), "manual");
        assert!(new.get::<_, String>(1).contains("PostgreSQL 16"));
        assert_eq!(new.get::<_, String>(2), "active");
        assert_eq!(
            active_ids(&mut tx, &file, ui),
            vec![successor],
            "exactly one active row for the section"
        );
    }

    /// The newest text the file produced counts, not an older identical one,
    /// and a section that comes back after it vanished is live again.
    #[test]
    fn re_import_follows_a_section_back_and_forth() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping re_import_follows_a_section_back_and_forth: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("project").join("atlas").join("notes.md");
        std::fs::create_dir_all(file.parent().unwrap()).unwrap();
        let v1 = "# Decision\nUse PostgreSQL with pgvector for every project store.\n";
        let v2 = "# Decision\nUse PostgreSQL 16 with pgvector for every project store.\n";
        let other = "# Pattern\nWrite the failing test first, then the smallest change.\n";
        let active_text = |tx: &mut postgres::Transaction<'_>| -> Vec<String> {
            notes(tx, &file)
                .into_iter()
                .filter(|n| n.1 == "active")
                .map(|n| n.2)
                .collect()
        };
        for (step, text, want) in [
            ("v1", format!("{v1}{other}"), Some("pgvector for")),
            ("v2", format!("{v2}{other}"), Some("PostgreSQL 16")),
            (
                "back to v1",
                format!("{v1}{other}"),
                Some("PostgreSQL with"),
            ),
            ("removed", other.to_string(), None),
            ("re-added", format!("{v1}{other}"), Some("PostgreSQL with")),
        ] {
            std::fs::write(&file, text).unwrap();
            ingest_agent_memory_from(&mut tx, dir.path()).expect(step);
            let decisions: Vec<String> = active_text(&mut tx)
                .into_iter()
                .filter(|c| c.contains("] Decision"))
                .collect();
            match want {
                Some(w) => {
                    assert_eq!(decisions.len(), 1, "{step}: {decisions:?}");
                    assert!(decisions[0].contains(w), "{step}: {decisions:?}");
                }
                None => assert!(decisions.is_empty(), "{step}: {decisions:?}"),
            }
        }
    }
}
