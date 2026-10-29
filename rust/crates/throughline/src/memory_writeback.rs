//! Write distilled memory back into the project's own instruction file
//! (AGENTS.md / CLAUDE.md) so the next agent session starts with it.

use anyhow::{Context, Result};
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

/// Replace or append the Throughline block in the instruction file.
pub fn writeback<C: GenericClient>(
    client: &mut C,
    root: &Path,
    project: Option<&str>,
    limit: usize,
) -> Result<WritebackSummary> {
    let rows = client.query(
        r#"
        SELECT content FROM memory_chunks
        WHERE status = 'active'
          AND category IN ('decision', 'preference', 'project_context', 'workflow', 'error_solution')
          AND ($1::text IS NULL OR project_name = $1)
        ORDER BY created_at DESC
        LIMIT $2::bigint
        "#,
        &[&project, &(limit as i64)],
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
    let new_text =
        if let (Some(a), Some(b)) = (existing.find(MARKER_START), existing.find(MARKER_END)) {
            let end = b + MARKER_END.len();
            format!("{}{}{}", &existing[..a], block, &existing[end..])
        } else {
            let mut t = existing;
            if !t.ends_with('\n') {
                t.push('\n');
            }
            t.push('\n');
            t.push_str(&block);
            t
        };

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
}
