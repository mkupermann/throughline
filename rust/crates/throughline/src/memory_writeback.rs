//! Write distilled memory back into the project's own instruction file
//! (AGENTS.md / CLAUDE.md) so the next agent session starts with it.

use crate::access::{push_visibility, Actor};
use anyhow::{Context, Result};
use postgres::types::ToSql;
use postgres::GenericClient;
use std::path::{Path, PathBuf};

pub const MARKER_START: &str = "<!-- throughline:learned -->";
pub const MARKER_END: &str = "<!-- /throughline:learned -->";

#[derive(Debug, serde::Serialize)]
pub struct WritebackSummary {
    pub file: String,
    pub items_written: usize,
    pub created: bool,
    pub replaced: bool,
}

fn pick_file(root: &Path) -> PathBuf {
    for name in [
        "AGENTS.md",
        "CLAUDE.md",
        "AGENTS.local.md",
        "CLAUDE.local.md",
    ] {
        let p = root.join(name);
        if p.is_file() {
            return p;
        }
    }
    root.join("AGENTS.md")
}

fn render_block(items: &[String]) -> String {
    let mut s = String::new();
    s.push_str(MARKER_START);
    s.push('\n');
    s.push_str("## Learned (Throughline)\n\n");
    s.push_str(
        "Distilled from this project's session history. Update via `throughline writeback`.\n\n",
    );
    for item in items {
        s.push_str(&format!("- {item}\n"));
    }
    s.push_str(MARKER_END);
    s.push('\n');
    s
}

/// Put `block` in place of the existing marker block, or append it.
fn merge_block(existing: String, block: &str) -> String {
    let start = existing.find(MARKER_START);
    let end = start.and_then(|a| {
        existing[a..]
            .find(MARKER_END)
            .map(|b| a + b + MARKER_END.len())
    });
    if let (Some(a), Some(end)) = (start, end) {
        format!("{}{}{}", &existing[..a], block, &existing[end..])
    } else {
        let mut t = existing;
        if !t.ends_with('\n') {
            t.push('\n');
        }
        t.push('\n');
        t.push_str(block);
        t
    }
}

/// Replace or append the Throughline block in the instruction file.
///
/// Without `project` the folder name decides, the same way ingest names a
/// project after its last path segment. Notes of other projects never land
/// in this repository's file.
pub fn writeback<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    root: &Path,
    project: Option<&str>,
    limit: usize,
) -> Result<WritebackSummary> {
    let project = match project {
        Some(p) => p.to_string(),
        None => std::path::absolute(root)
            .ok()
            .and_then(|p| p.file_name().map(|n| n.to_string_lossy().into_owned()))
            .with_context(|| {
                format!(
                    "cannot tell the project of {}, pass --project",
                    root.display()
                )
            })?,
    };
    let limit = limit as i64;
    let mut params: Vec<&(dyn ToSql + Sync)> = vec![&project, &limit];
    let seen = push_visibility(actor, "mc", &mut params);
    let rows = client.query(
        &format!(
            r#"
        SELECT content FROM memory_chunks mc
        WHERE status = 'active'
          AND category IN ('decision', 'preference', 'project_context', 'workflow', 'error_solution')
          AND project_name = $1
          AND {seen}
        ORDER BY created_at DESC
        LIMIT $2::bigint
        "#
        ),
        &params,
    )?;
    let items: Vec<String> = rows
        .into_iter()
        .map(|r| {
            let s: String = r.get(0);
            let s = s.replace('\n', " ");
            s.chars().take(160).collect::<String>()
        })
        .collect();

    let path = pick_file(root);
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let created = !path.is_file();
    let existing = if created {
        String::new()
    } else {
        std::fs::read_to_string(&path).unwrap_or_default()
    };

    let block = render_block(&items);
    let replaced = existing.contains(MARKER_START);
    let new_text = merge_block(existing, &block);

    std::fs::write(&path, &new_text).with_context(|| format!("write {}", path.display()))?;

    Ok(WritebackSummary {
        file: path.display().to_string(),
        items_written: items.len(),
        created,
        replaced,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn renders_marker_block() {
        let b = render_block(&["use postgres".into()]);
        assert!(b.contains(MARKER_START));
        assert!(b.contains("use postgres"));
        assert!(b.contains(MARKER_END));
    }

    #[test]
    fn replaces_existing_block() {
        let old = format!("header\n{}\nold\n{}\nfooter\n", MARKER_START, MARKER_END);
        let a = old.find(MARKER_START).unwrap();
        let b = old.find(MARKER_END).unwrap() + MARKER_END.len();
        let block = render_block(&["new".into()]);
        let out = format!("{}{}{}", &old[..a], block, &old[b..]);
        assert!(out.contains("new"));
        assert!(!out.contains("old"));
        assert!(out.contains("header"));
        assert!(out.contains("footer"));
    }

    #[test]
    fn a_stray_end_marker_before_the_block_is_not_duplicated() {
        let old = format!("intro {MARKER_END}\nmine\n{MARKER_START}\nold\n{MARKER_END}\ntail\n");
        let out = merge_block(old, &render_block(&["new".into()]));
        assert_eq!(out.matches("mine").count(), 1, "{out}");
        assert!(!out.contains("old"), "{out}");
        assert!(out.contains("new") && out.contains("tail"), "{out}");
    }

    fn test_db() -> Option<postgres::Client> {
        crate::config::DbConfig::from_env().connect().ok()
    }

    #[test]
    fn writeback_without_project_takes_only_the_root_project() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping writeback_without_project_takes_only_the_root_project: no PostgreSQL reachable");
            return;
        };
        // Never committed: the rows vanish with the transaction.
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let mine = format!("wb-mine-{tag}");
        let other = format!("wb-other-{tag}");
        for (project, text) in [(&mine, "mine"), (&other, "other")] {
            tx.execute(
                "INSERT INTO memory_chunks (source_type, content, category, project_name, status)
                 VALUES ('manual', $1, 'decision', $2, 'active')",
                &[&format!("{text} note {tag}"), project],
            )
            .expect("seed note");
        }
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join(&mine);
        std::fs::create_dir_all(&root).unwrap();

        let summary =
            writeback(&mut tx, &Actor::shared_only(), &root, None, 1000).expect("writeback");
        let text = std::fs::read_to_string(&summary.file).unwrap();
        assert!(text.contains(&format!("mine note {tag}")), "{text}");
        assert!(!text.contains(&format!("other note {tag}")), "{text}");
    }

    #[test]
    fn writeback_skips_private() {
        use crate::access::team_test::{assert_hidden, note, two_accounts};
        let Some(mut client) = test_db() else {
            eprintln!("skipping writeback_skips_private: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let (a, _) = two_accounts(&mut tx);
        let project = format!("wb-{}", uuid::Uuid::new_v4().simple());
        let secret = format!("secret {project}");
        let p = Some(project.as_str());
        note(
            &mut tx,
            a.user_id,
            "private",
            &format!("decided {secret}"),
            "decision",
            p,
        );
        note(
            &mut tx,
            None,
            "shared",
            &format!("shared rule {project}"),
            "decision",
            p,
        );
        let dir = tempfile::tempdir().unwrap();
        let summary =
            writeback(&mut tx, &Actor::shared_only(), dir.path(), p, 1000).expect("writeback");
        let text = std::fs::read_to_string(&summary.file).unwrap();
        assert!(text.contains(&format!("shared rule {project}")), "{text}");
        assert_hidden(&text, &secret, "AGENTS.md");
        assert_eq!(summary.items_written, 1);
    }
}
