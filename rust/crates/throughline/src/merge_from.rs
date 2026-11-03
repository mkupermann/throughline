//! `throughline merge-from`: take conversations, messages, notes, prompts,
//! skills and vectors from a second database on the same PostgreSQL server
//! and insert them into the target database.
//!
//! Insert-only: nothing that already exists in the target is changed, and the
//! whole run is one transaction — `--apply` commits it, without `--apply` the
//! report is printed and the transaction is rolled back. A second run over
//! the same source inserts nothing.
//!
//! Rules (order = order in [`merge_in`]):
//! 1. Precheck: target is in local mode (no `access_users` rows) and
//!    every Rust migration applied in the source is applied in the target too.
//! 2. Column lists per table: `information_schema` of the target, intersected
//!    with the source; generated columns and `id` are left out.
//! 3. `projects` by `name`. 4. `conversations` by `session_id`. 5. `messages`
//!    for new conversations only. 6. `memory_chunks` (`visibility='shared'`,
//!    sha256-of-trimmed-content dedup, `superseded_by`/`merged_from` in a
//!    second pass). 7. `memory_reflections`. 8. `prompts` by `name`. 9.
//!    `assistant_configs` skills named on the CLI, paths prefixed
//!    `mac-merge:`. 10. `embeddings` for taken messages and notes.
//! 11. Everything else stays where it is (skills/entities/pm_*/access_* …).

use crate::config_import::sha256_hex;
use crate::migrate;
use anyhow::{bail, Context, Result};
use postgres::{Client, GenericClient, Transaction};
use std::collections::{HashMap, HashSet};

/// A value for a query parameter.
type SqlParam<'a> = &'a (dyn postgres::types::ToSql + Sync);

/// What to merge and how.
pub struct MergeOptions {
    /// Commit instead of rolling back after the report.
    pub apply: bool,
    /// `assistant_configs` skill names (`kind = 'skills'`) to take over.
    pub skills: Vec<String>,
    /// Active embedding model; `None` takes no vectors over.
    pub embed_model: Option<String>,
}

/// One report line: `<table>: source=<n> inserted=<n> skipped=<n> …`.
#[derive(Debug, Clone)]
pub struct TableReport {
    pub table: &'static str,
    pub source: usize,
    pub inserted: usize,
    pub skipped: usize,
    pub extras: Vec<(String, usize)>,
}

/// The whole report, one [`TableReport`] per merged table.
#[derive(Debug, Clone)]
pub struct MergeReport {
    pub tables: Vec<TableReport>,
    pub applied: bool,
}

impl MergeReport {
    /// The line for `name`, if that table was merged.
    pub fn table(&self, name: &str) -> Option<&TableReport> {
        self.tables.iter().find(|t| t.table == name)
    }
}

impl std::fmt::Display for MergeReport {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        for t in &self.tables {
            write!(
                f,
                "{}: source={} inserted={} skipped={}",
                t.table, t.source, t.inserted, t.skipped
            )?;
            for (k, v) in &t.extras {
                write!(f, " {k}={v}")?;
            }
            writeln!(f)?;
        }
        writeln!(
            f,
            "mode: {}",
            if self.applied { "applied" } else { "dry-run" }
        )
    }
}

fn empty(table: &'static str, source: usize) -> TableReport {
    TableReport {
        table,
        source,
        inserted: 0,
        skipped: source,
        extras: Vec::new(),
    }
}

/// Merge from `source` into `target`; see the module docs for the rules.
pub fn merge_from(
    target: &mut Client,
    source: &mut Client,
    opts: &MergeOptions,
) -> Result<MergeReport> {
    precheck(target, source)?;
    let mut tx = target.transaction().context("begin merge transaction")?;
    let report = merge_in(&mut tx, source, opts)?;
    if opts.apply {
        tx.commit().context("commit merge")?;
    } else {
        tx.rollback().context("roll back dry-run")?;
    }
    Ok(report)
}

// ---------------------------------------------------------------------------
// Rule 1: prechecks
// ---------------------------------------------------------------------------

fn precheck(target: &mut Client, source: &mut Client) -> Result<()> {
    if table_exists(target, "access_users")? {
        let users: i64 = target
            .query_one("SELECT COUNT(*) FROM access_users", &[])?
            .get(0);
        if users > 0 {
            bail!(
                "target has {users} row(s) in access_users (team mode); \
                 merge-from only supports a local-mode target"
            );
        }
    }

    for (side, client) in [("source", &mut *source), ("target", &mut *target)] {
        if !table_exists(client, "applied_migrations")? {
            bail!(
                "{side} database has no applied_migrations ledger; run `throughline migrate` first"
            );
        }
    }
    let source_applied: HashSet<String> = source
        .query("SELECT migration_name FROM applied_migrations", &[])?
        .into_iter()
        .map(|r| r.get(0))
        .collect();
    // Names come from the Rust migration list (`migrate::status` iterates it).
    // Every migration the source has applied must be applied in the target,
    // not only the highest: a gap in the middle is a broken target too.
    let rust = migrate::status(target)?;
    let missing = rust
        .iter()
        .find(|(name, applied)| source_applied.contains(name) && !*applied)
        .map(|(name, _)| name.clone());
    if let Some(name) = missing {
        bail!(
            "source has migration {name} applied but the target does not; \
             run `throughline migrate` on the target first"
        );
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Schema helpers (rule 2)
// ---------------------------------------------------------------------------

fn quote_ident(name: &str) -> String {
    format!("\"{}\"", name.replace('"', "\"\""))
}

fn table_exists<C: GenericClient>(client: &mut C, table: &str) -> Result<bool> {
    let row = client.query_one(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables \
         WHERE table_schema = 'public' AND table_name = $1)",
        &[&table],
    )?;
    Ok(row.get(0))
}

/// Insertable columns of `table`: names from the target's `information_schema`
/// (rule 2: no generated columns, no `id`) with the exact SQL type for casts.
fn table_columns<C: GenericClient>(client: &mut C, table: &str) -> Result<Vec<(String, String)>> {
    let rows = client.query(
        "SELECT c.column_name, format_type(a.atttypid, a.atttypmod) AS typ \
         FROM information_schema.columns c \
         JOIN pg_attribute a \
           ON a.attrelid = to_regclass(format('public.%I', c.table_name)) \
          AND a.attname = c.column_name \
         WHERE c.table_schema = 'public' AND c.table_name = $1 \
           AND c.is_generated = 'NEVER' AND c.column_name <> 'id' \
         ORDER BY c.ordinal_position",
        &[&table],
    )?;
    Ok(rows.into_iter().map(|r| (r.get(0), r.get(1))).collect())
}

struct TargetCols {
    names: Vec<String>,
    types: Vec<String>,
}

impl TargetCols {
    fn has(&self, name: &str) -> bool {
        self.names.iter().any(|n| n == name)
    }
}

/// All non-`id` columns of the source table, as text values (rule 2: the
/// values fill the intersection of source and target columns).
struct SrcTable {
    names: Vec<String>,
    rows: Vec<SrcRow>,
}

struct SrcRow {
    id: i64,
    vals: Vec<Option<String>>,
}

impl SrcTable {
    fn get<'a>(&self, row: &'a SrcRow, col: &str) -> Option<&'a str> {
        self.names
            .iter()
            .position(|n| n == col)
            .and_then(|i| row.vals[i].as_deref())
    }
}

/// Target insert columns (intersection) plus the source rows, or `None` when
/// the table is missing on either side (rule 11: not merged).
fn load_table(
    target: &mut Transaction<'_>,
    source: &mut Client,
    table: &str,
) -> Result<Option<(TargetCols, SrcTable)>> {
    if !table_exists(target, table)? || !table_exists(source, table)? {
        return Ok(None);
    }
    let tcols = table_columns(target, table)?;
    let scols: Vec<String> = {
        let rows = source.query(
            "SELECT column_name FROM information_schema.columns \
             WHERE table_schema = 'public' AND table_name = $1 \
             AND column_name <> 'id' ORDER BY ordinal_position",
            &[&table],
        )?;
        rows.into_iter().map(|r| r.get(0)).collect()
    };
    let scol_set: HashSet<&str> = scols.iter().map(|s| s.as_str()).collect();
    let mut names = Vec::new();
    let mut types = Vec::new();
    for (name, typ) in tcols {
        if scol_set.contains(name.as_str()) {
            names.push(name);
            types.push(typ);
        }
    }

    let mut sql = String::from("SELECT \"id\"");
    for col in &scols {
        let q = quote_ident(col);
        sql.push_str(&format!(", {q}::text AS {q}"));
    }
    sql.push_str(&format!(
        " FROM public.{} ORDER BY \"id\"",
        quote_ident(table)
    ));
    let rows = source.query(&sql, &[])?;
    let rows = rows
        .into_iter()
        .map(|r| {
            let id = r.get(0);
            let vals = (0..scols.len())
                .map(|i| r.get::<_, Option<String>>(i + 1))
                .collect();
            SrcRow { id, vals }
        })
        .collect();
    Ok(Some((
        TargetCols { names, types },
        SrcTable { names: scols, rows },
    )))
}

fn params(vals: &[Option<String>]) -> Vec<SqlParam<'_>> {
    vals.iter().map(|v| v as SqlParam<'_>).collect()
}

/// `INSERT INTO table (cols) VALUES (($1)::text::type …) <extra>`.
fn insert_sql(table: &str, cols: &TargetCols, extra: &str) -> String {
    let names: Vec<String> = cols.names.iter().map(|n| quote_ident(n)).collect();
    let casts: Vec<String> = cols
        .types
        .iter()
        .enumerate()
        .map(|(i, t)| format!("(${})::text::{}", i + 1, t))
        .collect();
    format!(
        "INSERT INTO public.{} ({}) VALUES ({}){}",
        quote_ident(table),
        names.join(", "),
        casts.join(", "),
        extra
    )
}

/// Source values for the insert columns, with per-column overrides.
fn build_vals(
    src: &SrcTable,
    row: &SrcRow,
    cols: &TargetCols,
    overrides: &HashMap<String, Option<String>>,
) -> Vec<Option<String>> {
    cols.names
        .iter()
        .map(|c| match overrides.get(c) {
            Some(v) => v.clone(),
            None => src.get(row, c).map(str::to_string),
        })
        .collect()
}

fn pg_array(ids: &[i64]) -> String {
    let items: Vec<String> = ids.iter().map(|i| i.to_string()).collect();
    format!("{{{}}}", items.join(","))
}

// ---------------------------------------------------------------------------
// Rules 3–10: the merge itself
// ---------------------------------------------------------------------------

#[derive(Default)]
struct Maps {
    /// source conversation id -> target conversation id (old or new).
    conv: HashMap<i64, i64>,
    /// source conversation ids inserted by this run (rule 5: messages only
    /// for these).
    new_convs: HashSet<i64>,
    /// source message id -> target message id (inserted only).
    msgs: HashMap<i64, i64>,
    /// source chunk id -> target chunk id (inserted only).
    chunks: HashMap<i64, i64>,
}

fn merge_in(
    tx: &mut Transaction<'_>,
    source: &mut Client,
    opts: &MergeOptions,
) -> Result<MergeReport> {
    let mut maps = Maps::default();
    let mut tables = Vec::new();
    for line in [
        merge_projects(tx, source)?,
        merge_conversations(tx, source, &mut maps)?,
        merge_messages(tx, source, &mut maps)?,
        merge_chunks(tx, source, &mut maps)?,
        merge_reflections(tx, source, &maps)?,
        merge_prompts(tx, source)?,
        merge_skills(tx, source, opts)?,
        merge_embeddings(tx, source, &maps, opts)?,
    ]
    .into_iter()
    .flatten()
    {
        tables.push(line);
    }
    Ok(MergeReport {
        tables,
        applied: opts.apply,
    })
}

/// Rule 3: `projects` by `name`.
fn merge_projects(tx: &mut Transaction<'_>, source: &mut Client) -> Result<Option<TableReport>> {
    let Some((cols, src)) = load_table(tx, source, "projects")? else {
        return Ok(None);
    };
    let existing: HashSet<String> = tx
        .query("SELECT name FROM projects", &[])?
        .into_iter()
        .map(|r| r.get(0))
        .collect();
    let sql = insert_sql("projects", &cols, "");
    let mut inserted = 0;
    let mut skipped = 0;
    for row in &src.rows {
        let name = src.get(row, "name").unwrap_or("");
        if existing.contains(name) {
            skipped += 1;
            continue;
        }
        let vals = build_vals(&src, row, &cols, &HashMap::new());
        let p = params(&vals);
        tx.execute(&sql, &p)?;
        inserted += 1;
    }
    Ok(Some(TableReport {
        table: "projects",
        source: src.rows.len(),
        inserted,
        skipped,
        extras: Vec::new(),
    }))
}

/// Rule 4: `conversations` by `session_id`; existing conversations keep their
/// old target id in the map and nothing else happens to them.
fn merge_conversations(
    tx: &mut Transaction<'_>,
    source: &mut Client,
    maps: &mut Maps,
) -> Result<Option<TableReport>> {
    let Some((cols, src)) = load_table(tx, source, "conversations")? else {
        return Ok(None);
    };
    let existing: HashMap<String, i64> = tx
        .query("SELECT id, session_id::text FROM conversations", &[])?
        .into_iter()
        .map(|r| (r.get(1), r.get(0)))
        .collect();
    let sql = insert_sql("conversations", &cols, " RETURNING id");
    let mut inserted = 0;
    let mut skipped = 0;
    for row in &src.rows {
        let session = src.get(row, "session_id").unwrap_or("");
        if let Some(&old) = existing.get(session) {
            maps.conv.insert(row.id, old);
            skipped += 1;
            continue;
        }
        let vals = build_vals(&src, row, &cols, &HashMap::new());
        let p = params(&vals);
        let id: i64 = tx.query_one(&sql, &p)?.get(0);
        maps.conv.insert(row.id, id);
        maps.new_convs.insert(row.id);
        inserted += 1;
    }
    Ok(Some(TableReport {
        table: "conversations",
        source: src.rows.len(),
        inserted,
        skipped,
        extras: Vec::new(),
    }))
}

/// Rule 5: `messages` of new conversations, `conversation_id` remapped.
fn merge_messages(
    tx: &mut Transaction<'_>,
    source: &mut Client,
    maps: &mut Maps,
) -> Result<Option<TableReport>> {
    let Some((cols, src)) = load_table(tx, source, "messages")? else {
        return Ok(None);
    };
    let conv_of: HashMap<i64, i64> = source
        .query("SELECT id, conversation_id FROM messages ORDER BY id", &[])?
        .into_iter()
        .map(|r| (r.get(0), r.get(1)))
        .collect();
    let sql = insert_sql("messages", &cols, " RETURNING id");
    let mut inserted = 0;
    let mut skipped = 0;
    for row in &src.rows {
        let Some(&conv) = conv_of.get(&row.id) else {
            skipped += 1;
            continue;
        };
        if !maps.new_convs.contains(&conv) {
            // Rule 4: messages of conversations the target already has stay out.
            skipped += 1;
            continue;
        }
        let mut ov = HashMap::new();
        ov.insert(
            "conversation_id".to_string(),
            Some(maps.conv[&conv].to_string()),
        );
        let vals = build_vals(&src, row, &cols, &ov);
        let p = params(&vals);
        let id: i64 = tx.query_one(&sql, &p)?.get(0);
        maps.msgs.insert(row.id, id);
        inserted += 1;
    }
    Ok(Some(TableReport {
        table: "messages",
        source: src.rows.len(),
        inserted,
        skipped,
        extras: Vec::new(),
    }))
}

/// Rule 6: shared `memory_chunks` only, content dedup via sha256 of the
/// trimmed content plus the remapped source conversation, then a second pass
/// that sets `superseded_by` / `merged_from` through the chunk id map.
fn merge_chunks(
    tx: &mut Transaction<'_>,
    source: &mut Client,
    maps: &mut Maps,
) -> Result<Option<TableReport>> {
    let Some((cols, src)) = load_table(tx, source, "memory_chunks")? else {
        return Ok(None);
    };
    struct Refs {
        source_id: Option<i64>,
        superseded_by: Option<i64>,
        merged_from: Option<Vec<i64>>,
    }
    let refs: HashMap<i64, Refs> = source
        .query(
            "SELECT id, source_id, superseded_by, merged_from \
             FROM memory_chunks ORDER BY id",
            &[],
        )?
        .into_iter()
        .map(|r| {
            let merged: Option<Vec<Option<i64>>> = r.get(3);
            (
                r.get(0),
                Refs {
                    source_id: r.get(1),
                    superseded_by: r.get(2),
                    merged_from: merged.map(|v| v.into_iter().flatten().collect()),
                },
            )
        })
        .collect();

    let sql = insert_sql("memory_chunks", &cols, " RETURNING id");
    let mut inserted = 0;
    let mut skipped = 0;
    let mut inserted_rows: Vec<(i64, i64)> = Vec::new();
    for row in &src.rows {
        // Only shared notes (the column may be absent on old sources).
        if let Some(v) = src.get(row, "visibility") {
            if v != "shared" {
                skipped += 1;
                continue;
            }
        }
        let source_type = src.get(row, "source_type").unwrap_or("manual");
        let Some(r) = refs.get(&row.id) else {
            skipped += 1;
            continue;
        };
        let source_id = if source_type == "conversation" {
            let Some(sid) = r.source_id else {
                skipped += 1;
                continue;
            };
            if !maps.new_convs.contains(&sid) {
                // Rule 4: notes of conversations the target already has stay out.
                skipped += 1;
                continue;
            }
            Some(maps.conv[&sid])
        } else {
            r.source_id
        };
        let content = src.get(row, "content").unwrap_or("");
        let hash = sha256_hex(content.trim());
        if chunk_duplicate(tx, source_type, source_id, &hash)? {
            skipped += 1;
            continue;
        }
        let mut ov = HashMap::new();
        if cols.has("source_id") {
            ov.insert("source_id".to_string(), source_id.map(|i| i.to_string()));
        }
        // Rule 6: these are set in the second pass below.
        if cols.has("superseded_by") {
            ov.insert("superseded_by".to_string(), None);
        }
        if cols.has("merged_from") {
            ov.insert("merged_from".to_string(), Some("{}".to_string()));
        }
        ov.insert("owner_id".to_string(), None);
        ov.insert("visibility".to_string(), Some("shared".to_string()));
        ov.insert("access_count".to_string(), Some("0".to_string()));
        ov.insert("last_accessed".to_string(), None);
        let vals = build_vals(&src, row, &cols, &ov);
        let p = params(&vals);
        let id: i64 = tx.query_one(&sql, &p)?.get(0);
        maps.chunks.insert(row.id, id);
        inserted_rows.push((row.id, id));
        inserted += 1;
    }

    // Second pass: remap the reference columns over the chunk id map.
    let mut unresolved_refs = 0;
    for (src_id, tgt_id) in &inserted_rows {
        let Some(r) = refs.get(src_id) else { continue };
        if cols.has("superseded_by") {
            if let Some(sb) = r.superseded_by {
                let mapped = maps.chunks.get(&sb).copied();
                if mapped.is_none() {
                    unresolved_refs += 1;
                }
                tx.execute(
                    "UPDATE memory_chunks SET superseded_by = $1 WHERE id = $2",
                    &[&mapped as SqlParam<'_>, tgt_id as SqlParam<'_>],
                )?;
            }
        }
        if cols.has("merged_from") {
            if let Some(mf) = &r.merged_from {
                if !mf.is_empty() {
                    let mut kept = Vec::new();
                    for e in mf {
                        match maps.chunks.get(e).copied() {
                            Some(t) => kept.push(t),
                            None => unresolved_refs += 1,
                        }
                    }
                    tx.execute(
                        "UPDATE memory_chunks SET merged_from = $1 WHERE id = $2",
                        &[&kept as SqlParam<'_>, tgt_id as SqlParam<'_>],
                    )?;
                }
            }
        }
    }
    Ok(Some(TableReport {
        table: "memory_chunks",
        source: src.rows.len(),
        inserted,
        skipped,
        extras: vec![("unresolved_refs".to_string(), unresolved_refs)],
    }))
}

/// Duplicate of an incoming chunk: same sha256 of the trimmed content and the
/// same remapped source conversation (same `source_type` + `source_id`).
fn chunk_duplicate(
    tx: &mut Transaction<'_>,
    source_type: &str,
    source_id: Option<i64>,
    hash: &str,
) -> Result<bool> {
    let rows = tx.query(
        "SELECT content FROM memory_chunks \
         WHERE source_type = $1 AND source_id IS NOT DISTINCT FROM $2",
        &[&source_type, &source_id],
    )?;
    for r in rows {
        let content: String = r.get(0);
        if sha256_hex(content.trim()) == hash {
            return Ok(true);
        }
    }
    Ok(false)
}

/// Rule 7: `memory_reflections`, `affected_chunks` remapped over the chunk id
/// map; a reflection is inserted only when at least one entry resolves.
fn merge_reflections(
    tx: &mut Transaction<'_>,
    source: &mut Client,
    maps: &Maps,
) -> Result<Option<TableReport>> {
    let Some((cols, src)) = load_table(tx, source, "memory_reflections")? else {
        return Ok(None);
    };
    let affected: HashMap<i64, Vec<Option<i64>>> = source
        .query(
            "SELECT id, affected_chunks FROM memory_reflections ORDER BY id",
            &[],
        )?
        .into_iter()
        .map(|r| (r.get(0), r.get(1)))
        .collect();
    let sql = insert_sql("memory_reflections", &cols, "");
    let mut inserted = 0;
    let mut skipped = 0;
    for row in &src.rows {
        let Some(entries) = affected.get(&row.id) else {
            skipped += 1;
            continue;
        };
        let mapped: Vec<i64> = entries
            .iter()
            .filter_map(|e| e.and_then(|s| maps.chunks.get(&s).copied()))
            .collect();
        if mapped.is_empty() {
            skipped += 1;
            continue;
        }
        let reflection_type = src.get(row, "reflection_type").unwrap_or("");
        let created_at = src.get(row, "created_at");
        if reflection_duplicate(tx, reflection_type, created_at, &mapped)? {
            skipped += 1;
            continue;
        }
        let mut ov = HashMap::new();
        if cols.has("affected_chunks") {
            ov.insert("affected_chunks".to_string(), Some(pg_array(&mapped)));
        }
        let vals = build_vals(&src, row, &cols, &ov);
        let p = params(&vals);
        tx.execute(&sql, &p)?;
        inserted += 1;
    }
    Ok(Some(TableReport {
        table: "memory_reflections",
        source: src.rows.len(),
        inserted,
        skipped,
        extras: Vec::new(),
    }))
}

/// Reflection duplicate: same `reflection_type`, same remapped
/// `affected_chunks`, same `created_at`.
fn reflection_duplicate(
    tx: &mut Transaction<'_>,
    reflection_type: &str,
    created_at: Option<&str>,
    mapped: &[i64],
) -> Result<bool> {
    let rows = tx.query(
        "SELECT affected_chunks FROM memory_reflections \
         WHERE reflection_type IS NOT DISTINCT FROM $1 \
           AND created_at IS NOT DISTINCT FROM ($2)::text::timestamptz",
        &[&reflection_type, &created_at],
    )?;
    let mut want: Vec<i64> = mapped.to_vec();
    want.sort_unstable();
    for r in rows {
        let mut have: Vec<i64> = r
            .get::<_, Option<Vec<Option<i64>>>>(0)
            .unwrap_or_default()
            .into_iter()
            .flatten()
            .collect();
        have.sort_unstable();
        if have == want {
            return Ok(true);
        }
    }
    Ok(false)
}

/// Rule 8: `prompts` by `name` (UNIQUE); the target wins on a difference.
fn merge_prompts(tx: &mut Transaction<'_>, source: &mut Client) -> Result<Option<TableReport>> {
    let Some((cols, src)) = load_table(tx, source, "prompts")? else {
        return Ok(None);
    };
    let existing: HashMap<String, String> = tx
        .query("SELECT name, content FROM prompts", &[])?
        .into_iter()
        .map(|r| (r.get(0), r.get(1)))
        .collect();
    let sql = insert_sql("prompts", &cols, "");
    let mut inserted = 0;
    let mut skipped = 0;
    let mut skipped_different = 0;
    for row in &src.rows {
        let name = src.get(row, "name").unwrap_or("");
        if let Some(have) = existing.get(name) {
            skipped += 1;
            if Some(have.as_str()) != src.get(row, "content") {
                skipped_different += 1;
            }
            continue;
        }
        let vals = build_vals(&src, row, &cols, &HashMap::new());
        let p = params(&vals);
        tx.execute(&sql, &p)?;
        inserted += 1;
    }
    Ok(Some(TableReport {
        table: "prompts",
        source: src.rows.len(),
        inserted,
        skipped,
        extras: vec![("skipped_different".to_string(), skipped_different)],
    }))
}

/// Rule 9: `assistant_configs` skills named via `--skills`, copied with a
/// `mac-merge:` path prefix so `(tool, path)` never collides with local files.
/// `import-config` leaves those rows alone (its vanish sweep skips
/// `mac-merge:%` paths).
fn merge_skills(
    tx: &mut Transaction<'_>,
    source: &mut Client,
    opts: &MergeOptions,
) -> Result<Option<TableReport>> {
    let Some((cols, src)) = load_table(tx, source, "assistant_configs")? else {
        return Ok(None);
    };
    let wanted: HashSet<&str> = opts.skills.iter().map(|s| s.as_str()).collect();
    let existing: HashSet<(String, String)> = tx
        .query("SELECT tool, path FROM assistant_configs", &[])?
        .into_iter()
        .map(|r| (r.get(0), r.get(1)))
        .collect();
    let sql = insert_sql("assistant_configs", &cols, "");
    let mut inserted = 0;
    let mut skipped = 0;
    for row in &src.rows {
        let kind = src.get(row, "kind").unwrap_or("");
        let name = src.get(row, "name").unwrap_or("");
        if kind != "skills" || !wanted.contains(name) {
            skipped += 1;
            continue;
        }
        let path = src.get(row, "path").unwrap_or("");
        let merged_path = format!("mac-merge:{path}");
        let tool = src.get(row, "tool").unwrap_or("");
        if existing.contains(&(tool.to_string(), merged_path.clone())) {
            skipped += 1;
            continue;
        }
        let mut ov = HashMap::new();
        ov.insert("path".to_string(), Some(merged_path));
        ov.insert("present".to_string(), Some("true".to_string()));
        let vals = build_vals(&src, row, &cols, &ov);
        let p = params(&vals);
        tx.execute(&sql, &p)?;
        inserted += 1;
    }
    Ok(Some(TableReport {
        table: "assistant_configs",
        source: src.rows.len(),
        inserted,
        skipped,
        extras: Vec::new(),
    }))
}

/// Rule 10: `embeddings` of the active model for taken messages and notes.
fn merge_embeddings(
    tx: &mut Transaction<'_>,
    source: &mut Client,
    maps: &Maps,
    opts: &MergeOptions,
) -> Result<Option<TableReport>> {
    let Some((cols, src)) = load_table(tx, source, "embeddings")? else {
        return Ok(None);
    };
    let Some(model) = opts.embed_model.as_deref() else {
        return Ok(Some(empty("embeddings", src.rows.len())));
    };
    let refs: HashMap<i64, (String, i64)> = source
        .query(
            "SELECT id, source_type, source_id FROM embeddings ORDER BY id",
            &[],
        )?
        .into_iter()
        .map(|r| (r.get(0), (r.get(1), r.get(2))))
        .collect();
    let sql = insert_sql(
        "embeddings",
        &cols,
        " ON CONFLICT (source_type, source_id, model) DO NOTHING",
    );
    let mut inserted = 0;
    let mut skipped = 0;
    for row in &src.rows {
        if src.get(row, "model") != Some(model) {
            skipped += 1;
            continue;
        }
        let Some((source_type, source_id)) = refs.get(&row.id) else {
            skipped += 1;
            continue;
        };
        let mapped = match source_type.as_str() {
            "message" => maps.msgs.get(source_id).copied(),
            "memory_chunk" => maps.chunks.get(source_id).copied(),
            _ => None,
        };
        let Some(new_id) = mapped else {
            skipped += 1;
            continue;
        };
        let mut ov = HashMap::new();
        ov.insert("source_id".to_string(), Some(new_id.to_string()));
        let vals = build_vals(&src, row, &cols, &ov);
        let p = params(&vals);
        if tx.execute(&sql, &p)? > 0 {
            inserted += 1;
        } else {
            skipped += 1;
        }
    }
    Ok(Some(TableReport {
        table: "embeddings",
        source: src.rows.len(),
        inserted,
        skipped,
        extras: Vec::new(),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::DbConfig;

    static NEXT_DB: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);

    /// Python-era `memory_reflections` shape (sql/schema.sql): a Rust-only
    /// migration does not create it, so the merge tests add it to exercise
    /// rule 7 where both sides have the table.
    const REFLECTIONS_DDL: &str = "CREATE TABLE memory_reflections (
        id BIGSERIAL PRIMARY KEY,
        reflection_type TEXT NOT NULL,
        affected_chunks BIGINT[],
        action_taken TEXT,
        reasoning TEXT,
        confidence NUMERIC(3,2),
        created_at TIMESTAMPTZ DEFAULT now()
    )";

    struct TestDbs {
        admin: Client,
        source_name: String,
        target_name: String,
        source: Option<Client>,
        target: Option<Client>,
    }

    impl TestDbs {
        fn new() -> Self {
            Self::with_reflections(true)
        }

        fn with_reflections(reflections: bool) -> Self {
            let mut admin = DbConfig::from_env().connect().expect("admin connect");
            let n = NEXT_DB.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            let source_name = format!("m29s{}_{}", std::process::id(), n);
            let target_name = format!("m29t{}_{}", std::process::id(), n);
            for name in [&source_name, &target_name] {
                admin
                    .batch_execute(&format!("DROP DATABASE IF EXISTS {name}"))
                    .expect("drop leftover test database");
                admin
                    .batch_execute(&format!("CREATE DATABASE {name}"))
                    .expect("create test database");
            }
            let mut source = connect(&source_name);
            let mut target = connect(&target_name);
            crate::migrate::migrate(&mut source).expect("migrate source");
            crate::migrate::migrate(&mut target).expect("migrate target");
            if reflections {
                for c in [&mut source, &mut target] {
                    c.batch_execute(REFLECTIONS_DDL)
                        .expect("memory_reflections");
                }
            }
            // Only the target: merged ids must never be confusable with
            // source ids.
            bump_ids(&mut target);
            Self {
                admin,
                source_name,
                target_name,
                source: Some(source),
                target: Some(target),
            }
        }

        fn source(&mut self) -> &mut Client {
            self.source.as_mut().expect("source client")
        }

        fn target(&mut self) -> &mut Client {
            self.target.as_mut().expect("target client")
        }

        fn both(&mut self) -> (&mut Client, &mut Client) {
            (
                self.source.as_mut().expect("source client"),
                self.target.as_mut().expect("target client"),
            )
        }
    }

    impl Drop for TestDbs {
        fn drop(&mut self) {
            self.source = None;
            self.target = None;
            let _ = self
                .admin
                .batch_execute(&format!("DROP DATABASE IF EXISTS {}", self.source_name));
            let _ = self
                .admin
                .batch_execute(&format!("DROP DATABASE IF EXISTS {}", self.target_name));
        }
    }

    fn connect(name: &str) -> Client {
        let mut cfg = DbConfig::from_env();
        cfg.dbname = name.to_string();
        cfg.connect().expect("connect test database")
    }

    /// Push id sequences high up so merged ids can never be confused with
    /// source ids (the remap assertions check for exactly that).
    fn bump_ids(c: &mut Client) {
        for t in [
            "projects",
            "conversations",
            "messages",
            "memory_chunks",
            "memory_reflections",
            "prompts",
            "assistant_configs",
            "embeddings",
        ] {
            if table_exists(c, t).expect("table_exists") {
                c.query_one(
                    &format!("SELECT setval(pg_get_serial_sequence('{t}', 'id'), 1000)"),
                    &[],
                )
                .expect("bump id sequence");
            }
        }
    }

    fn run(
        dbs: &mut TestDbs,
        apply: bool,
        skills: &[&str],
        embed_model: Option<&str>,
    ) -> MergeReport {
        let opts = MergeOptions {
            apply,
            skills: skills.iter().map(|s| (*s).to_string()).collect(),
            embed_model: embed_model.map(str::to_string),
        };
        let (source, target) = dbs.both();
        merge_from(target, source, &opts).expect("merge_from")
    }

    // --- seed helpers -----------------------------------------------------

    fn seed_user(c: &mut Client) -> i64 {
        c.query_one(
            "INSERT INTO access_users (username, display_name, password_hash, role) \
             VALUES ('owner1', 'Owner One', 'hash', 'editor') RETURNING id",
            &[],
        )
        .expect("seed user")
        .get(0)
    }

    fn seed_conv(c: &mut Client, session: uuid::Uuid, path: &str) -> i64 {
        let p: &[SqlParam<'_>] = &[&session, &path];
        c.query_one(
            "INSERT INTO conversations (session_id, project_path, started_at) \
             VALUES ($1, $2, now()) RETURNING id",
            p,
        )
        .expect("seed conversation")
        .get(0)
    }

    fn seed_msg(c: &mut Client, conv: i64, content: &str) -> i64 {
        let role = "user";
        let p: &[SqlParam<'_>] = &[&conv, &role, &content];
        c.query_one(
            "INSERT INTO messages (conversation_id, role, content, created_at) \
             VALUES ($1, ($2)::text::message_role, $3, now()) RETURNING id",
            p,
        )
        .expect("seed message")
        .get(0)
    }

    fn seed_chunk(c: &mut Client, source_type: &str, source_id: Option<i64>, content: &str) -> i64 {
        seed_chunk_full(
            c,
            source_type,
            source_id,
            content,
            "shared",
            None,
            None,
            None,
        )
    }

    fn seed_chunk_vis(
        c: &mut Client,
        source_type: &str,
        source_id: Option<i64>,
        content: &str,
        visibility: &str,
        owner: Option<i64>,
    ) -> i64 {
        seed_chunk_full(
            c,
            source_type,
            source_id,
            content,
            visibility,
            owner,
            None,
            None,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn seed_chunk_full(
        c: &mut Client,
        source_type: &str,
        source_id: Option<i64>,
        content: &str,
        visibility: &str,
        owner: Option<i64>,
        superseded_by: Option<i64>,
        merged_from: Option<Vec<i64>>,
    ) -> i64 {
        let p: &[SqlParam<'_>] = &[
            &source_type,
            &source_id,
            &content,
            &visibility,
            &owner,
            &superseded_by,
            &merged_from,
        ];
        c.query_one(
            "INSERT INTO memory_chunks \
             (source_type, source_id, content, visibility, owner_id, superseded_by, merged_from) \
             VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
            p,
        )
        .expect("seed chunk")
        .get(0)
    }

    fn seed_prompt(c: &mut Client, name: &str, content: &str) -> i64 {
        let p: &[SqlParam<'_>] = &[&name, &content];
        c.query_one(
            "INSERT INTO prompts (name, content) VALUES ($1, $2) RETURNING id",
            p,
        )
        .expect("seed prompt")
        .get(0)
    }

    fn seed_project(c: &mut Client, name: &str) -> i64 {
        let p: &[SqlParam<'_>] = &[&name];
        c.query_one("INSERT INTO projects (name) VALUES ($1) RETURNING id", p)
            .expect("seed project")
            .get(0)
    }

    fn seed_config(c: &mut Client, kind: &str, name: &str, path: &str) -> i64 {
        let tool = "claude";
        let scope = "global";
        let content = "config body";
        let sha = "deadbeef";
        let bytes: i64 = 11;
        let p: &[SqlParam<'_>] = &[&tool, &kind, &scope, &path, &name, &content, &sha, &bytes];
        c.query_one(
            "INSERT INTO assistant_configs \
             (tool, kind, scope, path, name, content, content_sha256, bytes) \
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id",
            p,
        )
        .expect("seed assistant config")
        .get(0)
    }

    fn seed_reflection(
        c: &mut Client,
        reflection_type: &str,
        affected: &[i64],
        created_at: chrono::DateTime<chrono::Utc>,
    ) -> i64 {
        let affected = affected.to_vec();
        let p: &[SqlParam<'_>] = &[&reflection_type, &affected, &created_at];
        c.query_one(
            "INSERT INTO memory_reflections (reflection_type, affected_chunks, created_at) \
             VALUES ($1, $2, $3) RETURNING id",
            p,
        )
        .expect("seed reflection")
        .get(0)
    }

    fn seed_embedding(c: &mut Client, source_type: &str, source_id: i64, model: &str) {
        let vtext = format!("[{}]", vec!["0.5"; 768].join(","));
        let p: &[SqlParam<'_>] = &[&source_type, &source_id, &model, &vtext];
        c.execute(
            "INSERT INTO embeddings (source_type, source_id, model, embedding_768) \
             VALUES ($1, $2, $3, ($4)::text::vector)",
            p,
        )
        .expect("seed embedding");
    }

    fn chunk_id(c: &mut Client, content: &str) -> i64 {
        let p: &[SqlParam<'_>] = &[&content];
        c.query_one("SELECT id FROM memory_chunks WHERE content = $1", p)
            .expect("chunk id")
            .get(0)
    }

    // --- criterion 3: existing data stays byte-identical -------------------

    fn content_md5(c: &mut Client, table: &str, max_id: i64) -> String {
        let sql = format!(
            "SELECT COALESCE(md5(string_agg(id::text || ':' || COALESCE(content, ''), \
             '|' ORDER BY id)), '') FROM {table} WHERE id <= {max_id}"
        );
        c.query_one(&sql, &[]).expect("content md5").get(0)
    }

    fn snapshot_existing(c: &mut Client) -> (i64, String, i64, String) {
        let msg_max: i64 = c
            .query_one("SELECT COALESCE(MAX(id), 0) FROM messages", &[])
            .unwrap()
            .get(0);
        let chunk_max: i64 = c
            .query_one("SELECT COALESCE(MAX(id), 0) FROM memory_chunks", &[])
            .unwrap()
            .get(0);
        (
            msg_max,
            content_md5(c, "messages", msg_max),
            chunk_max,
            content_md5(c, "memory_chunks", chunk_max),
        )
    }

    fn assert_existing_unchanged(c: &mut Client, snap: &(i64, String, i64, String)) {
        assert_eq!(
            content_md5(c, "messages", snap.0),
            snap.1,
            "existing messages changed"
        );
        assert_eq!(
            content_md5(c, "memory_chunks", snap.2),
            snap.3,
            "existing memory_chunks changed"
        );
    }

    fn all_counts(c: &mut Client) -> Vec<(String, i64)> {
        let tables: Vec<String> = c
            .query(
                "SELECT table_name FROM information_schema.tables \
                 WHERE table_schema = 'public' AND table_type = 'BASE TABLE' \
                 ORDER BY table_name",
                &[],
            )
            .unwrap()
            .into_iter()
            .map(|r| r.get(0))
            .collect();
        tables
            .into_iter()
            .map(|t| {
                let n: i64 = c
                    .query_one(&format!("SELECT COUNT(*) FROM public.\"{t}\""), &[])
                    .unwrap()
                    .get(0);
                (t, n)
            })
            .collect()
    }

    // --- acceptance criteria 2a–2i ----------------------------------------

    /// 2a: new conversation with 3 messages and 2 notes arrives, ids are
    /// remapped and the generated `project_name` is right.
    #[test]
    fn merge_from_takes_new_conversation_messages_and_notes() {
        let mut dbs = TestDbs::new();
        let session = uuid::Uuid::new_v4();
        let snap = snapshot_existing(dbs.target());
        let conv = seed_conv(dbs.source(), session, "/w/alpha");
        seed_msg(dbs.source(), conv, "first");
        seed_msg(dbs.source(), conv, "second");
        seed_msg(dbs.source(), conv, "third");
        seed_chunk(dbs.source(), "conversation", Some(conv), "note one");
        seed_chunk(dbs.source(), "conversation", Some(conv), "note two");
        seed_project(dbs.source(), "alpha");

        let report = run(&mut dbs, true, &[], None);
        assert_eq!(report.table("conversations").unwrap().inserted, 1);
        assert_eq!(report.table("messages").unwrap().inserted, 3);
        assert_eq!(report.table("memory_chunks").unwrap().inserted, 2);

        let target = dbs.target();
        let t_conv: i64 = target
            .query_one(
                "SELECT id FROM conversations WHERE session_id = $1",
                &[&session],
            )
            .unwrap()
            .get(0);
        assert_ne!(t_conv, conv, "conversation id must be remapped");
        let pname: String = target
            .query_one(
                "SELECT project_name FROM conversations WHERE id = $1",
                &[&t_conv],
            )
            .unwrap()
            .get(0);
        assert_eq!(pname, "alpha");
        let msg_convs: Vec<i64> = target
            .query("SELECT conversation_id FROM messages", &[])
            .unwrap()
            .into_iter()
            .map(|r| r.get(0))
            .collect();
        assert_eq!(msg_convs, vec![t_conv, t_conv, t_conv]);
        let chunk_src: Vec<Option<i64>> = target
            .query(
                "SELECT source_id FROM memory_chunks WHERE source_type = 'conversation'",
                &[],
            )
            .unwrap()
            .into_iter()
            .map(|r| r.get(0))
            .collect();
        assert_eq!(chunk_src, vec![Some(t_conv), Some(t_conv)]);
        assert_existing_unchanged(target, &snap);
    }

    /// 2b: a conversation the target already has (same `session_id`) stays
    /// untouched; its source messages and notes do not come over.
    #[test]
    fn merge_from_keeps_existing_session_conversation_untouched() {
        let mut dbs = TestDbs::new();
        let session = uuid::Uuid::new_v4();
        let t_conv = seed_conv(dbs.target(), session, "/w/keep");
        seed_msg(dbs.target(), t_conv, "keep a");
        seed_msg(dbs.target(), t_conv, "keep b");
        seed_chunk(dbs.target(), "conversation", Some(t_conv), "keep note");
        let snap = snapshot_existing(dbs.target());

        let s_conv = seed_conv(dbs.source(), session, "/w/source");
        seed_msg(dbs.source(), s_conv, "src a");
        seed_msg(dbs.source(), s_conv, "src b");
        seed_msg(dbs.source(), s_conv, "src c");
        seed_chunk(dbs.source(), "conversation", Some(s_conv), "src note 1");
        seed_chunk(dbs.source(), "conversation", Some(s_conv), "src note 2");

        let report = run(&mut dbs, true, &[], None);
        for table in ["conversations", "messages", "memory_chunks"] {
            assert_eq!(
                report.table(table).unwrap().inserted,
                0,
                "{table} must not grow"
            );
        }
        let target = dbs.target();
        let msgs: Vec<String> = target
            .query("SELECT content FROM messages ORDER BY id", &[])
            .unwrap()
            .into_iter()
            .map(|r| r.get(0))
            .collect();
        assert_eq!(msgs, vec!["keep a", "keep b"]);
        let n: i64 = target
            .query_one("SELECT COUNT(*) FROM memory_chunks", &[])
            .unwrap()
            .get(0);
        assert_eq!(n, 1);
        assert_existing_unchanged(target, &snap);
    }

    /// 2c: `superseded_by` / `merged_from` point at target ids after the run,
    /// never at source ids; unresolvable references become NULL / are dropped.
    #[test]
    fn merge_from_remaps_supersession_to_target_ids() {
        let mut dbs = TestDbs::new();
        let session = uuid::Uuid::new_v4();
        let snap = snapshot_existing(dbs.target());
        let owner = seed_user(dbs.source());
        let conv = seed_conv(dbs.source(), session, "/w/sup");
        let a = seed_chunk(dbs.source(), "conversation", Some(conv), "chunk a");
        let b = seed_chunk_full(
            dbs.source(),
            "conversation",
            Some(conv),
            "chunk b",
            "shared",
            None,
            Some(a),
            None,
        );
        let _c = seed_chunk_full(
            dbs.source(),
            "conversation",
            Some(conv),
            "chunk c",
            "shared",
            None,
            None,
            Some(vec![b]),
        );
        let e = seed_chunk_vis(
            dbs.source(),
            "conversation",
            Some(conv),
            "chunk e",
            "private",
            Some(owner),
        );
        let _d = seed_chunk_full(
            dbs.source(),
            "conversation",
            Some(conv),
            "chunk d",
            "shared",
            None,
            Some(e),
            None,
        );

        let report = run(&mut dbs, true, &[], None);
        let chunks = report.table("memory_chunks").unwrap();
        assert_eq!(chunks.inserted, 4);
        assert_eq!(chunks.extras[0], ("unresolved_refs".to_string(), 1));

        let target = dbs.target();
        let (ta, tb, tc, td) = (
            chunk_id(target, "chunk a"),
            chunk_id(target, "chunk b"),
            chunk_id(target, "chunk c"),
            chunk_id(target, "chunk d"),
        );
        for t in [ta, tb, tc, td] {
            assert!(t > 1000, "expected a target-side id, got {t}");
        }
        let sb: Option<i64> = target
            .query_one(
                "SELECT superseded_by FROM memory_chunks WHERE id = $1",
                &[&tb],
            )
            .unwrap()
            .get(0);
        assert_eq!(sb, Some(ta));
        assert_ne!(sb, Some(a), "must not point at the source id");
        let mf: Vec<i64> = target
            .query_one(
                "SELECT merged_from FROM memory_chunks WHERE id = $1",
                &[&tc],
            )
            .unwrap()
            .get(0);
        assert_eq!(mf, vec![tb]);
        let sd: Option<i64> = target
            .query_one(
                "SELECT superseded_by FROM memory_chunks WHERE id = $1",
                &[&td],
            )
            .unwrap()
            .get(0);
        assert_eq!(sd, None, "reference to a private note must become NULL");
        assert_existing_unchanged(target, &snap);
    }

    /// 2d: a private note never crosses over.
    #[test]
    fn merge_from_skips_private_notes() {
        let mut dbs = TestDbs::new();
        let snap = snapshot_existing(dbs.target());
        let owner = seed_user(dbs.source());
        let session = uuid::Uuid::new_v4();
        let conv = seed_conv(dbs.source(), session, "/w/priv");
        seed_chunk(dbs.source(), "conversation", Some(conv), "shared note");
        seed_chunk_vis(
            dbs.source(),
            "conversation",
            Some(conv),
            "private note",
            "private",
            Some(owner),
        );

        let report = run(&mut dbs, true, &[], None);
        let chunks = report.table("memory_chunks").unwrap();
        assert_eq!(chunks.source, 2);
        assert_eq!(chunks.inserted, 1);
        assert_eq!(chunks.skipped, 1);

        let target = dbs.target();
        let private: i64 = target
            .query_one(
                "SELECT COUNT(*) FROM memory_chunks WHERE content = 'private note'",
                &[],
            )
            .unwrap()
            .get(0);
        assert_eq!(private, 0);
        let shared: i64 = target
            .query_one(
                "SELECT COUNT(*) FROM memory_chunks WHERE content = 'shared note'",
                &[],
            )
            .unwrap()
            .get(0);
        assert_eq!(shared, 1);
        assert_existing_unchanged(target, &snap);
    }

    /// 2e: a second run over the same source inserts nothing anywhere.
    #[test]
    fn merge_from_second_run_inserts_nothing() {
        let mut dbs = TestDbs::new();
        let snap = snapshot_existing(dbs.target());
        let session = uuid::Uuid::new_v4();
        let conv = seed_conv(dbs.source(), session, "/w/twice");
        let m1 = seed_msg(dbs.source(), conv, "hello");
        let c1 = seed_chunk(dbs.source(), "conversation", Some(conv), "note one");
        seed_chunk_full(
            dbs.source(),
            "conversation",
            Some(conv),
            "note two",
            "shared",
            None,
            Some(c1),
            None,
        );
        seed_chunk(dbs.source(), "manual", None, "manual note");
        seed_reflection(dbs.source(), "dedup", &[c1], chrono::Utc::now());
        seed_prompt(dbs.source(), "p1", "prompt content");
        seed_project(dbs.source(), "twice");
        seed_config(dbs.source(), "skills", "a", "/skills/a.md");
        seed_embedding(dbs.source(), "message", m1, "model-a");
        seed_embedding(dbs.source(), "memory_chunk", c1, "model-a");

        let first = run(&mut dbs, true, &["a"], Some("model-a"));
        for t in &first.tables {
            assert!(t.inserted > 0, "first run must insert into {}", t.table);
        }
        let second = run(&mut dbs, true, &["a"], Some("model-a"));
        for t in &second.tables {
            assert_eq!(t.inserted, 0, "{} inserted {}", t.table, t.inserted);
        }
        assert_existing_unchanged(dbs.target(), &snap);
    }

    /// 2f: the dry-run rolls back — every table count is unchanged.
    #[test]
    fn merge_from_dry_run_changes_nothing() {
        let mut dbs = TestDbs::new();
        let before = all_counts(dbs.target());
        let snap = snapshot_existing(dbs.target());
        let session = uuid::Uuid::new_v4();
        let conv = seed_conv(dbs.source(), session, "/w/dry");
        seed_msg(dbs.source(), conv, "dry message");
        seed_chunk(dbs.source(), "conversation", Some(conv), "dry note");
        seed_prompt(dbs.source(), "dry prompt", "dry");
        seed_project(dbs.source(), "dry");

        let report = run(&mut dbs, false, &[], None);
        assert!(!report.applied);
        assert!(report.to_string().contains("mode: dry-run"));
        assert_eq!(report.table("conversations").unwrap().inserted, 1);
        assert_eq!(report.table("messages").unwrap().inserted, 1);
        assert_eq!(report.table("memory_chunks").unwrap().inserted, 1);
        let after = all_counts(dbs.target());
        assert_eq!(before, after, "dry-run must change nothing");
        assert_existing_unchanged(dbs.target(), &snap);
    }

    /// 2g: only vectors of the active model arrive, with remapped `source_id`.
    #[test]
    fn merge_from_takes_vectors_of_active_model_only() {
        let mut dbs = TestDbs::new();
        let snap = snapshot_existing(dbs.target());
        let session = uuid::Uuid::new_v4();
        let conv = seed_conv(dbs.source(), session, "/w/vec");
        let m1 = seed_msg(dbs.source(), conv, "embedded message");
        let c1 = seed_chunk(dbs.source(), "conversation", Some(conv), "embedded note");
        seed_embedding(dbs.source(), "message", m1, "model-a");
        seed_embedding(dbs.source(), "message", m1, "model-b");
        seed_embedding(dbs.source(), "memory_chunk", c1, "model-a");
        seed_embedding(dbs.source(), "memory_chunk", c1, "model-b");

        let report = run(&mut dbs, true, &[], Some("model-a"));
        let emb = report.table("embeddings").unwrap();
        assert_eq!(emb.source, 4);
        assert_eq!(emb.inserted, 2);
        assert_eq!(emb.skipped, 2);

        let target = dbs.target();
        let rows = target
            .query(
                "SELECT model, source_type, source_id FROM embeddings ORDER BY id",
                &[],
            )
            .unwrap();
        assert_eq!(rows.len(), 2);
        let t_msg: i64 = target
            .query_one(
                "SELECT id FROM messages WHERE content = 'embedded message'",
                &[],
            )
            .unwrap()
            .get(0);
        let t_chunk = chunk_id(target, "embedded note");
        for r in rows {
            let model: String = r.get(0);
            assert_eq!(model, "model-a");
            let source_type: String = r.get(1);
            let source_id: i64 = r.get(2);
            let want = if source_type == "message" {
                t_msg
            } else {
                t_chunk
            };
            assert_eq!(source_id, want, "source_id must be remapped");
            assert!(source_id > 1000);
        }
        assert_existing_unchanged(target, &snap);
    }

    /// 2h: `--skills a` takes only skill a (path `mac-merge:…`), and a prompt
    /// whose name already exists in the target is left as it is.
    #[test]
    fn merge_from_takes_named_skills_with_mac_merge_path() {
        let mut dbs = TestDbs::new();
        let pid = seed_prompt(dbs.target(), "p1", "target content");
        let snap = snapshot_existing(dbs.target());
        seed_config(dbs.source(), "skills", "a", "/skills/a.md");
        seed_config(dbs.source(), "skills", "b", "/skills/b.md");
        seed_config(dbs.source(), "rules", "a", "/rules/a.md");
        seed_prompt(dbs.source(), "p1", "source content");

        let report = run(&mut dbs, true, &["a"], None);
        let skills = report.table("assistant_configs").unwrap();
        assert_eq!(skills.inserted, 1);
        assert_eq!(skills.skipped, 2);
        let prompts = report.table("prompts").unwrap();
        assert_eq!(prompts.inserted, 0);
        assert_eq!(prompts.extras[0], ("skipped_different".to_string(), 1));

        let target = dbs.target();
        let rows = target
            .query(
                "SELECT kind, name, path, present FROM assistant_configs",
                &[],
            )
            .unwrap();
        assert_eq!(rows.len(), 1);
        let kind: String = rows[0].get(0);
        let name: String = rows[0].get(1);
        let path: String = rows[0].get(2);
        let present: bool = rows[0].get(3);
        assert_eq!(kind, "skills");
        assert_eq!(name, "a");
        assert_eq!(path, "mac-merge:/skills/a.md");
        assert!(present);
        let content: String = target
            .query_one("SELECT content FROM prompts WHERE id = $1", &[&pid])
            .unwrap()
            .get(0);
        assert_eq!(content, "target content");
        assert_existing_unchanged(target, &snap);
    }

    /// 2i: the precheck refuses a team-mode target (rows in `access_users`).
    #[test]
    fn merge_from_aborts_on_team_mode_target() {
        let mut dbs = TestDbs::new();
        let snap = snapshot_existing(dbs.target());
        seed_user(dbs.target());
        let session = uuid::Uuid::new_v4();
        seed_conv(dbs.source(), session, "/w/team");
        let opts = MergeOptions {
            apply: true,
            skills: vec![],
            embed_model: None,
        };
        let (source, target) = dbs.both();
        let err = merge_from(target, source, &opts).expect_err("must abort");
        let msg = format!("{err:#}");
        assert!(msg.contains("access_users"), "{msg}");
        assert!(msg.contains("local-mode"), "{msg}");
        let n: i64 = target
            .query_one("SELECT COUNT(*) FROM conversations", &[])
            .unwrap()
            .get(0);
        assert_eq!(n, 0, "nothing may be written on abort");
        assert_existing_unchanged(target, &snap);
    }

    // --- rules 1, 7, 9, 11 ------------------------------------------------

    /// Rule 1: abort when the source's highest Rust migration is missing in
    /// the target.
    #[test]
    fn merge_from_aborts_when_source_rust_migration_missing_in_target() {
        let mut dbs = TestDbs::new();
        let snap = snapshot_existing(dbs.target());
        dbs.target()
            .execute(
                "DELETE FROM applied_migrations \
                 WHERE migration_name = $1",
                &[&crate::migrate::LATEST],
            )
            .unwrap();
        let session = uuid::Uuid::new_v4();
        seed_conv(dbs.source(), session, "/w/mig");
        let opts = MergeOptions {
            apply: true,
            skills: vec![],
            embed_model: None,
        };
        let (source, target) = dbs.both();
        let err = merge_from(target, source, &opts).expect_err("must abort");
        assert!(
            format!("{err:#}").contains(crate::migrate::LATEST),
            "{err:#}"
        );
        assert_existing_unchanged(dbs.target(), &snap);
    }

    /// Rule 7: reflections remap `affected_chunks`, drop unresolvable ones,
    /// and dedup on (type, remapped chunks, created_at).
    #[test]
    fn merge_from_remaps_and_dedups_reflections() {
        let mut dbs = TestDbs::new();
        let snap = snapshot_existing(dbs.target());
        let session = uuid::Uuid::new_v4();
        let conv = seed_conv(dbs.source(), session, "/w/refl");
        let a = seed_chunk(dbs.source(), "conversation", Some(conv), "refl a");
        let b = seed_chunk(dbs.source(), "conversation", Some(conv), "refl b");
        let owner = seed_user(dbs.source());
        let p = seed_chunk_vis(
            dbs.source(),
            "conversation",
            Some(conv),
            "refl priv",
            "private",
            Some(owner),
        );
        let at = chrono::Utc::now();
        seed_reflection(dbs.source(), "dedup", &[a, b], at);
        seed_reflection(dbs.source(), "dedup", &[a, b], at);
        seed_reflection(dbs.source(), "dedup", &[p], at);
        seed_reflection(dbs.source(), "stale", &[a, p], at);

        let report = run(&mut dbs, true, &[], None);
        let refl = report.table("memory_reflections").unwrap();
        assert_eq!(refl.source, 4);
        assert_eq!(refl.inserted, 2);
        assert_eq!(refl.skipped, 2);

        let target = dbs.target();
        let rows = target
            .query(
                "SELECT reflection_type, affected_chunks FROM memory_reflections ORDER BY id",
                &[],
            )
            .unwrap();
        assert_eq!(rows.len(), 2);
        let ta = chunk_id(target, "refl a");
        let tb = chunk_id(target, "refl b");
        let t1: String = rows[0].get(0);
        let f1: Vec<i64> = rows[0].get(1);
        assert_eq!(t1, "dedup");
        assert_eq!(f1, vec![ta, tb]);
        let t2: String = rows[1].get(0);
        let f2: Vec<i64> = rows[1].get(1);
        assert_eq!(t2, "stale");
        assert_eq!(f2, vec![ta], "unresolvable entries are dropped");
        assert_existing_unchanged(target, &snap);
    }

    /// Rule 11: a source table the target does not have is not merged at all.
    #[test]
    fn merge_from_skips_source_tables_missing_in_target() {
        let mut dbs = TestDbs::with_reflections(false);
        let snap = snapshot_existing(dbs.target());
        dbs.source().batch_execute(REFLECTIONS_DDL).unwrap();
        let session = uuid::Uuid::new_v4();
        let conv = seed_conv(dbs.source(), session, "/w/tbl");
        let a = seed_chunk(dbs.source(), "conversation", Some(conv), "note for refl");
        seed_reflection(dbs.source(), "dedup", &[a], chrono::Utc::now());

        let report = run(&mut dbs, true, &[], None);
        assert!(
            report.table("memory_reflections").is_none(),
            "table missing in the target: no report line"
        );
        assert_eq!(report.table("memory_chunks").unwrap().inserted, 1);
        let exists: bool = dbs
            .target()
            .query_one(
                "SELECT to_regclass('public.memory_reflections') IS NOT NULL",
                &[],
            )
            .unwrap()
            .get(0);
        assert!(!exists);
        assert_existing_unchanged(dbs.target(), &snap);
    }

    /// Rule 9: `import-config` marks vanished inventory rows absent but must
    /// leave `mac-merge:` rows (merge-from skill copies) untouched.
    #[test]
    fn import_config_leaves_mac_merge_rows_alone() {
        let mut dbs = TestDbs::new();
        let snap = snapshot_existing(dbs.target());
        let merged = seed_config(dbs.target(), "skills", "x", "mac-merge:/x.md");
        let local = seed_config(
            dbs.target(),
            "skills",
            "y",
            "/definitely/not/in/the/local/inventory.md",
        );
        crate::config_import::run(dbs.target()).expect("import-config");

        let target = dbs.target();
        let merged_present: bool = target
            .query_one(
                "SELECT present FROM assistant_configs WHERE id = $1",
                &[&merged],
            )
            .unwrap()
            .get(0);
        assert!(merged_present, "mac-merge row must stay present");
        let local_present: bool = target
            .query_one(
                "SELECT present FROM assistant_configs WHERE id = $1",
                &[&local],
            )
            .unwrap()
            .get(0);
        assert!(!local_present, "rows without inventory file become absent");
        assert_existing_unchanged(dbs.target(), &snap);
    }
}
