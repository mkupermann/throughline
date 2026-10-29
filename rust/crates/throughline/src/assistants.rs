//! Inventory of AI-assistant configuration files: instructions, skills,
//! agents and config, per tool and scope. Read-only, with secret masking.

use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::str::FromStr;

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/// File category.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    Instructions,
    Skills,
    Agents,
    Config,
}

impl Kind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Kind::Instructions => "instructions",
            Kind::Skills => "skills",
            Kind::Agents => "agents",
            Kind::Config => "config",
        }
    }
}

impl FromStr for Kind {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "instructions" => Ok(Kind::Instructions),
            "skills" => Ok(Kind::Skills),
            "agents" => Ok(Kind::Agents),
            "config" => Ok(Kind::Config),
            _ => Err(format!("unknown kind: {s}")),
        }
    }
}

/// One inventory entry.
#[derive(Debug, Clone, serde::Serialize)]
pub struct Entry {
    pub id: String,
    pub tool: String,
    pub kind: Kind,
    pub name: String,
    pub scope: String,
    pub project: Option<String>,
    pub path: PathBuf,
    pub bytes: u64,
    pub modified: Option<String>,
    pub summary: Option<String>,
    pub target: Option<String>,
}

/// Per-tool counts.
#[derive(Debug, serde::Serialize)]
pub struct ToolCounts {
    pub tool: String,
    pub instructions: usize,
    pub skills: usize,
    pub agents: usize,
    pub config: usize,
}

/// File content view with redaction metadata.
#[derive(Debug, serde::Serialize)]
pub struct FileView {
    pub id: String,
    pub path: String,
    pub name: String,
    pub kind: Kind,
    pub content: String,
    pub redacted: bool,
    pub truncated: bool,
    /// Set by the server when the file may be edited in the UI.
    pub editable: bool,
}

// ---------------------------------------------------------------------------
// Home
// ---------------------------------------------------------------------------

/// Assistant home: `THROUGHLINE_ASSISTANT_HOME` or `dirs::home_dir()`.
pub fn home() -> PathBuf {
    if let Ok(h) = std::env::var("THROUGHLINE_ASSISTANT_HOME") {
        return PathBuf::from(h);
    }
    dirs::home_dir().unwrap_or_else(|| PathBuf::from("/"))
}

/// Map a host-style path prefix to `home`.  Pure path logic.
///
/// Normalises `\` to `/` and compares the drive-letter prefix
/// case-insensitively.  When `target` starts with one of `prefixes`, the
/// matching prefix is replaced by `home`; otherwise the target is returned
/// unchanged.
pub fn map_host_prefix(target: &Path, prefixes: &[String], home: &Path) -> PathBuf {
    let target_str = target.to_string_lossy().replace('\\', "/");
    for prefix in prefixes {
        let prefix_str = prefix.replace('\\', "/");
        if prefix_str.is_empty() {
            continue;
        }
        if target_str.len() < prefix_str.len() {
            continue;
        }
        if !target_str[..prefix_str.len()].eq_ignore_ascii_case(&prefix_str) {
            continue;
        }
        if target_str.len() > prefix_str.len() && !target_str[prefix_str.len()..].starts_with('/') {
            continue;
        }
        let rest = target_str[prefix_str.len()..].trim_start_matches('/');
        if rest.is_empty() {
            return home.to_path_buf();
        }
        return home.join(rest);
    }
    target.to_path_buf()
}

// ---------------------------------------------------------------------------
// Allowlist
// ---------------------------------------------------------------------------

struct Pattern {
    tool: &'static str,
    kind: Kind,
    scope: &'static str,
    path: &'static str,
}

#[allow(clippy::type_complexity)]
const ALLOWLIST: &[Pattern] = &[
    // -- claude_code --
    Pattern {
        tool: "claude_code",
        kind: Kind::Instructions,
        scope: "global",
        path: ".claude/CLAUDE.md",
    },
    Pattern {
        tool: "claude_code",
        kind: Kind::Skills,
        scope: "global",
        path: ".claude/skills/*/SKILL.md",
    },
    Pattern {
        tool: "claude_code",
        kind: Kind::Agents,
        scope: "global",
        path: ".claude/agents/*.md",
    },
    Pattern {
        tool: "claude_code",
        kind: Kind::Instructions,
        scope: "global",
        path: ".claude/commands/*.md",
    },
    Pattern {
        tool: "claude_code",
        kind: Kind::Config,
        scope: "global",
        path: ".claude/settings.json",
    },
    Pattern {
        tool: "claude_code",
        kind: Kind::Instructions,
        scope: "project",
        path: "CLAUDE.md",
    },
    Pattern {
        tool: "claude_code",
        kind: Kind::Instructions,
        scope: "project",
        path: "CLAUDE.local.md",
    },
    Pattern {
        tool: "claude_code",
        kind: Kind::Skills,
        scope: "project",
        path: ".claude/skills/*/SKILL.md",
    },
    Pattern {
        tool: "claude_code",
        kind: Kind::Agents,
        scope: "project",
        path: ".claude/agents/*.md",
    },
    Pattern {
        tool: "claude_code",
        kind: Kind::Instructions,
        scope: "project",
        path: ".claude/commands/*.md",
    },
    Pattern {
        tool: "claude_code",
        kind: Kind::Config,
        scope: "project",
        path: ".claude/settings.json",
    },
    // -- codex --
    Pattern {
        tool: "codex",
        kind: Kind::Instructions,
        scope: "global",
        path: ".codex/AGENTS.md",
    },
    Pattern {
        tool: "codex",
        kind: Kind::Config,
        scope: "global",
        path: ".codex/config.toml",
    },
    Pattern {
        tool: "codex",
        kind: Kind::Skills,
        scope: "global",
        path: ".codex/skills/*/SKILL.md",
    },
    Pattern {
        tool: "codex",
        kind: Kind::Instructions,
        scope: "global",
        path: ".codex/rules/*",
    },
    Pattern {
        tool: "codex",
        kind: Kind::Instructions,
        scope: "project",
        path: "AGENTS.md",
    },
    Pattern {
        tool: "codex",
        kind: Kind::Instructions,
        scope: "project",
        path: "AGENTS.local.md",
    },
    // -- cursor --
    Pattern {
        tool: "cursor",
        kind: Kind::Agents,
        scope: "global",
        path: ".cursor/agents/*",
    },
    Pattern {
        tool: "cursor",
        kind: Kind::Skills,
        scope: "global",
        path: ".cursor/skills-cursor/*/SKILL.md",
    },
    Pattern {
        tool: "cursor",
        kind: Kind::Instructions,
        scope: "project",
        path: ".cursorrules",
    },
    Pattern {
        tool: "cursor",
        kind: Kind::Instructions,
        scope: "project",
        path: ".cursor/rules/*.mdc",
    },
    // -- vibe --
    Pattern {
        tool: "vibe",
        kind: Kind::Instructions,
        scope: "global",
        path: ".vibe/AGENTS.md",
    },
    Pattern {
        tool: "vibe",
        kind: Kind::Config,
        scope: "global",
        path: ".vibe/config.toml",
    },
    Pattern {
        tool: "vibe",
        kind: Kind::Agents,
        scope: "global",
        path: ".vibe/agents/*",
    },
    Pattern {
        tool: "vibe",
        kind: Kind::Instructions,
        scope: "project",
        path: "AGENTS.md",
    },
    // -- aider --
    Pattern {
        tool: "aider",
        kind: Kind::Config,
        scope: "global",
        path: ".aider.conf.yml",
    },
    Pattern {
        tool: "aider",
        kind: Kind::Config,
        scope: "project",
        path: ".aider.conf.yml",
    },
    Pattern {
        tool: "aider",
        kind: Kind::Instructions,
        scope: "project",
        path: "CONVENTIONS.md",
    },
    // -- opencode --
    Pattern {
        tool: "opencode",
        kind: Kind::Config,
        scope: "global",
        path: ".config/opencode/opencode.jsonc",
    },
    Pattern {
        tool: "opencode",
        kind: Kind::Instructions,
        scope: "global",
        path: ".config/opencode/AGENTS.md",
    },
    Pattern {
        tool: "opencode",
        kind: Kind::Config,
        scope: "project",
        path: "opencode.json",
    },
    Pattern {
        tool: "opencode",
        kind: Kind::Instructions,
        scope: "project",
        path: "AGENTS.md",
    },
    // -- cline --
    Pattern {
        tool: "cline",
        kind: Kind::Instructions,
        scope: "project",
        path: ".clinerules",
    },
    // -- windsurf --
    Pattern {
        tool: "windsurf",
        kind: Kind::Instructions,
        scope: "global",
        path: ".codeium/windsurf/memories/global_rules.md",
    },
    Pattern {
        tool: "windsurf",
        kind: Kind::Instructions,
        scope: "project",
        path: ".windsurfrules",
    },
    Pattern {
        tool: "windsurf",
        kind: Kind::Instructions,
        scope: "project",
        path: ".windsurf/rules/*",
    },
    // -- continue_dev --
    Pattern {
        tool: "continue_dev",
        kind: Kind::Config,
        scope: "global",
        path: ".continue/config.yaml",
    },
    Pattern {
        tool: "continue_dev",
        kind: Kind::Instructions,
        scope: "global",
        path: ".continue/rules/*",
    },
    Pattern {
        tool: "continue_dev",
        kind: Kind::Instructions,
        scope: "project",
        path: ".continue/rules/*",
    },
    // -- zed --
    Pattern {
        tool: "zed",
        kind: Kind::Config,
        scope: "global",
        path: ".config/zed/settings.json",
    },
    Pattern {
        tool: "zed",
        kind: Kind::Instructions,
        scope: "project",
        path: ".rules",
    },
    // -- hermes --
    Pattern {
        tool: "hermes",
        kind: Kind::Config,
        scope: "global",
        path: ".hermes/config.*",
    },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn is_excluded(name: &str) -> bool {
    matches!(
        name,
        "node_modules" | ".git" | "target" | "dist" | "build" | "__pycache__" | ".venv"
    )
}

fn is_binary(path: &Path) -> bool {
    use std::io::Read;
    let Ok(mut f) = std::fs::File::open(path) else {
        return true;
    };
    let mut buf = [0u8; 512];
    let Ok(n) = f.read(&mut buf) else {
        return true;
    };
    buf[..n].contains(&0)
}

/// Strip the Windows verbatim prefix `\\?\` so paths display and compare
/// without the extra layer.
fn strip_verbatim(path: &Path) -> PathBuf {
    let s = path.to_string_lossy();
    if let Some(rest) = s.strip_prefix(r"\\?\") {
        PathBuf::from(rest)
    } else {
        path.to_path_buf()
    }
}

/// Resolve a symlink to a target that lives under `home` or one of `roots`.
///
/// When `path` is a symlink, its target is read, mapped through
/// `map_host_prefix` (using `THROUGHLINE_HOST_HOME` prefixes) and
/// canonicalised.  The canonical result is returned only when it lies under
/// `home` or one of `roots`.  For non-symlinks the path itself is returned.
fn resolve_link(path: &Path, home: &Path, roots: &[PathBuf]) -> Option<PathBuf> {
    let meta = std::fs::symlink_metadata(path).ok()?;
    if !meta.file_type().is_symlink() {
        return Some(path.to_path_buf());
    }
    let target = std::fs::read_link(path).ok()?;
    let resolved = if target.is_absolute() {
        target
    } else {
        path.parent()?.join(target)
    };
    let prefixes: Vec<String> = std::env::var("THROUGHLINE_HOST_HOME")
        .map(|s| {
            s.split(';')
                .filter(|p| !p.is_empty())
                .map(|p| p.to_string())
                .collect()
        })
        .unwrap_or_default();
    let mapped = map_host_prefix(&resolved, &prefixes, home);
    let canonical = std::fs::canonicalize(&mapped).ok()?;
    if let Ok(ch) = std::fs::canonicalize(home) {
        if canonical.starts_with(&ch) {
            return Some(strip_verbatim(&canonical));
        }
    }
    for root in roots {
        if let Ok(cr) = std::fs::canonicalize(root) {
            if canonical.starts_with(&cr) {
                return Some(strip_verbatim(&canonical));
            }
        }
    }
    None
}

/// Fully resolve `path`, following directory and file symlinks.
/// Returns `None` when a symlink target falls outside `home` / `roots`.
fn resolve_full(path: &Path, home: &Path, roots: &[PathBuf]) -> Option<PathBuf> {
    // If the final component is a symlink, resolve it directly.
    if let Ok(meta) = std::fs::symlink_metadata(path) {
        if meta.file_type().is_symlink() {
            return resolve_link(path, home, roots);
        }
    }
    // Otherwise walk up to resolve directory symlinks in the parent chain.
    let parent = path.parent()?;
    let file_name = path.file_name()?;
    if parent.as_os_str().is_empty() {
        return Some(path.to_path_buf());
    }
    let resolved_parent = resolve_link(parent, home, roots)?;
    let candidate = resolved_parent.join(file_name);
    // The file itself may still be a symlink after the parent was resolved.
    if let Ok(meta) = std::fs::symlink_metadata(&candidate) {
        if meta.file_type().is_symlink() {
            return resolve_link(&candidate, home, roots);
        }
    }
    Some(candidate)
}

fn is_valid_file(path: &Path) -> bool {
    let Ok(meta) = std::fs::metadata(path) else {
        return false;
    };
    if !meta.is_file() || meta.len() > 1_000_000 {
        return false;
    }
    !is_binary(path)
}

pub fn make_id(path: &Path) -> String {
    let mut hasher = Sha256::new();
    hasher.update(path.to_string_lossy().as_bytes());
    let hash = hasher.finalize();
    let mut id = String::with_capacity(16);
    for b in hash.iter().take(8) {
        id.push_str(&format!("{b:02x}"));
    }
    id
}

fn unquote(s: &str) -> String {
    let s = s.trim();
    if s.len() >= 2
        && ((s.starts_with('"') && s.ends_with('"')) || (s.starts_with('\'') && s.ends_with('\'')))
    {
        s[1..s.len() - 1].to_string()
    } else {
        s.to_string()
    }
}

/// Parse YAML frontmatter: returns `(name, description)`.
fn parse_frontmatter(content: &str) -> (Option<String>, Option<String>) {
    let mut name = None;
    let mut description = None;
    let mut after_first_dash = false;
    for line in content.lines() {
        if line.trim() == "---" {
            if after_first_dash {
                break;
            }
            after_first_dash = true;
            continue;
        }
        if !after_first_dash {
            continue;
        }
        if let Some(rest) = line.strip_prefix("name:") {
            name = Some(unquote(rest));
        } else if let Some(rest) = line.strip_prefix("description:") {
            description = Some(unquote(rest));
        }
    }
    (name, description)
}

fn match_glob(name: &str, pattern: &str) -> bool {
    if pattern == "*" {
        return true;
    }
    if let Some(suffix) = pattern.strip_prefix("*.") {
        return name.ends_with(suffix) && name.len() > suffix.len();
    }
    if let Some(prefix) = pattern.strip_suffix(".*") {
        return name.starts_with(prefix) && name.len() > prefix.len();
    }
    name == pattern
}

fn glob_dir(dir: &Path, glob: &str, home: &Path, roots: &[PathBuf]) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut out: Vec<PathBuf> = entries
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .map(|n| match_glob(&n.to_string_lossy(), glob))
                .unwrap_or(false)
        })
        .filter(|p| resolve_link(p, home, roots).is_some_and(|r| is_valid_file(&r)))
        .collect();
    out.sort();
    out
}

fn two_level_glob(dir: &Path, literal: &str, home: &Path, roots: &[PathBuf]) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut out: Vec<PathBuf> = Vec::new();
    for entry in entries.filter_map(|e| e.ok()) {
        let path = entry.path();
        if is_excluded(&entry.file_name().to_string_lossy()) {
            continue;
        }
        // Resolve directory symlinks (host-path mapping included).
        let Some(resolved_dir) = resolve_link(&path, home, roots) else {
            continue;
        };
        if !resolved_dir.is_dir() {
            continue;
        }
        let candidate = path.join(literal);
        let resolved_candidate = resolved_dir.join(literal);
        if is_valid_file(&resolved_candidate) {
            out.push(candidate);
        }
    }
    out.sort();
    out
}

/// Expand a glob pattern relative to `base`.  `*` appears at most once.
fn expand_pattern(base: &Path, pattern: &str, home: &Path, roots: &[PathBuf]) -> Vec<PathBuf> {
    let parts: Vec<&str> = pattern.split('/').collect();
    let star_idx = parts.iter().position(|p| p.contains('*'));
    match star_idx {
        None => {
            let path: PathBuf = parts.iter().fold(base.to_path_buf(), |acc, p| acc.join(p));
            if resolve_link(&path, home, roots).is_some_and(|r| is_valid_file(&r)) {
                vec![path]
            } else {
                Vec::new()
            }
        }
        Some(idx) if idx == parts.len() - 1 => {
            let dir: PathBuf = parts[..idx]
                .iter()
                .fold(base.to_path_buf(), |acc, p| acc.join(p));
            glob_dir(&dir, parts[idx], home, roots)
        }
        Some(idx) => {
            let dir: PathBuf = parts[..idx]
                .iter()
                .fold(base.to_path_buf(), |acc, p| acc.join(p));
            two_level_glob(&dir, parts[idx + 1], home, roots)
        }
    }
}

fn entry_for_file(
    path: &Path,
    home: &Path,
    roots: &[PathBuf],
    tool: &str,
    kind: Kind,
    scope: &str,
    project: Option<&str>,
) -> Option<Entry> {
    // Fully resolve the path (directory and file symlinks).
    let resolved = resolve_full(path, home, roots)?;
    if !is_valid_file(&resolved) {
        return None;
    }
    let meta = std::fs::metadata(&resolved).ok()?;
    let bytes = meta.len();
    let id = make_id(path);
    let mut name = path
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default();
    let mut summary = None;

    if kind == Kind::Skills {
        let content = std::fs::read_to_string(&resolved).ok()?;
        let (fm_name, fm_desc) = parse_frontmatter(&content);
        if let Some(n) = fm_name {
            name = n;
        } else {
            name = path
                .parent()
                .and_then(|p| p.file_name())
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or(name);
        }
        summary = fm_desc;
    }

    let modified = meta.modified().ok().map(|t| {
        let dt: chrono::DateTime<chrono::Utc> = t.into();
        dt.to_rfc3339()
    });

    let target = if resolved != path {
        Some(resolved.to_string_lossy().to_string())
    } else {
        None
    };

    Some(Entry {
        id,
        tool: tool.to_string(),
        kind,
        name,
        scope: scope.to_string(),
        project: project.map(|p| p.to_string()),
        path: path.to_path_buf(),
        bytes,
        modified,
        summary,
        target,
    })
}

// ---------------------------------------------------------------------------
// Project roots
// ---------------------------------------------------------------------------

/// Keep only plausible project-root paths from a corpus dump.
///
/// A candidate is kept when it exists, is a directory, has at least three
/// path components (`/Users/x/y`, `C:\Users\x\y`), is neither `home` itself
/// nor an ancestor of `home`, and does not sit inside a dot-folder directly
/// under `home` (e.g. `home/.claude`, `home/.codex`).
pub fn filter_corpus_roots(candidates: Vec<PathBuf>, home: &Path) -> Vec<PathBuf> {
    candidates
        .into_iter()
        .filter(|p| {
            if !p.is_dir() {
                return false;
            }
            if p.components().count() < 3 {
                return false;
            }
            if p == home {
                return false;
            }
            if home.starts_with(p) {
                return false;
            }
            if let Ok(rel) = p.strip_prefix(home) {
                if let Some(first) = rel.components().next() {
                    if let Some(name) = first.as_os_str().to_str() {
                        if name.starts_with('.') {
                            return false;
                        }
                    }
                }
            }
            true
        })
        .collect()
}

fn has_project_files(dir: &Path, home: &Path, roots: &[PathBuf]) -> bool {
    ALLOWLIST
        .iter()
        .filter(|p| p.scope == "project")
        .any(|p| !expand_pattern(dir, p.path, home, roots).is_empty())
}

fn find_project_roots(
    dir: &Path,
    depth: usize,
    max_depth: usize,
    home: &Path,
    roots: &[PathBuf],
    out: &mut Vec<PathBuf>,
) {
    if depth > max_depth {
        return;
    }
    if depth > 0 && has_project_files(dir, home, roots) && !out.contains(&dir.to_path_buf()) {
        out.push(dir.to_path_buf());
    }
    if depth == max_depth {
        return;
    }
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.filter_map(|e| e.ok()) {
        let path = entry.path();
        if path.is_dir() && !is_excluded(&entry.file_name().to_string_lossy()) {
            find_project_roots(&path, depth + 1, max_depth, home, roots, out);
        }
    }
}

/// Scan-roots sub-dirs (depth ≤ 2) with at least one hit file, plus corpus paths.
pub fn project_roots(client: Option<&mut postgres::Client>) -> Vec<PathBuf> {
    let home = home();
    let scan_roots = crate::project_instructions::default_roots();
    let mut roots: Vec<PathBuf> = Vec::new();

    if let Some(client) = client {
        if let Ok(corpus) = crate::project_instructions::roots_from_corpus(client) {
            for r in filter_corpus_roots(corpus, &home) {
                if !roots.contains(&r) {
                    roots.push(r);
                }
            }
        }
    }

    for sr in &scan_roots {
        find_project_roots(sr, 0, 2, &home, &scan_roots, &mut roots);
    }
    roots
}

// ---------------------------------------------------------------------------
// Inventory
// ---------------------------------------------------------------------------

/// Build fresh inventory from the allowlist.  No caching, no DB writes.
pub fn inventory(client: Option<&mut postgres::Client>) -> Vec<Entry> {
    let home = home();
    let scan_roots = crate::project_instructions::default_roots();
    let roots = project_roots(client);
    let mut entries = Vec::new();

    for pat in ALLOWLIST {
        match pat.scope {
            "global" => {
                for f in expand_pattern(&home, pat.path, &home, &scan_roots) {
                    if let Some(e) =
                        entry_for_file(&f, &home, &scan_roots, pat.tool, pat.kind, "global", None)
                    {
                        entries.push(e);
                    }
                }
            }
            "project" => {
                for root in &roots {
                    let proj_name = root.file_name().map(|n| n.to_string_lossy().to_string());
                    for f in expand_pattern(root, pat.path, &home, &scan_roots) {
                        if let Some(e) = entry_for_file(
                            &f,
                            &home,
                            &scan_roots,
                            pat.tool,
                            pat.kind,
                            "project",
                            proj_name.as_deref(),
                        ) {
                            entries.push(e);
                        }
                    }
                }
            }
            _ => {}
        }
    }
    entries
}

// ---------------------------------------------------------------------------
// Counts
// ---------------------------------------------------------------------------

/// Per-tool counts in registry order; tools with sum 0 are omitted.
pub fn counts(entries: &[Entry]) -> Vec<ToolCounts> {
    let order: Vec<String> = crate::adapters::registry::built_ins()
        .iter()
        .map(|a| a.name().to_string())
        .collect();

    let mut map = std::collections::HashMap::<String, [usize; 4]>::new();
    for e in entries {
        let slot = map.entry(e.tool.clone()).or_default();
        match e.kind {
            Kind::Instructions => slot[0] += 1,
            Kind::Skills => slot[1] += 1,
            Kind::Agents => slot[2] += 1,
            Kind::Config => slot[3] += 1,
        }
    }

    order
        .into_iter()
        .filter_map(|tool| {
            let c = map.get(&tool)?;
            let sum: usize = c.iter().sum();
            (sum > 0).then(|| ToolCounts {
                tool,
                instructions: c[0],
                skills: c[1],
                agents: c[2],
                config: c[3],
            })
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Read entry
// ---------------------------------------------------------------------------

const MAX_CONTENT: usize = 256 * 1024;

/// Look up entry by `id`, return its (masked) content.  `None` for unknown id.
pub fn read_entry(entries: &[Entry], id: &str) -> Option<FileView> {
    let entry = entries.iter().find(|e| e.id == id)?;
    let read_path = entry
        .target
        .as_deref()
        .map(PathBuf::from)
        .unwrap_or_else(|| entry.path.clone());
    let raw = std::fs::read_to_string(&read_path).ok()?;

    let (content, truncated) = if raw.len() > MAX_CONTENT {
        let mut end = MAX_CONTENT;
        while end > 0 && !raw.is_char_boundary(end) {
            end -= 1;
        }
        (raw[..end].to_string(), true)
    } else {
        (raw, false)
    };

    let (masked, mask_n) = mask_secrets(&content);
    let (redacted, pii_n) = crate::pii::redact(&masked);

    Some(FileView {
        id: entry.id.clone(),
        path: entry.path.to_string_lossy().to_string(),
        name: entry.name.clone(),
        kind: entry.kind,
        content: redacted,
        redacted: mask_n > 0 || pii_n > 0,
        truncated,
        editable: false,
    })
}

// ---------------------------------------------------------------------------
// Edit entry
// ---------------------------------------------------------------------------

/// Unmasked content of an inventory file for the editor, with its checksum.
#[derive(Debug, serde::Serialize)]
pub struct RawView {
    pub id: String,
    pub path: String,
    pub content: String,
    pub sha256: String,
}

/// Result of saving an edited file.
#[derive(Debug, serde::Serialize)]
pub struct SaveOutcome {
    pub id: String,
    pub path: String,
    pub sha256: String,
    pub backup: String,
}

/// Why a file cannot be edited or saved.
#[derive(Debug, PartialEq, Eq)]
pub enum SaveError {
    NotFound,
    HasSecrets,
    TooLarge,
    Conflict,
    Io(String),
}

/// Hex SHA-256 of a text.
pub fn content_sha256(text: &str) -> String {
    Sha256::digest(text.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// The inventory entry for `id` and the file to read and write. A symlinked
/// entry is written through to the file it points at, so the link stays.
fn entry_file<'a>(entries: &'a [Entry], id: &str) -> Result<(&'a Entry, PathBuf), SaveError> {
    let entry = entries
        .iter()
        .find(|e| e.id == id)
        .ok_or(SaveError::NotFound)?;
    let file = entry
        .target
        .as_deref()
        .map(PathBuf::from)
        .unwrap_or_else(|| entry.path.clone());
    Ok((entry, file))
}

/// Current text of a file that may be edited. A file with secrets stays
/// read-only, so its keys never travel to the browser unmasked.
fn editable_text(file: &Path) -> Result<String, SaveError> {
    let text = std::fs::read_to_string(file).map_err(|e| SaveError::Io(e.to_string()))?;
    if text.len() > MAX_CONTENT {
        return Err(SaveError::TooLarge);
    }
    if mask_secrets(&text).1 > 0 {
        return Err(SaveError::HasSecrets);
    }
    Ok(text)
}

/// Read an inventory file unmasked for editing, with the checksum a save
/// must present.
pub fn read_entry_raw(entries: &[Entry], id: &str) -> Result<RawView, SaveError> {
    let (entry, file) = entry_file(entries, id)?;
    let content = editable_text(&file)?;
    Ok(RawView {
        id: entry.id.clone(),
        path: entry.path.to_string_lossy().to_string(),
        sha256: content_sha256(&content),
        content,
    })
}

/// Save edited content over an inventory file. Refuses when the file changed
/// since it was read (`base_sha256`), copies the current text to
/// `backup_dir` first, and replaces the file atomically with its permissions.
pub fn save_entry(
    entries: &[Entry],
    id: &str,
    content: &str,
    base_sha256: &str,
    backup_dir: &Path,
) -> Result<SaveOutcome, SaveError> {
    let io = |e: std::io::Error| SaveError::Io(e.to_string());
    let (entry, file) = entry_file(entries, id)?;
    let current = editable_text(&file)?;
    if content.len() > MAX_CONTENT {
        return Err(SaveError::TooLarge);
    }
    if content_sha256(&current) != base_sha256 {
        return Err(SaveError::Conflict);
    }
    let name = file
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("file")
        .to_string();
    std::fs::create_dir_all(backup_dir).map_err(io)?;
    let stamp = chrono::Utc::now().format("%Y%m%d-%H%M%S-%3f");
    let backup = backup_dir.join(format!("{stamp}-{}-{name}", entry.id));
    std::fs::write(&backup, &current).map_err(io)?;

    let perms = std::fs::metadata(&file).map_err(io)?.permissions();
    let tmp = file.with_file_name(format!(".{name}.throughline-edit"));
    let replaced = std::fs::write(&tmp, content)
        .and_then(|_| std::fs::set_permissions(&tmp, perms))
        .and_then(|_| std::fs::rename(&tmp, &file));
    if let Err(e) = replaced {
        let _ = std::fs::remove_file(&tmp);
        return Err(io(e));
    }
    Ok(SaveOutcome {
        id: entry.id.clone(),
        path: entry.path.to_string_lossy().to_string(),
        sha256: content_sha256(content),
        backup: backup.to_string_lossy().to_string(),
    })
}

// ---------------------------------------------------------------------------
// Mask secrets
// ---------------------------------------------------------------------------

fn mask_line(line: &str, count: &mut usize) -> String {
    let Some(sep_pos) = line.find(['=', ':']) else {
        return line.to_string();
    };
    let key = line[..sep_pos].trim().to_lowercase();
    const TRIGGERS: &[&str] = &[
        "api_key",
        "apikey",
        "token",
        "secret",
        "password",
        "passwd",
        "auth",
        "bearer",
        "credential",
    ];
    if !TRIGGERS.iter().any(|t| key.contains(t)) {
        return line.to_string();
    }
    *count += 1;

    let after = &line[sep_pos + 1..];
    let ws_len = after.len() - after.trim_start().len();
    let ws = &after[..ws_len];
    let value = after.trim();

    let masked_val = if value.len() >= 2
        && ((value.starts_with('"') && value.ends_with('"'))
            || (value.starts_with('\'') && value.ends_with('\'')))
    {
        let q = &value[..1];
        format!("{q}\u{2022}\u{2022}\u{2022}{q}")
    } else {
        "\u{2022}\u{2022}\u{2022}".to_string()
    };

    format!("{}{}{}", &line[..=sep_pos], ws, masked_val)
}

fn mask_standalone(text: &str) -> (String, usize) {
    use std::sync::OnceLock;
    static RE: OnceLock<Vec<regex::Regex>> = OnceLock::new();
    let res = RE.get_or_init(|| {
        ["sk-", "ghp_", "xoxb-", "AKIA"]
            .iter()
            .map(|p| {
                regex::Regex::new(&format!(r"{p}[a-zA-Z0-9]{{16,}}")).expect("valid secret regex")
            })
            .collect()
    });

    let mut out = text.to_string();
    let mut n = 0;
    for re in res {
        let spans: Vec<(usize, usize)> = re.find_iter(&out).map(|m| (m.start(), m.end())).collect();
        for (s, e) in spans.into_iter().rev() {
            out.replace_range(s..e, "\u{2022}\u{2022}\u{2022}");
            n += 1;
        }
    }
    (out, n)
}

/// Mask key=value secrets and standalone tokens.  Returns `(text, replacements)`.
pub fn mask_secrets(text: &str) -> (String, usize) {
    let mut count = 0usize;
    let mut result = String::with_capacity(text.len());
    for (i, line) in text.lines().enumerate() {
        if i > 0 {
            result.push('\n');
        }
        result.push_str(&mask_line(line, &mut count));
    }
    if text.ends_with('\n') && !result.ends_with('\n') {
        result.push('\n');
    }
    let (result, standalone_n) = mask_standalone(&result);
    count += standalone_n;
    (result, count)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;
    use tempfile::TempDir;

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    /// Create a temp home dir, set the env var, return (tmp, guard).
    fn setup_home() -> (TempDir, std::sync::MutexGuard<'static, ()>) {
        let guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let tmp = TempDir::new().unwrap();
        std::env::set_var("THROUGHLINE_ASSISTANT_HOME", tmp.path());
        (tmp, guard)
    }

    fn teardown(tmp: TempDir, _guard: std::sync::MutexGuard<'static, ()>) {
        std::env::remove_var("THROUGHLINE_ASSISTANT_HOME");
        std::env::remove_var("THROUGHLINE_SCAN_ROOTS");
        drop(tmp);
    }

    // (a) global inventory: CLAUDE.md, SKILL.md with frontmatter, agents/bar.md
    #[test]
    fn inventory_global_entries() {
        let (tmp, guard) = setup_home();
        let h = tmp.path();
        std::fs::create_dir_all(h.join(".claude/skills/foo")).unwrap();
        std::fs::create_dir_all(h.join(".claude/agents")).unwrap();
        std::fs::write(h.join(".claude/CLAUDE.md"), "# Rules").unwrap();
        std::fs::write(
            h.join(".claude/skills/foo/SKILL.md"),
            "---\nname: foo-skill\ndescription: Does foo things\n---\nBody\n",
        )
        .unwrap();
        std::fs::write(h.join(".claude/agents/bar.md"), "Agent bar\n").unwrap();

        let entries = inventory(None);
        let claude_md = entries
            .iter()
            .find(|e| e.path.ends_with("CLAUDE.md") && e.scope == "global")
            .expect("CLAUDE.md entry");
        assert_eq!(claude_md.tool, "claude_code");
        assert_eq!(claude_md.kind, Kind::Instructions);

        let skill = entries
            .iter()
            .find(|e| e.kind == Kind::Skills)
            .expect("skill entry");
        assert_eq!(skill.name, "foo-skill");
        assert_eq!(skill.summary.as_deref(), Some("Does foo things"));
        assert_eq!(skill.tool, "claude_code");

        let agent = entries
            .iter()
            .find(|e| e.kind == Kind::Agents)
            .expect("agent entry");
        assert_eq!(agent.name, "bar.md");
        assert_eq!(agent.tool, "claude_code");

        teardown(tmp, guard);
    }

    // (b) project scope: CLAUDE.md + .cursorrules
    #[test]
    fn inventory_project_entries() {
        let (tmp, guard) = setup_home();
        let scan = tmp.path().join("scanroot");
        let proj = scan.join("myrepo");
        std::fs::create_dir_all(&proj).unwrap();
        std::fs::write(proj.join("CLAUDE.md"), "rules\n").unwrap();
        std::fs::write(proj.join(".cursorrules"), "cursor rules\n").unwrap();
        std::env::set_var("THROUGHLINE_SCAN_ROOTS", &scan);

        let entries = inventory(None);

        let cm = entries
            .iter()
            .find(|e| e.tool == "claude_code" && e.scope == "project")
            .expect("project CLAUDE.md");
        assert_eq!(cm.project.as_deref(), Some("myrepo"));
        assert_eq!(cm.kind, Kind::Instructions);

        let cr = entries
            .iter()
            .find(|e| e.tool == "cursor" && e.scope == "project")
            .expect("project .cursorrules");
        assert_eq!(cr.project.as_deref(), Some("myrepo"));

        teardown(tmp, guard);
    }

    // (c) mask_secrets
    #[test]
    fn mask_secrets_masks_keys_and_leaves_plain() {
        let input = concat!(
            "api_key = \"sk-abc123def456ghi\"\n",
            "OPENAI_API_KEY: xyzsecret\n",
            "token=mytoken123\n",
            "model = \"gpt-5\"\n",
        );
        let (out, n) = mask_secrets(input);
        assert!(
            out.contains("api_key = \"\u{2022}\u{2022}\u{2022}\""),
            "{out}"
        );
        assert!(
            out.contains("OPENAI_API_KEY: \u{2022}\u{2022}\u{2022}"),
            "{out}"
        );
        assert!(out.contains("token=\u{2022}\u{2022}\u{2022}"), "{out}");
        assert!(out.contains("model = \"gpt-5\""), "{out}");
        assert!(!out.contains("sk-abc"), "{out}");
        assert_eq!(n, 3, "three replacements expected, got {n}");
    }

    // (g) editing: save keeps a backup, refuses conflicts and secret files
    fn claude_md_entry(h: &Path, text: &str) -> (Vec<Entry>, String) {
        std::fs::create_dir_all(h.join(".claude")).unwrap();
        std::fs::write(h.join(".claude/CLAUDE.md"), text).unwrap();
        let entries = inventory(None);
        let id = entries
            .iter()
            .find(|e| e.path.ends_with(".claude/CLAUDE.md"))
            .unwrap()
            .id
            .clone();
        (entries, id)
    }

    #[test]
    fn save_entry_writes_content_and_keeps_a_backup() {
        let (tmp, guard) = setup_home();
        let h = tmp.path().to_path_buf();
        let (entries, id) = claude_md_entry(&h, "old rules\n");
        let raw = read_entry_raw(&entries, &id).unwrap();
        assert_eq!(raw.content, "old rules\n");
        let out = save_entry(
            &entries,
            &id,
            "new rules\n",
            &raw.sha256,
            &h.join("backups"),
        )
        .unwrap();
        assert_eq!(
            std::fs::read_to_string(h.join(".claude/CLAUDE.md")).unwrap(),
            "new rules\n"
        );
        assert_eq!(std::fs::read_to_string(&out.backup).unwrap(), "old rules\n");
        assert_eq!(out.sha256, content_sha256("new rules\n"));
        teardown(tmp, guard);
    }

    #[cfg(unix)]
    #[test]
    fn save_entry_keeps_file_permissions() {
        use std::os::unix::fs::PermissionsExt;
        let (tmp, guard) = setup_home();
        let h = tmp.path().to_path_buf();
        let (entries, id) = claude_md_entry(&h, "rules\n");
        let file = h.join(".claude/CLAUDE.md");
        std::fs::set_permissions(&file, std::fs::Permissions::from_mode(0o640)).unwrap();
        let raw = read_entry_raw(&entries, &id).unwrap();
        save_entry(&entries, &id, "rules v2\n", &raw.sha256, &h.join("backups")).unwrap();
        let mode = std::fs::metadata(&file).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o640);
        teardown(tmp, guard);
    }

    #[test]
    fn save_entry_refuses_a_file_changed_since_it_was_opened() {
        let (tmp, guard) = setup_home();
        let h = tmp.path().to_path_buf();
        let (entries, id) = claude_md_entry(&h, "v1\n");
        let raw = read_entry_raw(&entries, &id).unwrap();
        std::fs::write(h.join(".claude/CLAUDE.md"), "changed elsewhere\n").unwrap();
        let err = save_entry(&entries, &id, "mine\n", &raw.sha256, &h.join("backups")).unwrap_err();
        assert_eq!(err, SaveError::Conflict);
        assert_eq!(
            std::fs::read_to_string(h.join(".claude/CLAUDE.md")).unwrap(),
            "changed elsewhere\n"
        );
        teardown(tmp, guard);
    }

    #[test]
    fn files_with_secrets_stay_read_only() {
        let (tmp, guard) = setup_home();
        let h = tmp.path().to_path_buf();
        let secret = "# Rules\napi_key = \"sk-abc123def456ghi\"\n";
        let (entries, id) = claude_md_entry(&h, secret);
        assert_eq!(
            read_entry_raw(&entries, &id).unwrap_err(),
            SaveError::HasSecrets
        );
        let err = save_entry(
            &entries,
            &id,
            "# Rules\n",
            &content_sha256(secret),
            &h.join("backups"),
        )
        .unwrap_err();
        assert_eq!(err, SaveError::HasSecrets);
        assert_eq!(
            std::fs::read_to_string(h.join(".claude/CLAUDE.md")).unwrap(),
            secret
        );
        teardown(tmp, guard);
    }

    #[test]
    fn save_entry_unknown_id_is_not_found() {
        let dir = tempfile::tempdir().unwrap();
        let err = save_entry(&[], "deadbeefdeadbeef", "x", "y", dir.path()).unwrap_err();
        assert_eq!(err, SaveError::NotFound);
    }

    // (d) read_entry with unknown id
    #[test]
    fn read_entry_unknown_id_returns_none() {
        let entries = vec![];
        assert!(read_entry(&entries, "deadbeefdeadbeef").is_none());
    }

    // (e) file > 256 KB truncated
    #[test]
    fn read_entry_truncates_large_file() {
        let (tmp, guard) = setup_home();
        let h = tmp.path();
        std::fs::create_dir_all(h.join(".claude")).unwrap();
        let big = "x".repeat(300 * 1024);
        std::fs::write(h.join(".claude/CLAUDE.md"), &big).unwrap();

        let entries = inventory(None);
        let entry = entries
            .iter()
            .find(|e| e.path.ends_with("CLAUDE.md"))
            .unwrap();
        let view = read_entry(&entries, &entry.id).unwrap();
        assert!(view.truncated);
        assert!(view.content.len() <= MAX_CONTENT);

        teardown(tmp, guard);
    }

    // (f) symlinks and binary files are excluded
    #[test]
    fn inventory_skips_symlinks_and_binaries() {
        let (tmp, guard) = setup_home();
        let h = tmp.path();
        std::env::set_var("THROUGHLINE_SCAN_ROOTS", h.join("__empty__"));
        std::fs::create_dir_all(h.join(".claude")).unwrap();
        std::fs::write(h.join(".claude/CLAUDE.md"), "ok\n").unwrap();
        // binary file (NUL byte in first 512 bytes)
        std::fs::write(h.join(".claude/settings.json"), b"\x00\x01\x02\x03").unwrap();
        // symlink (may fail without elevated privileges on Windows)
        let sym_result = {
            #[cfg(unix)]
            {
                std::os::unix::fs::symlink(
                    h.join(".claude/CLAUDE.md"),
                    h.join(".claude/CLAUDE.local.md"),
                )
            }
            #[cfg(windows)]
            {
                std::os::windows::fs::symlink_file(
                    h.join(".claude/CLAUDE.md"),
                    h.join(".claude/CLAUDE.local.md"),
                )
            }
        };

        let entries = inventory(None);
        assert!(
            !entries.iter().any(|e| e.path.ends_with("settings.json")),
            "binary file should be skipped"
        );
        assert!(entries.iter().any(|e| e.path.ends_with("CLAUDE.md")));
        if sym_result.is_ok() {
            assert!(
                !entries.iter().any(|e| e.path.ends_with("CLAUDE.local.md")),
                "symlink should be skipped"
            );
        }

        teardown(tmp, guard);
    }

    // (g) id is stable across two inventory runs
    #[test]
    fn id_is_stable_across_runs() {
        let (tmp, guard) = setup_home();
        let h = tmp.path();
        std::fs::create_dir_all(h.join(".claude")).unwrap();
        std::fs::write(h.join(".claude/CLAUDE.md"), "hello\n").unwrap();

        let run1 = inventory(None);
        let run2 = inventory(None);
        let id1 = run1
            .iter()
            .find(|e| e.path.ends_with("CLAUDE.md"))
            .unwrap()
            .id
            .clone();
        let id2 = run2
            .iter()
            .find(|e| e.path.ends_with("CLAUDE.md"))
            .unwrap()
            .id
            .clone();
        assert_eq!(id1, id2);
        assert_eq!(id1.len(), 16);

        teardown(tmp, guard);
    }

    // ── M17d: symlink / map_host_prefix / filter_corpus_roots ────────────

    /// Create a directory symlink (or file symlink).  Returns `Err` with raw
    /// OS error 1314 when Windows refuses without elevated privileges.
    #[cfg(unix)]
    fn try_symlink_dir(target: &Path, link: &Path) -> std::io::Result<()> {
        std::os::unix::fs::symlink(target, link)
    }
    #[cfg(windows)]
    fn try_symlink_dir(target: &Path, link: &Path) -> std::io::Result<()> {
        std::os::windows::fs::symlink_dir(target, link)
    }

    // AK 1: skill folder as symlink → captured, name from frontmatter, target set
    #[test]
    fn skill_symlink_under_scan_root_is_captured() {
        let (tmp, guard) = setup_home();
        let h = tmp.path();
        let scan = h.join("scanroot");
        let target_skill = scan.join("skills").join("myskill");
        std::fs::create_dir_all(&target_skill).unwrap();
        std::fs::write(
            target_skill.join("SKILL.md"),
            "---\nname: my-symlink-skill\ndescription: Symlinked\n---\nBody\n",
        )
        .unwrap();
        std::env::set_var("THROUGHLINE_SCAN_ROOTS", &scan);

        let skills_dir = h.join(".claude").join("skills");
        std::fs::create_dir_all(&skills_dir).unwrap();
        let link = skills_dir.join("myskill");
        if let Err(e) = try_symlink_dir(&target_skill, &link) {
            if e.raw_os_error() == Some(1314) {
                eprintln!("skipped: symlink creation failed with error 1314");
                teardown(tmp, guard);
                return;
            }
            panic!("symlink creation failed: {e}");
        }

        let entries = inventory(None);
        let skill = entries
            .iter()
            .find(|e| e.kind == Kind::Skills && e.name == "my-symlink-skill")
            .expect("symlinked skill should be captured");
        assert_eq!(skill.name, "my-symlink-skill");
        let target = skill.target.as_ref().expect("Entry.target must be set");
        assert!(
            target.contains("myskill"),
            "target should reference the skill dir: {target}"
        );

        teardown(tmp, guard);
    }

    // AK 2: symlink target outside home and scan roots → NOT captured
    #[test]
    fn skill_symlink_outside_home_and_roots_is_skipped() {
        let (tmp, guard) = setup_home();
        let h = tmp.path();
        let scan = h.join("scanroot");
        std::fs::create_dir_all(&scan).unwrap();
        std::env::set_var("THROUGHLINE_SCAN_ROOTS", &scan);

        // Target lives in a separate temp tree, not under home or scan roots.
        let outside = TempDir::new().unwrap();
        let outside_skill = outside.path().join("myskill");
        std::fs::create_dir_all(&outside_skill).unwrap();
        std::fs::write(
            outside_skill.join("SKILL.md"),
            "---\nname: outside-skill\ndescription: Outside\n---\nBody\n",
        )
        .unwrap();

        let skills_dir = h.join(".claude").join("skills");
        std::fs::create_dir_all(&skills_dir).unwrap();
        let link = skills_dir.join("myskill");
        if let Err(e) = try_symlink_dir(&outside_skill, &link) {
            if e.raw_os_error() == Some(1314) {
                eprintln!("skipped: symlink creation failed with error 1314");
                drop(outside);
                teardown(tmp, guard);
                return;
            }
            panic!("symlink creation failed: {e}");
        }

        let entries = inventory(None);
        assert!(
            !entries.iter().any(|e| e.name == "outside-skill"),
            "skill with target outside home/roots must not be captured"
        );

        drop(outside);
        teardown(tmp, guard);
    }

    // AK 3: map_host_prefix maps host paths and leaves unknowns unchanged
    #[test]
    fn map_host_prefix_maps_host_paths_and_leaves_unknown() {
        let home = PathBuf::from("/home/throughline");
        let prefixes = vec![
            "C:\\Users\\alice".to_string(),
            "/mnt/host/c/Users/alice".to_string(),
        ];

        let expected = home.join("x").join("y");

        // Windows-style target
        let t1 = Path::new("C:\\Users\\alice\\x\\y");
        assert_eq!(map_host_prefix(t1, &prefixes, &home), expected);

        // Unix-style target (Docker Desktop host mapping)
        let t2 = Path::new("/mnt/host/c/Users/alice/x/y");
        assert_eq!(map_host_prefix(t2, &prefixes, &home), expected);

        // Unknown target stays unchanged
        let t3 = Path::new("/opt/z");
        assert_eq!(
            map_host_prefix(t3, &prefixes, &home),
            PathBuf::from("/opt/z")
        );
    }

    // AK 4: filter_corpus_roots rejects nonsense, keeps a real project dir
    #[test]
    fn filter_corpus_roots_rejects_nonsense_paths() {
        let (tmp, guard) = setup_home();
        let h = tmp.path();
        // Existing temp dir with ≥ 3 components
        let valid = h.join("a").join("b").join("c");
        std::fs::create_dir_all(&valid).unwrap();
        let dot_dir = h.join(".claude");
        std::fs::create_dir_all(&dot_dir).unwrap();

        let candidates = vec![
            PathBuf::from("/"),
            PathBuf::from("/Users"),
            h.to_path_buf(),
            dot_dir.clone(),
            PathBuf::from("/nonexistent/path/xyz"),
            valid.clone(),
        ];

        let filtered = filter_corpus_roots(candidates, h);
        assert_eq!(filtered, vec![valid], "only the deep existing dir survives");

        teardown(tmp, guard);
    }
}
