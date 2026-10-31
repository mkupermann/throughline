//! Import AI-assistant configuration inventory into `assistant_configs`.
//!
//! Reads the masked inventory from [`crate::assistants`], writes content +
//! SHA-256 into the database, and tracks new / changed / unchanged / vanished
//! entries across runs.

use anyhow::Result;
use sha2::{Digest, Sha256};

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

#[derive(Debug, Default, Clone, Copy)]
pub struct ImportSummary {
    pub seen: usize,
    pub inserted: usize,
    pub updated: usize,
    pub unchanged: usize,
    pub vanished: usize,
    pub errors: usize,
}

impl std::fmt::Display for ImportSummary {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "import-config: seen={} new={} changed={} unchanged={} vanished={} errors={}",
            self.seen, self.inserted, self.updated, self.unchanged, self.vanished, self.errors
        )
    }
}

// ---------------------------------------------------------------------------
// Change classification
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Change {
    Inserted,
    Updated,
    Unchanged,
}

/// Classify a row by comparing the existing SHA with the new one.
pub fn classify(existing_sha: Option<&str>, new_sha: &str) -> Change {
    match existing_sha {
        None => Change::Inserted,
        Some(old) if old != new_sha => Change::Updated,
        Some(_) => Change::Unchanged,
    }
}

// ---------------------------------------------------------------------------
// SHA-256 helper
// ---------------------------------------------------------------------------

/// Lowercase hex SHA-256 of `text` (64 characters).
pub fn sha256_hex(text: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(text.as_bytes());
    let hash = hasher.finalize();
    hash.iter().map(|b| format!("{b:02x}")).collect()
}

// ---------------------------------------------------------------------------
// Row types (for M20a2 HTTP routes)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, serde::Serialize)]
pub struct ImportedEntry {
    pub id: i64,
    pub tool: String,
    pub kind: String,
    pub scope: String,
    pub project: Option<String>,
    pub path: String,
    pub name: String,
    pub summary: Option<String>,
    pub bytes: i64,
    pub modified_at: Option<String>,
    pub imported_at: String,
    pub present: bool,
    pub content_sha256: String,
    /// Inventory id of the entry (path hash) — the favorites `ref`.
    pub fav_ref: String,
    /// Whether this entry is favorited.
    pub favorite: bool,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct ImportedFile {
    pub id: i64,
    pub tool: String,
    pub kind: String,
    pub scope: String,
    pub project: Option<String>,
    pub path: String,
    pub name: String,
    pub summary: Option<String>,
    pub content: String,
    pub content_sha256: String,
    pub bytes: i64,
    pub modified_at: Option<String>,
    pub imported_at: String,
    pub present: bool,
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn parse_ts(s: Option<&str>) -> Option<chrono::DateTime<chrono::Utc>> {
    s.and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())
        .map(|dt| dt.with_timezone(&chrono::Utc))
}

// ---------------------------------------------------------------------------
// Import
// ---------------------------------------------------------------------------

/// Import the current assistant-config inventory into `assistant_configs`.
pub fn run(client: &mut postgres::Client) -> Result<ImportSummary> {
    let entries = crate::assistants::inventory(Some(&mut *client));
    let mut summary = ImportSummary {
        seen: entries.len(),
        ..Default::default()
    };

    struct Item<'a> {
        entry: &'a crate::assistants::Entry,
        content: String,
        sha: String,
    }
    let mut items: Vec<Item> = Vec::new();
    for entry in &entries {
        match crate::assistants::read_entry(&entries, &entry.id) {
            Some(view) => {
                let sha = sha256_hex(&view.content);
                items.push(Item {
                    entry,
                    content: view.content,
                    sha,
                });
            }
            None => summary.errors += 1,
        }
    }

    let mut tx = client.transaction()?;
    let mut tools_seen: Vec<String> = Vec::with_capacity(items.len());
    let mut paths_seen: Vec<String> = Vec::with_capacity(items.len());

    for item in &items {
        let path_str = item.entry.path.to_string_lossy().to_string();
        tools_seen.push(item.entry.tool.clone());
        paths_seen.push(path_str.clone());

        let existing: Option<String> = tx
            .query_opt(
                "SELECT content_sha256 FROM assistant_configs WHERE tool = $1 AND path = $2",
                &[&item.entry.tool, &path_str],
            )?
            .map(|r| r.get(0));

        let kind_str = item.entry.kind.as_str();
        let modified_at = parse_ts(item.entry.modified.as_deref());

        match classify(existing.as_deref(), &item.sha) {
            Change::Inserted => {
                tx.execute(
                    "INSERT INTO assistant_configs \
                     (tool, kind, scope, project, path, name, summary, content, \
                      content_sha256, bytes, modified_at) \
                     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                    &[
                        &item.entry.tool,
                        &kind_str,
                        &item.entry.scope,
                        &item.entry.project,
                        &path_str,
                        &item.entry.name,
                        &item.entry.summary,
                        &item.content,
                        &item.sha,
                        &(item.entry.bytes as i64),
                        &modified_at,
                    ],
                )?;
                summary.inserted += 1;
            }
            Change::Updated => {
                tx.execute(
                    "UPDATE assistant_configs SET \
                     content=$3, summary=$4, bytes=$5, modified_at=$6, \
                     imported_at=now(), present=true \
                     WHERE tool=$1 AND path=$2",
                    &[
                        &item.entry.tool,
                        &path_str,
                        &item.content,
                        &item.entry.summary,
                        &(item.entry.bytes as i64),
                        &modified_at,
                    ],
                )?;
                summary.updated += 1;
            }
            Change::Unchanged => {
                tx.execute(
                    "UPDATE assistant_configs SET present=true WHERE tool=$1 AND path=$2",
                    &[&item.entry.tool, &path_str],
                )?;
                summary.unchanged += 1;
            }
        }
    }

    // Mark vanished rows.
    let vanished = if items.is_empty() {
        tx.execute(
            "UPDATE assistant_configs SET present=false WHERE present",
            &[],
        )?
    } else {
        tx.execute(
            "UPDATE assistant_configs SET present=false \
             WHERE present AND (tool, path) NOT IN \
             (SELECT * FROM unnest($1::text[], $2::text[]) AS t(tool, path))",
            &[&tools_seen, &paths_seen],
        )?
    };
    summary.vanished = vanished as usize;

    tx.commit()?;
    Ok(summary)
}

// ---------------------------------------------------------------------------
// Queries (used by M20a2 HTTP routes)
// ---------------------------------------------------------------------------

/// List imported entries, newest first. Optional `tool` / `kind` filters.
/// Each row carries `fav_ref` (inventory id) and `favorite`; `favorites_only`
/// keeps only favorited rows. Stars are those of `actor`. Config refs are path hashes, not SQL ids, so
/// the config favorites are resolved here in Rust.
pub fn list(
    client: &mut postgres::Client,
    actor: &crate::access::Actor,
    tool: Option<&str>,
    kind: Option<&str>,
    favorites_only: bool,
) -> Result<Vec<ImportedEntry>> {
    let rows = client.query(
        "SELECT id, tool, kind, scope, project, path, name, summary, \
                content_sha256, bytes, modified_at, imported_at, present \
         FROM assistant_configs \
         WHERE ($1::text IS NULL OR tool = $1) \
           AND ($2::text IS NULL OR kind = $2) \
         ORDER BY imported_at DESC",
        &[&tool, &kind],
    )?;
    // The actor's own stars.
    let favs = crate::favorites::list(client, actor, Some(crate::favorites::FavKind::Config))?;
    let fav_refs: std::collections::HashSet<String> = favs.into_iter().map(|f| f.ref_).collect();
    let mut entries: Vec<ImportedEntry> = rows
        .into_iter()
        .map(|r| {
            let path: String = r.get(5);
            let fav_ref = crate::assistants::make_id(std::path::Path::new(&path));
            let favorite = fav_refs.contains(&fav_ref);
            ImportedEntry {
                id: r.get(0),
                tool: r.get(1),
                kind: r.get(2),
                scope: r.get(3),
                project: r.get(4),
                path,
                name: r.get(6),
                summary: r.get(7),
                content_sha256: r.get(8),
                bytes: r.get(9),
                modified_at: r
                    .get::<_, Option<chrono::DateTime<chrono::Utc>>>(10)
                    .map(|d| d.to_rfc3339()),
                imported_at: r.get::<_, chrono::DateTime<chrono::Utc>>(11).to_rfc3339(),
                present: r.get(12),
                fav_ref,
                favorite,
            }
        })
        .collect();
    if favorites_only {
        entries.retain(|e| e.favorite);
    }
    Ok(entries)
}

/// Fetch a single entry with full content.
pub fn get(client: &mut postgres::Client, id: i64) -> Result<Option<ImportedFile>> {
    let row = client.query_opt(
        "SELECT id, tool, kind, scope, project, path, name, summary, \
                content, content_sha256, bytes, modified_at, imported_at, present \
         FROM assistant_configs WHERE id = $1",
        &[&id],
    )?;
    Ok(row.map(|r| ImportedFile {
        id: r.get(0),
        tool: r.get(1),
        kind: r.get(2),
        scope: r.get(3),
        project: r.get(4),
        path: r.get(5),
        name: r.get(6),
        summary: r.get(7),
        content: r.get(8),
        content_sha256: r.get(9),
        bytes: r.get(10),
        modified_at: r
            .get::<_, Option<chrono::DateTime<chrono::Utc>>>(11)
            .map(|d| d.to_rfc3339()),
        imported_at: r.get::<_, chrono::DateTime<chrono::Utc>>(12).to_rfc3339(),
        present: r.get(13),
    }))
}

/// Timestamp of the most recent import, if any.
pub fn last_import(client: &mut postgres::Client) -> Result<Option<String>> {
    let row = client.query_one("SELECT MAX(imported_at) FROM assistant_configs", &[])?;
    Ok(row
        .get::<_, Option<chrono::DateTime<chrono::Utc>>>(0)
        .map(|d| d.to_rfc3339()))
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classify_three_cases() {
        assert_eq!(classify(None, "abc"), Change::Inserted);
        assert_eq!(classify(Some("abc"), "abc"), Change::Unchanged);
        assert_eq!(classify(Some("abc"), "xyz"), Change::Updated);
    }

    #[test]
    fn sha256_hex_stable_and_64_chars() {
        let h1 = sha256_hex("hello");
        let h2 = sha256_hex("hello");
        assert_eq!(h1, h2, "must be deterministic");
        assert_eq!(h1.len(), 64, "must be 64 hex chars");
        assert!(h1.chars().all(|c| c.is_ascii_hexdigit()), "must be hex");
    }

    #[test]
    fn display_line_format() {
        let s = ImportSummary {
            seen: 10,
            inserted: 3,
            updated: 2,
            unchanged: 4,
            vanished: 1,
            errors: 0,
        };
        assert_eq!(
            s.to_string(),
            "import-config: seen=10 new=3 changed=2 unchanged=4 vanished=1 errors=0"
        );
    }

    #[test]
    fn mask_secrets_in_config_file() {
        // Avoid THROUGHLINE_ASSISTANT_HOME (shared env-var race with
        // assistants::tests); call read_entry directly with a synthetic Entry.
        let tmp = tempfile::TempDir::new().unwrap();
        let config_path = tmp.path().join("config.toml");
        std::fs::write(
            &config_path,
            "api_key = \"sk-abcdefghijklmnopqrstuvwxyz\"\n",
        )
        .unwrap();

        let entry = crate::assistants::Entry {
            id: "testcfg000000001".to_string(),
            tool: "codex".to_string(),
            kind: crate::assistants::Kind::Config,
            name: "config.toml".to_string(),
            scope: "global".to_string(),
            project: None,
            path: config_path.clone(),
            bytes: 50,
            modified: None,
            summary: None,
            target: None,
        };
        let entries = vec![entry];
        let view = crate::assistants::read_entry(&entries, "testcfg000000001").expect("file view");

        assert!(
            view.content.contains('\u{2022}'),
            "masked: {}",
            view.content
        );
        assert!(
            !view.content.contains("sk-abcdef"),
            "secret must not appear: {}",
            view.content
        );
    }
}
