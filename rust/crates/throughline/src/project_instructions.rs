//! R5 — index project instruction files (AGENTS.md, CLAUDE.md, .cursorrules).
//!
//! These are the cross-tool conventions teams actually share. Storing them as
//! `prompts` rows makes them searchable and MCP-visible without inventing a
//! new table.

use anyhow::Result;
use postgres::GenericClient;
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

/// Filenames treated as project instructions, in priority order.
pub const INSTRUCTION_FILES: &[&str] = &[
    "AGENTS.md",
    "CLAUDE.md",
    "CLAUDE.local.md",
    "AGENTS.local.md",
    ".cursorrules",
    ".clinerules",
    "CODESTYLE.md",
];

/// Roots to scan. `THROUGHLINE_SCAN_ROOTS` (path-separator list) overrides.
pub fn default_roots() -> Vec<PathBuf> {
    if let Ok(raw) = std::env::var("THROUGHLINE_SCAN_ROOTS") {
        return raw
            .split([';', ':'])
            .filter(|s| !s.trim().is_empty())
            .map(PathBuf::from)
            .collect();
    }
    let mut roots = Vec::new();
    if let Some(home) = dirs::home_dir() {
        for sub in [
            "Documents/GitHub",
            "Documents/Gitlab",
            "src",
            "code",
            "work",
            "projects",
        ] {
            let p = home.join(sub);
            if p.is_dir() {
                roots.push(p);
            }
        }
    }
    // Also scan project paths already seen in the corpus.
    roots
}

/// Depth-limited scan for instruction files under `root`.
pub fn discover_instructions(root: &Path, max_depth: usize) -> Vec<PathBuf> {
    let mut out = Vec::new();
    for entry in WalkDir::new(root)
        .max_depth(max_depth)
        .into_iter()
        .filter_entry(|e| {
            let name = e.file_name().to_string_lossy();
            !matches!(
                name.as_ref(),
                "node_modules" | ".git" | "target" | "dist" | "build" | "__pycache__" | ".venv"
            )
        })
        .filter_map(|e| e.ok())
    {
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
            if INSTRUCTION_FILES.contains(&name) {
                out.push(path.to_path_buf());
            }
        }
    }
    out.sort();
    out
}

#[derive(Debug, Default)]
pub struct ScanSummary {
    pub files_seen: usize,
    pub upserted: usize,
    pub skipped: usize,
    pub errors: usize,
}

/// Store each instruction file as a `prompts` row (name unique).
pub fn ingest_instructions<C: GenericClient>(
    client: &mut C,
    roots: &[PathBuf],
    max_depth: usize,
) -> Result<ScanSummary> {
    let mut summary = ScanSummary::default();
    for root in roots {
        if !root.exists() {
            continue;
        }
        for path in discover_instructions(root, max_depth) {
            summary.files_seen += 1;
            let content = match std::fs::read_to_string(&path) {
                Ok(c) => c,
                Err(_) => {
                    summary.errors += 1;
                    continue;
                }
            };
            if content.trim().len() < 20 {
                summary.skipped += 1;
                continue;
            }
            let name = path
                .file_name()
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or_default();
            // Unique by path so the same filename in two repos does not collide.
            let name = format!("{name}:{}", path.to_string_lossy());
            let path_str = path.to_string_lossy().to_string();
            let tags: Vec<String> = vec![
                "project_instruction".into(),
                path.file_name()
                    .map(|s| s.to_string_lossy().to_string())
                    .unwrap_or_default(),
            ];
            let n = client.execute(
                r#"
                INSERT INTO prompts (name, category, content, source_path, tags)
                VALUES ($1, 'project_instruction', $2, $3, $4)
                ON CONFLICT (name) DO UPDATE
                SET content = EXCLUDED.content,
                    source_path = EXCLUDED.source_path,
                    tags = EXCLUDED.tags,
                    updated_at = now()
                "#,
                &[&name, &content, &path_str, &tags],
            );
            match n {
                Ok(_) => summary.upserted += 1,
                Err(_) => {
                    summary.errors += 1;
                }
            }
        }
    }
    Ok(summary)
}

/// Roots derived from conversation project_paths (so ingest knows where work happens).
pub fn roots_from_corpus<C: GenericClient>(client: &mut C) -> Result<Vec<PathBuf>> {
    let rows = client.query(
        "SELECT DISTINCT project_path FROM conversations WHERE project_path IS NOT NULL AND project_path <> '' LIMIT 200",
        &[],
    )?;
    let mut roots = Vec::new();
    for r in rows {
        let p: String = r.get(0);
        let path = PathBuf::from(&p);
        // project_path may be a file cwd; take parent if it's not a dir we want.
        if path.is_dir() {
            roots.push(path);
        } else if let Some(parent) = path.parent() {
            if parent.as_os_str().len() > 1 {
                roots.push(parent.to_path_buf());
            }
        }
    }
    Ok(roots)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn instruction_files_are_recognised() {
        assert!(INSTRUCTION_FILES.contains(&"AGENTS.md"));
        assert!(INSTRUCTION_FILES.contains(&"CLAUDE.md"));
        assert!(INSTRUCTION_FILES.contains(&".cursorrules"));
    }

    #[test]
    fn discover_instructions_finds_known_files() {
        let tmp = std::env::temp_dir().join("tl_instr_test");
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(tmp.join("sub")).unwrap();
        std::fs::write(tmp.join("AGENTS.md"), "use cargo test").unwrap();
        std::fs::write(tmp.join("README.md"), "not an instruction").unwrap();
        std::fs::write(tmp.join("sub").join("CLAUDE.md"), "be concise").unwrap();
        let found = discover_instructions(&tmp, 3);
        let names: Vec<String> = found
            .iter()
            .filter_map(|p| p.file_name().map(|n| n.to_string_lossy().to_string()))
            .collect();
        assert!(names.contains(&"AGENTS.md".to_string()));
        assert!(names.contains(&"CLAUDE.md".to_string()));
        assert!(!names.contains(&"README.md".to_string()));
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn discover_instructions_skips_node_modules() {
        let tmp = std::env::temp_dir().join("tl_instr_skip_test");
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(tmp.join("node_modules")).unwrap();
        std::fs::write(tmp.join("node_modules").join("AGENTS.md"), "hidden").unwrap();
        std::fs::write(tmp.join("AGENTS.md"), "visible").unwrap();
        let found = discover_instructions(&tmp, 3);
        assert_eq!(found.len(), 1);
        assert!(found[0].ends_with("AGENTS.md"));
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn discover_instructions_respects_depth() {
        let tmp = std::env::temp_dir().join("tl_instr_depth_test");
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(tmp.join("a").join("b").join("c")).unwrap();
        std::fs::write(tmp.join("a").join("b").join("c").join("AGENTS.md"), "deep").unwrap();
        std::fs::write(tmp.join("AGENTS.md"), "shallow").unwrap();
        let shallow = discover_instructions(&tmp, 1);
        assert_eq!(shallow.len(), 1);
        let deep = discover_instructions(&tmp, 5);
        assert_eq!(deep.len(), 2);
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn default_roots_respects_env_override() {
        std::env::set_var("THROUGHLINE_SCAN_ROOTS", "/tmp/foo;/tmp/bar");
        let roots = default_roots();
        assert_eq!(roots.len(), 2);
        assert!(roots[0].to_string_lossy().contains("foo"));
        assert!(roots[1].to_string_lossy().contains("bar"));
        std::env::remove_var("THROUGHLINE_SCAN_ROOTS");
    }
}
