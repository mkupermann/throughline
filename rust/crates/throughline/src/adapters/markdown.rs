//! Markdown folders as a source.
//!
//! Reads every `.md` and `.markdown` file under the roots in
//! `THROUGHLINE_MARKDOWN_ROOTS` (colon-separated, `~` expanded against
//! `$HOME`). Without roots the adapter finds nothing, so `ingest --source all`
//! passes over it without a word. Discovery skips `.git`, `node_modules`,
//! `target` and `dist` folders, symlinks (files and folders) and files over
//! 1 MiB.
//!
//! One file is one conversation:
//! - `session_id` is a stable UUID of the file path;
//! - `project_path` is the root folder, or `markdown/<project>` when the
//!   frontmatter names a project, never the file path: the project name is
//!   the last segment of `project_path`. The file path is in the metadata;
//! - `summary` is the frontmatter title, else the first H1, else the file
//!   name;
//! - each message is a section, role `document`, cut at H1 to H3 headings
//!   (ATX `#` headings; setext underlines stay text). A `#` line inside a
//!   fenced code block is text. Text before the first heading is the first
//!   section. A section over 2,000 characters goes on in further messages,
//!   so the extraction, which cuts each line at 2,000 characters, sees all
//!   of it;
//! - messages carry no uuid, so the messages of a changed file are deleted
//!   and written again (the runner sees the change as a new SHA-256);
//! - message n of the file is stamped file mtime plus n µs, so times are
//!   distinct and ordered.
//!
//! Frontmatter is read by a small reader, not a YAML parser: `key: value`,
//! inline `[a, b]` lists and `- item` lists, for `title`, `tags`, `date` and
//! `project`. Wikilinks stay text.

use super::{stable_uuid, Adapter};
use crate::types::{NormalisedConversation, NormalisedMessage, Role};
use anyhow::Result;
use chrono::{DateTime, Duration, Utc};
use serde_json::{json, Map, Value};
use std::ffi::OsStr;
use std::path::{Component, Path, PathBuf};
use uuid::Uuid;
use walkdir::WalkDir;

/// Adapter name, as `ingest --source` takes it and `source_tool` stores it.
pub const NAME: &str = "markdown";
/// Colon-separated root folders; `~` is expanded against `$HOME`.
pub const ROOTS_ENV: &str = "THROUGHLINE_MARKDOWN_ROOTS";
/// Larger files are skipped.
pub const MAX_FILE_BYTES: u64 = 1024 * 1024;
/// A longer section is split into continuation messages of at most this many
/// characters.
pub const MAX_MESSAGE_CHARS: usize = 2_000;

/// Folders never read, wherever they sit below a root.
const SKIP_DIRS: [&str; 4] = [".git", "node_modules", "target", "dist"];

const NS: Uuid = Uuid::from_u128(0x13361d86_15b2_47e6_9a69_8e9d403b8de2);

/// The roots in a [`ROOTS_ENV`] value: split at `:`, blanks dropped, `~` and
/// `~/…` expanded against `home`, trailing slashes removed (they would leave
/// the project name empty), each root once.
pub fn parse_roots(raw: &str, home: Option<&Path>) -> Vec<PathBuf> {
    let mut roots: Vec<PathBuf> = Vec::new();
    for entry in raw.split(':').map(str::trim).filter(|e| !e.is_empty()) {
        let expanded = match (entry.strip_prefix('~'), home) {
            (Some(rest), Some(home)) if rest.is_empty() || rest.starts_with('/') => {
                home.join(rest.trim_start_matches('/'))
            }
            _ => PathBuf::from(entry),
        };
        let root: PathBuf = expanded.components().collect();
        if !roots.contains(&root) {
            roots.push(root);
        }
    }
    roots
}

/// The fields of a frontmatter block that the adapter keeps.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
struct Frontmatter {
    title: Option<String>,
    tags: Vec<String>,
    date: Option<String>,
    project: Option<String>,
}

impl Frontmatter {
    fn to_json(&self) -> Value {
        let mut out = Map::new();
        let mut put = |key: &str, value: &Option<String>| {
            if let Some(v) = value {
                out.insert(key.into(), json!(v));
            }
        };
        put("title", &self.title);
        put("date", &self.date);
        put("project", &self.project);
        if !self.tags.is_empty() {
            out.insert("tags".into(), json!(self.tags));
        }
        Value::Object(out)
    }
}

/// A leading `---` block closed by `---` or `...`, and the text after it.
/// Without a closing line there is no frontmatter and all of it is text.
fn read_frontmatter(text: &str) -> (Frontmatter, &str) {
    let mut lines = text.split_inclusive('\n');
    let Some(first) = lines.next().filter(|l| l.trim_end() == "---") else {
        return (Frontmatter::default(), text);
    };
    let mut offset = first.len();
    let mut block = Vec::new();
    for line in lines {
        offset += line.len();
        let bare = line.trim_end();
        if bare == "---" || bare == "..." {
            return (frontmatter_fields(&block), &text[offset..]);
        }
        block.push(bare);
    }
    (Frontmatter::default(), text)
}

fn frontmatter_fields(lines: &[&str]) -> Frontmatter {
    let mut fm = Frontmatter::default();
    // The key whose value goes on as `- item` lines.
    let mut list_of: Option<&str> = None;
    for line in lines {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        if let Some(item) = trimmed.strip_prefix('-') {
            if list_of == Some("tags") && (item.is_empty() || item.starts_with([' ', '\t'])) {
                let tag = unquote(item.trim());
                if !tag.is_empty() {
                    fm.tags.push(tag);
                }
            }
            continue;
        }
        list_of = None;
        // Nested mappings are not read.
        if line.starts_with([' ', '\t']) {
            continue;
        }
        let Some((key, value)) = line.split_once(':') else {
            continue;
        };
        let (key, value) = (key.trim(), value.trim());
        let scalar = Some(unquote(value)).filter(|v| !v.is_empty());
        match key {
            "title" => fm.title = scalar,
            "date" => fm.date = scalar,
            "project" => fm.project = scalar,
            "tags" if value.is_empty() => list_of = Some("tags"),
            "tags" => fm.tags = inline_list(value),
            _ => {}
        }
    }
    fm
}

fn unquote(value: &str) -> String {
    for q in ['"', '\''] {
        if value.len() >= 2 && value.starts_with(q) && value.ends_with(q) {
            return value[1..value.len() - 1].to_string();
        }
    }
    value.to_string()
}

/// `[a, b]` or `a, b`.
fn inline_list(value: &str) -> Vec<String> {
    let inner = value
        .strip_prefix('[')
        .and_then(|v| v.strip_suffix(']'))
        .unwrap_or(value);
    inner
        .split(',')
        .map(|t| unquote(t.trim()))
        .filter(|t| !t.is_empty())
        .collect()
}

/// One section of a file: its heading line and body, or the text before the
/// first heading (level 0, empty heading path).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Section {
    /// Heading level 1 to 3; 0 for the text before the first heading.
    pub level: usize,
    /// The texts of the enclosing headings, outermost first, this one last.
    pub heading_path: Vec<String>,
    /// From the heading line on, blank lines at both ends removed.
    pub text: String,
}

/// An open code fence: its character and run length.
struct Fence {
    ch: u8,
    len: usize,
}

fn indent(line: &str) -> usize {
    line.len() - line.trim_start_matches(' ').len()
}

/// A line that opens a fenced code block (CommonMark: up to three spaces,
/// three or more backticks or tildes; a backtick fence's info string holds
/// no backtick).
fn fence_open(line: &str) -> Option<Fence> {
    if indent(line) > 3 {
        return None;
    }
    let rest = line.trim_start_matches(' ');
    let ch = *rest.as_bytes().first()?;
    if ch != b'`' && ch != b'~' {
        return None;
    }
    let len = rest.bytes().take_while(|&b| b == ch).count();
    if len < 3 || (ch == b'`' && rest[len..].contains('`')) {
        return None;
    }
    Some(Fence { ch, len })
}

/// A line that closes `fence`: the same character, at least as many, and
/// nothing else.
fn fence_closes(line: &str, fence: &Fence) -> bool {
    if indent(line) > 3 {
        return false;
    }
    let rest = line.trim_start_matches(' ');
    let len = rest.bytes().take_while(|&b| b == fence.ch).count();
    len >= fence.len && rest[len..].trim().is_empty()
}

/// An ATX heading of level 1 to 3 and its text. `#hashtag`, an indented
/// code line and levels 4 to 6 are not section breaks.
fn heading(line: &str) -> Option<(usize, String)> {
    if indent(line) > 3 {
        return None;
    }
    let rest = line.trim_start_matches(' ');
    let level = rest.bytes().take_while(|&b| b == b'#').count();
    let after = &rest[level..];
    if !(1..=3).contains(&level) || !(after.is_empty() || after.starts_with([' ', '\t'])) {
        return None;
    }
    let text = after.trim();
    // An optional closing run of `#` after a space; `C#` keeps its hash.
    let open = text.trim_end_matches('#');
    let text = if open.is_empty() {
        ""
    } else if open.ends_with([' ', '\t']) {
        open.trim_end()
    } else {
        text
    };
    Some((level, text.to_string()))
}

/// Split a Markdown body at its H1 to H3 headings, outside fenced code.
pub fn split_sections(body: &str) -> Vec<Section> {
    let mut out = Vec::new();
    let mut open: Vec<(usize, String)> = Vec::new();
    let mut level = 0;
    let mut lines: Vec<&str> = Vec::new();
    let mut fence: Option<Fence> = None;
    for line in body.lines() {
        if let Some(f) = &fence {
            if fence_closes(line, f) {
                fence = None;
            }
        } else if let Some(f) = fence_open(line) {
            fence = Some(f);
        } else if let Some((l, text)) = heading(line) {
            push_section(&mut out, level, &open, &lines);
            open.retain(|(outer, _)| *outer < l);
            open.push((l, text));
            level = l;
            lines.clear();
        }
        lines.push(line);
    }
    push_section(&mut out, level, &open, &lines);
    out
}

fn push_section(out: &mut Vec<Section>, level: usize, open: &[(usize, String)], lines: &[&str]) {
    let Some(start) = lines.iter().position(|l| !l.trim().is_empty()) else {
        return;
    };
    out.push(Section {
        level,
        heading_path: open.iter().map(|(_, t)| t.clone()).collect(),
        text: lines[start..].join("\n").trim_end().to_string(),
    });
}

/// Cut a section into pieces of at most `max` characters: at the last line
/// break in the second half of a piece, else at the last space there, else
/// hard. Whitespace at the cut is dropped, no other text.
fn split_long(text: &str, max: usize) -> Vec<&str> {
    let mut out = Vec::new();
    let mut rest = text;
    while let Some((cut, _)) = rest.char_indices().nth(max) {
        let window = &rest[..cut];
        let half = window.char_indices().nth(max / 2).map_or(0, |(i, _)| i);
        let at = window
            .rfind('\n')
            .filter(|&i| i >= half)
            .or_else(|| window.rfind(' ').filter(|&i| i >= half))
            .map_or(cut, |i| i + 1);
        let piece = rest[..at].trim_end();
        if !piece.is_empty() {
            out.push(piece);
        }
        rest = rest[at..].trim_start_matches(['\n', '\r']);
    }
    if !rest.trim().is_empty() {
        out.push(rest);
    }
    out
}

fn is_markdown(path: &Path) -> bool {
    path.extension()
        .and_then(OsStr::to_str)
        .is_some_and(|e| e.eq_ignore_ascii_case("md") || e.eq_ignore_ascii_case("markdown"))
}

fn is_skipped_dir(name: &OsStr) -> bool {
    SKIP_DIRS.iter().any(|d| name == OsStr::new(d))
}

pub struct MarkdownAdapter {
    roots: Vec<PathBuf>,
}

impl MarkdownAdapter {
    /// The roots in [`ROOTS_ENV`], `~` expanded against `$HOME`. Unset or
    /// empty: no roots, and the adapter is a no-op.
    pub fn from_env() -> Self {
        let raw = std::env::var(ROOTS_ENV).unwrap_or_default();
        let home = std::env::var_os("HOME")
            .filter(|h| !h.is_empty())
            .map(PathBuf::from)
            .or_else(dirs::home_dir);
        Self::new(parse_roots(&raw, home.as_deref()))
    }

    pub fn new(roots: Vec<PathBuf>) -> Self {
        Self { roots }
    }

    /// The innermost root that holds `path`.
    fn root_of(&self, path: &Path) -> Option<&Path> {
        self.roots
            .iter()
            .filter(|r| path.starts_with(r))
            .max_by_key(|r| r.components().count())
            .map(PathBuf::as_path)
    }
}

impl Adapter for MarkdownAdapter {
    fn name(&self) -> &str {
        NAME
    }

    /// The first root; the others are in [`ROOTS_ENV`].
    fn home(&self) -> PathBuf {
        self.roots.first().cloned().unwrap_or_default()
    }

    fn is_present(&self) -> bool {
        self.roots.iter().any(|r| r.is_dir())
    }

    fn discover(&self) -> Result<Vec<PathBuf>> {
        let mut out = Vec::new();
        for root in self.roots.iter().filter(|r| r.is_dir()) {
            let walk = WalkDir::new(root)
                .sort_by_file_name()
                .into_iter()
                .filter_entry(|e| {
                    e.depth() == 0 || !(e.file_type().is_dir() && is_skipped_dir(e.file_name()))
                });
            // Unreadable folders are passed over. Symlinks are not followed
            // and are not files, so they drop out here.
            for entry in walk.flatten() {
                if entry.file_type().is_file() && self.excluded_reason(entry.path()).is_none() {
                    out.push(entry.into_path());
                }
            }
        }
        // Nested roots find a file twice.
        out.sort();
        out.dedup();
        Ok(out)
    }

    fn excluded_reason(&self, path: &Path) -> Option<&'static str> {
        let Some(root) = self.root_of(path) else {
            return Some("outside the markdown roots");
        };
        let inner = path.strip_prefix(root).unwrap_or(path);
        if inner
            .components()
            .any(|c| matches!(c, Component::Normal(n) if is_skipped_dir(n)))
        {
            return Some("skipped folder");
        }
        if !is_markdown(path) {
            return Some("not markdown");
        }
        match std::fs::symlink_metadata(path) {
            Err(_) => Some("unreadable"),
            Ok(m) if m.file_type().is_symlink() => Some("symlink"),
            Ok(m) if !m.is_file() => Some("not a file"),
            Ok(m) if m.len() > MAX_FILE_BYTES => Some("larger than 1 MiB"),
            Ok(_) => None,
        }
    }

    fn parse(&self, path: &Path) -> Result<Option<NormalisedConversation>> {
        if self.excluded_reason(path).is_some() {
            return Ok(None);
        }
        let bytes = std::fs::read(path)?;
        let raw = String::from_utf8_lossy(&bytes);
        let text = raw.strip_prefix('\u{feff}').unwrap_or(&raw);
        // Microseconds, as PostgreSQL stores them.
        let modified: DateTime<Utc> = std::fs::metadata(path)?.modified()?.into();
        let base = DateTime::from_timestamp_micros(modified.timestamp_micros()).unwrap_or(modified);

        let (front, body) = read_frontmatter(text);
        let sections = split_sections(body);
        let file_path = path.to_string_lossy().into_owned();
        let mut messages: Vec<NormalisedMessage> = Vec::new();
        for (index, section) in sections.iter().enumerate() {
            for (part, piece) in split_long(&section.text, MAX_MESSAGE_CHARS)
                .into_iter()
                .enumerate()
            {
                let at = base + Duration::microseconds(messages.len() as i64);
                messages.push(NormalisedMessage {
                    role: Role::Document.as_str().into(),
                    content: piece.to_string(),
                    created_at: Some(at),
                    metadata: json!({
                        "file_path": file_path,
                        "heading_path": section.heading_path,
                        "section_index": index,
                        "part": part,
                    }),
                    ..Default::default()
                });
            }
        }
        let Some(ended_at) = messages.last().and_then(|m| m.created_at) else {
            return Ok(None);
        };

        let root = self
            .root_of(path)
            .map(|r| r.to_string_lossy().into_owned())
            .unwrap_or_default();
        let project_path = match &front.project {
            Some(p) => format!("markdown/{}", p.replace(['/', '\\'], "-")),
            None => root.clone(),
        };
        let first_h1 = sections
            .iter()
            .find(|s| s.level == 1)
            .and_then(|s| s.heading_path.last())
            .filter(|t| !t.is_empty())
            .cloned();
        let file_name = path.file_name().map(|n| n.to_string_lossy().into_owned());
        Ok(Some(NormalisedConversation {
            session_id: stable_uuid(&NS, None, &file_path),
            project_path: Some(project_path),
            model: None,
            entrypoint: None,
            git_branch: None,
            started_at: base,
            ended_at: Some(ended_at),
            messages,
            token_count_in: None,
            token_count_out: None,
            summary: front.title.clone().or(first_h1).or(file_name),
            metadata: json!({
                "source": NAME,
                "file_path": file_path,
                "root": root,
                "frontmatter": front.to_json(),
            }),
            source_tool: Some(NAME.into()),
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::adapters::{sha256_file, stable_uuid};
    use crate::ignore::project_name_of;
    use crate::writer::{replace_messages, upsert_conversation};
    use chrono::{DateTime, Duration, Utc};
    use postgres::GenericClient;
    use std::fs;

    /// Connect to the database (`PG*` env). Skip when unreachable so offline
    /// `cargo test --lib` stays green. Mirrors the writer.rs test helper.
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

    /// Invented notes: frontmatter, a preamble, three heading levels and a
    /// fourth that stays inside its section.
    const ALPHA: &str = "---
title: Tide tables
tags:
  - harbor
  - \"tides\"
date: 2031-04-02
project: lighthouse
keeper: ignored key
---
Intro line before the first heading.

# Tide tables

Opening paragraph.

## Morning

Low tide at six.

### Gauge

Read the gauge twice.

#### Detail

Kept inside the gauge section.

## Evening

High tide at nine.
";

    /// Heading marks inside code, in three fence styles, plus lines that
    /// look like headings and are not. The last fence is never closed.
    const FENCED: &str = "# Real

```sh
# a shell comment
## not a heading either
```

~~~
### tilde fenced
~~~

````md
```
# inside a longer fence
```
````

#hashtag is text
    # indented four spaces is code
   ## Three spaces is a heading

```
# unclosed fence runs to the end
## still text
";

    /// One H1 section of about 4,800 characters, then a short H2.
    fn long_text() -> String {
        let mut s = String::from("# Long\n\n");
        for n in 0..100 {
            s.push_str(&format!(
                "Line {n:03} of the long section, padded with words.\n"
            ));
        }
        s.push_str("\n## After\n\nshort\n");
        s
    }

    fn write(root: &Path, rel: &str, text: &str) -> PathBuf {
        let path = root.join(rel);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, text).unwrap();
        path
    }

    /// The invented folder of S1 (b17): 10 valid files, one over 1 MiB, one
    /// symlink, `node_modules/x.md`, and for good measure the other skipped
    /// folders and a text file.
    fn fixture(tmp: &Path) -> PathBuf {
        let root = tmp.join("field-notes");
        write(&root, "alpha.md", ALPHA);
        write(
            &root,
            "beta.markdown",
            "Loose words before any heading.\n\n# Beta\n\nBody of beta.\n",
        );
        write(&root, "fenced.md", FENCED);
        write(&root, "long.md", &long_text());
        write(&root, "sub/delta.md", "## Delta only\n\nSecond level.\n");
        write(
            &root,
            "sub/deeper/epsilon.md",
            "# Epsilon\n\n### Skip a level\n\nThird level.\n",
        );
        write(&root, "zeta.md", "No heading at all, just a line.\n");
        write(
            &root,
            "eta.md",
            "---\ntitle: \"Eta: quoted\"\ntags: [kelp, 'tide']\n---\n# Eta heading\n\ntext\n",
        );
        write(
            &root,
            "theta.md",
            "# Theta\n\n#### Fourth level stays inside\n\nstill theta\n",
        );
        write(
            &root,
            "iota.md",
            "---\nunterminated: true\n\n# Iota\n\nbody\n",
        );
        // Skipped.
        let mut huge = String::from("# Huge\n\n");
        huge.push_str(&"x".repeat(MAX_FILE_BYTES as usize + 10));
        write(&root, "huge.md", &huge);
        write(&root, "node_modules/x.md", "# vendored\n");
        write(&root, ".git/x.md", "# git internals\n");
        write(&root, "target/x.md", "# build output\n");
        write(&root, "dist/x.md", "# bundle\n");
        write(&root, "notes.txt", "# not markdown\n");
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(root.join("alpha.md"), root.join("link.md")).unwrap();
            std::os::unix::fs::symlink(root.join("sub"), root.join("linked-dir")).unwrap();
        }
        root
    }

    fn mtime(path: &Path) -> DateTime<Utc> {
        let t: DateTime<Utc> = fs::metadata(path).unwrap().modified().unwrap().into();
        DateTime::from_timestamp_micros(t.timestamp_micros()).unwrap()
    }

    fn texts(conv: &NormalisedConversation) -> Vec<&str> {
        conv.messages.iter().map(|m| m.content.as_str()).collect()
    }

    fn heading_paths(conv: &NormalisedConversation) -> Vec<Vec<String>> {
        conv.messages
            .iter()
            .map(|m| serde_json::from_value(m.metadata["heading_path"].clone()).unwrap())
            .collect()
    }

    fn strings(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn markdown_root_yields_ten_conversations_with_sections() {
        let tmp = tempfile::tempdir().unwrap();
        let root = fixture(tmp.path());
        let adapter = MarkdownAdapter::new(vec![root.clone()]);
        assert_eq!(adapter.name(), NAME);
        assert!(adapter.is_present());

        let found = adapter.discover().unwrap();
        let rel: Vec<String> = found
            .iter()
            .map(|p| p.strip_prefix(&root).unwrap().display().to_string())
            .collect();
        assert_eq!(
            rel,
            [
                "alpha.md",
                "beta.markdown",
                "eta.md",
                "fenced.md",
                "iota.md",
                "long.md",
                "sub/deeper/epsilon.md",
                "sub/delta.md",
                "theta.md",
                "zeta.md",
            ],
            "10 valid files, in stable order"
        );
        assert!(adapter.excluded_reason(&root.join("huge.md")).is_some());
        assert!(adapter
            .excluded_reason(&root.join("node_modules/x.md"))
            .is_some());
        #[cfg(unix)]
        assert!(adapter.excluded_reason(&root.join("link.md")).is_some());
        assert!(adapter.excluded_reason(&root.join("alpha.md")).is_none());

        let convs: Vec<NormalisedConversation> = found
            .iter()
            .map(|p| {
                adapter
                    .parse(p)
                    .unwrap()
                    .expect("one conversation per file")
            })
            .collect();
        assert_eq!(convs.len(), 10);
        let by = |name: &str| {
            let i = rel.iter().position(|r| r == name).unwrap();
            (&found[i], &convs[i])
        };

        for (path, conv) in found.iter().zip(&convs) {
            let path_str = path.to_string_lossy();
            assert_eq!(conv.source_tool.as_deref(), Some(NAME));
            assert_eq!(conv.session_id, stable_uuid(&NS, None, &path_str));
            assert_eq!(conv.metadata["file_path"], *path_str);
            assert!(!conv.messages.is_empty());
            // Section time: file mtime plus the message's position in µs.
            let base = mtime(path);
            assert_eq!(conv.started_at, base);
            for (i, m) in conv.messages.iter().enumerate() {
                assert_eq!(m.role, "document");
                assert!(m.uuid.is_none(), "no uuid: a changed file is replaced (a5)");
                assert_eq!(m.created_at, Some(base + Duration::microseconds(i as i64)));
                assert_eq!(m.metadata["file_path"], *path_str);
                assert!(m.content.chars().count() <= MAX_MESSAGE_CHARS);
            }
            assert_eq!(conv.ended_at, conv.messages.last().unwrap().created_at);
        }
        let mut ids: Vec<Uuid> = convs.iter().map(|c| c.session_id).collect();
        ids.sort();
        ids.dedup();
        assert_eq!(ids.len(), 10);

        // Frontmatter, preamble, three levels, H4 inside its section.
        let (_, alpha) = by("alpha.md");
        assert_eq!(alpha.summary.as_deref(), Some("Tide tables"));
        assert_eq!(alpha.project_path.as_deref(), Some("markdown/lighthouse"));
        assert_eq!(
            alpha.metadata["frontmatter"],
            serde_json::json!({
                "title": "Tide tables",
                "tags": ["harbor", "tides"],
                "date": "2031-04-02",
                "project": "lighthouse"
            })
        );
        assert_eq!(
            texts(alpha),
            [
                "Intro line before the first heading.",
                "# Tide tables\n\nOpening paragraph.",
                "## Morning\n\nLow tide at six.",
                "### Gauge\n\nRead the gauge twice.\n\n#### Detail\n\nKept inside the gauge section.",
                "## Evening\n\nHigh tide at nine.",
            ]
        );
        assert_eq!(
            heading_paths(alpha),
            [
                strings(&[]),
                strings(&["Tide tables"]),
                strings(&["Tide tables", "Morning"]),
                strings(&["Tide tables", "Morning", "Gauge"]),
                strings(&["Tide tables", "Evening"]),
            ]
        );
        let index: Vec<u64> = alpha
            .messages
            .iter()
            .map(|m| m.metadata["section_index"].as_u64().unwrap())
            .collect();
        assert_eq!(index, [0, 1, 2, 3, 4]);

        // Summary falls back to the first H1, then to the file name.
        let (_, beta) = by("beta.markdown");
        assert_eq!(beta.summary.as_deref(), Some("Beta"));
        assert_eq!(
            texts(beta),
            ["Loose words before any heading.", "# Beta\n\nBody of beta."]
        );
        assert_eq!(beta.project_path.as_deref(), root.to_str());
        assert_eq!(by("sub/delta.md").1.summary.as_deref(), Some("delta.md"));
        assert_eq!(by("zeta.md").1.summary.as_deref(), Some("zeta.md"));
        assert_eq!(texts(by("zeta.md").1), ["No heading at all, just a line."]);

        // Quoted title with a colon, inline tag list.
        let (_, eta) = by("eta.md");
        assert_eq!(eta.summary.as_deref(), Some("Eta: quoted"));
        assert_eq!(
            eta.metadata["frontmatter"],
            serde_json::json!({"title": "Eta: quoted", "tags": ["kelp", "tide"]})
        );
        assert_eq!(texts(eta), ["# Eta heading\n\ntext"]);

        // An unterminated frontmatter block is text.
        let (_, iota) = by("iota.md");
        assert_eq!(iota.metadata["frontmatter"], serde_json::json!({}));
        assert_eq!(texts(iota), ["---\nunterminated: true", "# Iota\n\nbody"]);
        assert_eq!(iota.summary.as_deref(), Some("Iota"));

        assert_eq!(
            heading_paths(by("sub/deeper/epsilon.md").1),
            [strings(&["Epsilon"]), strings(&["Epsilon", "Skip a level"])]
        );
        assert_eq!(
            texts(by("theta.md").1),
            ["# Theta\n\n#### Fourth level stays inside\n\nstill theta"]
        );
        assert_eq!(by("fenced.md").1.messages.len(), 2);

        // Over 2,000 characters: continuation messages of one section.
        let (_, long) = by("long.md");
        let parts: Vec<(u64, u64)> = long
            .messages
            .iter()
            .map(|m| {
                (
                    m.metadata["section_index"].as_u64().unwrap(),
                    m.metadata["part"].as_u64().unwrap(),
                )
            })
            .collect();
        assert_eq!(parts, [(0, 0), (0, 1), (0, 2), (1, 0)]);
        assert!(long.messages[0].content.starts_with("# Long\n"));
        assert!(long.messages[1].content.starts_with("Line "));
        let squeeze = |s: &str| s.split_whitespace().collect::<String>();
        let joined: String = long.messages[..3]
            .iter()
            .map(|m| m.content.as_str())
            .collect();
        let section = long_text();
        let section = section.split("\n## After").next().unwrap();
        assert_eq!(
            squeeze(&joined),
            squeeze(section),
            "no text lost or repeated"
        );
        assert_eq!(heading_paths(long)[2], strings(&["Long"]));

        // Stable across parses.
        let again = adapter.parse(by("alpha.md").0).unwrap().unwrap();
        assert_eq!(again.session_id, alpha.session_id);
        assert_eq!(texts(&again), texts(alpha));
    }

    fn message_rows<C: GenericClient>(
        client: &mut C,
        conversation_id: i64,
    ) -> Vec<(i64, String, Option<Uuid>, DateTime<Utc>)> {
        client
            .query(
                "SELECT id, content, uuid, created_at FROM messages \
                 WHERE conversation_id = $1 ORDER BY created_at, id",
                &[&conversation_id],
            )
            .unwrap()
            .iter()
            .map(|r| (r.get(0), r.get(1), r.get(2), r.get(3)))
            .collect()
    }

    #[test]
    fn markdown_changed_file_replaces_only_its_messages() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping markdown_changed_file_replaces_only_its_messages: no PostgreSQL reachable"
            );
            return;
        };
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().join("tide-log");
        let a = write(
            &root,
            "a.md",
            "# Log\n\nOld first.\n\n## Second\n\nOld second.\n\n## Third\n\nOld third.\n",
        );
        let b = write(
            &root,
            "b.md",
            "# Other\n\nUntouched.\n\n## More\n\nStill.\n",
        );
        let adapter = MarkdownAdapter::new(vec![root.clone()]);

        /// What the runner does with a file whose hash changed.
        fn store<C: GenericClient>(tx: &mut C, adapter: &MarkdownAdapter, path: &Path) -> i64 {
            let conv = adapter.parse(path).unwrap().unwrap();
            let id = upsert_conversation(tx, &conv).unwrap();
            replace_messages(tx, id, &conv).unwrap();
            id
        }
        let mut tx = client.transaction().expect("begin test tx");
        let a_id = store(&mut tx, &adapter, &a);
        let b_id = store(&mut tx, &adapter, &b);
        let a_before = message_rows(&mut tx, a_id);
        let b_before = message_rows(&mut tx, b_id);
        assert_eq!(a_before.len(), 3);
        assert_eq!(b_before.len(), 2);
        let project: Option<String> = tx
            .query_one(
                "SELECT project_name FROM conversations WHERE id = $1",
                &[&a_id],
            )
            .unwrap()
            .get(0);
        assert_eq!(
            project.as_deref(),
            Some("tide-log"),
            "a4: the root, not the file"
        );

        // The second section changes and the third goes away.
        let (hash_a, hash_b) = (sha256_file(&a).unwrap(), sha256_file(&b).unwrap());
        fs::write(&a, "# Log\n\nOld first.\n\n## Second\n\nNew second.\n").unwrap();
        assert_ne!(sha256_file(&a).unwrap(), hash_a);
        assert_eq!(sha256_file(&b).unwrap(), hash_b, "the runner skips b");

        assert_eq!(
            store(&mut tx, &adapter, &a),
            a_id,
            "same file, same conversation"
        );
        let a_after = message_rows(&mut tx, a_id);
        let contents: Vec<&str> = a_after.iter().map(|r| r.1.as_str()).collect();
        assert_eq!(
            contents,
            ["# Log\n\nOld first.", "## Second\n\nNew second."]
        );
        for row in &a_after {
            assert!(row.2.is_none());
            assert!(
                a_before.iter().all(|old| old.0 != row.0),
                "delete-and-replace: every row of a is new"
            );
        }
        assert!(a_after[0].3 < a_after[1].3, "distinct, ordered times");
        let count: i32 = tx
            .query_one(
                "SELECT message_count FROM conversations WHERE id = $1",
                &[&a_id],
            )
            .unwrap()
            .get(0);
        assert_eq!(count, 2);
        assert_eq!(message_rows(&mut tx, b_id), b_before, "b keeps its rows");
    }

    #[test]
    fn markdown_project_is_the_root_not_the_file() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().join("harbor-notes");
        let nested = write(&root, "a/b/c.md", "# Deep\n\ntext\n");
        let tagged = write(
            &root,
            "tagged.md",
            "---\nproject: lighthouse\n---\n# Tagged\n",
        );
        let slashed = write(
            &root,
            "slashed.md",
            "---\nproject: sea/charts\n---\n# Slashed\n",
        );

        // A trailing slash would leave the project name empty.
        let raw = format!("{}/", root.display());
        let roots = parse_roots(&raw, None);
        // Compared as text: `PathBuf` equality ignores a trailing slash.
        let as_text: Vec<&str> = roots.iter().filter_map(|r| r.to_str()).collect();
        assert_eq!(as_text, [root.to_str().unwrap()]);
        let adapter = MarkdownAdapter::new(roots);

        let conv = adapter.parse(&nested).unwrap().unwrap();
        assert_eq!(conv.project_path.as_deref(), root.to_str());
        assert_eq!(
            project_name_of(conv.project_path.as_deref()),
            "harbor-notes"
        );
        assert_eq!(conv.metadata["file_path"], *nested.to_string_lossy());
        assert_eq!(conv.metadata["root"], *root.to_string_lossy());

        let conv = adapter.parse(&tagged).unwrap().unwrap();
        assert_eq!(conv.project_path.as_deref(), Some("markdown/lighthouse"));
        assert_eq!(project_name_of(conv.project_path.as_deref()), "lighthouse");

        let conv = adapter.parse(&slashed).unwrap().unwrap();
        assert_eq!(project_name_of(conv.project_path.as_deref()), "sea-charts");

        // `~` against `$HOME`; empty entries dropped; duplicates once.
        let home = Path::new("/home/fixture");
        assert_eq!(
            parse_roots(
                "~/harbor-notes: ~ ::/srv/notes/::~/harbor-notes",
                Some(home)
            ),
            [
                home.join("harbor-notes"),
                home.to_path_buf(),
                PathBuf::from("/srv/notes"),
            ]
        );

        // Nested roots: the file is found once and belongs to the inner root.
        let inner = root.join("a");
        let adapter = MarkdownAdapter::new(vec![root.clone(), inner.clone()]);
        let found = adapter.discover().unwrap();
        assert_eq!(found.iter().filter(|p| **p == nested).count(), 1);
        assert_eq!(found.len(), 3);
        let conv = adapter.parse(&nested).unwrap().unwrap();
        assert_eq!(conv.project_path.as_deref(), inner.to_str());
    }

    #[test]
    fn markdown_headings_inside_code_fences_are_text() {
        let sections = split_sections(FENCED);
        let paths: Vec<Vec<String>> = sections.iter().map(|s| s.heading_path.clone()).collect();
        assert_eq!(
            paths,
            [
                strings(&["Real"]),
                strings(&["Real", "Three spaces is a heading"])
            ]
        );
        for line in [
            "# a shell comment",
            "## not a heading either",
            "### tilde fenced",
            "# inside a longer fence",
            "#hashtag is text",
            "    # indented four spaces is code",
        ] {
            assert!(sections[0].text.contains(line), "{line:?} is text");
        }
        for line in ["# unclosed fence runs to the end", "## still text"] {
            assert!(sections[1].text.contains(line), "{line:?} is text");
        }
        assert!(sections[1]
            .text
            .starts_with("   ## Three spaces is a heading"));

        // Closing hashes go; a hash inside a word stays.
        let s = split_sections("## Closed ##\n\nx\n\n## C#\n\ny\n");
        let paths: Vec<Vec<String>> = s.iter().map(|s| s.heading_path.clone()).collect();
        assert_eq!(paths, [strings(&["Closed"]), strings(&["C#"])]);
    }

    #[test]
    fn markdown_without_roots_is_a_noop() {
        let home = Path::new("/home/fixture");
        assert!(parse_roots("", Some(home)).is_empty());
        assert!(parse_roots(" : :", Some(home)).is_empty());

        let empty = MarkdownAdapter::new(Vec::new());
        assert!(!empty.is_present());
        assert!(empty.discover().unwrap().is_empty());

        let tmp = tempfile::tempdir().unwrap();
        let missing = MarkdownAdapter::new(vec![tmp.path().join("absent")]);
        assert!(!missing.is_present());
        assert!(missing.discover().unwrap().is_empty());

        // Registered, so `ingest --source all` runs it, and it does nothing.
        assert!(crate::adapters::registry::built_ins()
            .iter()
            .any(|a| a.name() == NAME));
        assert_eq!(
            crate::adapters::registry::by_name(NAME).unwrap().name(),
            NAME
        );
        if let Some(mut client) = test_db() {
            let s = crate::ingest_run::run_adapter(&mut client, &empty, false).unwrap();
            assert_eq!(
                (s.files_seen, s.ingested, s.skipped, s.errors),
                (0, 0, 0, 0)
            );
        }
    }
}
