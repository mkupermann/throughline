//! Shared ingest runner used by the CLI and the Operate UI.

use crate::adapters::{sha256_file, Adapter};
use crate::ignore::{project_name_of, IgnoreRules};
use crate::types::{IngestSummary, NormalisedConversation};
use crate::writer as w;
use anyhow::{Context, Result};

/// One adapter run against the open connection. Unchanged files are skipped
/// by `(file_path, file_hash)`; refreshes replace messages in place.
/// Ignore rules come from `~/.throughline-ignore` and `THROUGHLINE_INGEST_IGNORE`.
pub fn run_adapter(
    client: &mut postgres::Client,
    adapter: &dyn Adapter,
    verbose: bool,
) -> Result<IngestSummary> {
    let rules = IgnoreRules::load(&[]);
    run_adapter_with_rules(client, adapter, verbose, &rules)
}

/// Same as [`run_adapter`], with an explicit rule set (CLI `--ignore` merges in).
pub fn run_adapter_with_rules(
    client: &mut postgres::Client,
    adapter: &dyn Adapter,
    verbose: bool,
    rules: &IgnoreRules,
) -> Result<IngestSummary> {
    let mut summary = IngestSummary::new(adapter.name());
    if !adapter.is_present() {
        return Ok(summary);
    }

    let files = adapter.discover().context("discover source files")?;
    summary.files_seen = files.len();
    if verbose {
        println!("  [{}] found {} file(s)", adapter.name(), files.len());
    }

    for path in &files {
        // Privacy: path exclusions happen before the file is read at all —
        // no hash, no parse, no DB row. Only the counter and rule kind survive.
        if rules.matches_path(path) {
            summary.ignored += 1;
            summary.ignored_path += 1;
            if verbose {
                println!("    ignored (rule=path)");
            }
            continue;
        }

        let content_hash = match sha256_file(path) {
            Ok(h) => h,
            Err(e) => {
                summary.errors += 1;
                if verbose {
                    println!("    ✗ {}: {e}", path.display());
                }
                continue;
            }
        };

        match w::already_ingested(client, path, &content_hash) {
            Ok(Some(count)) if count != 0 => {
                summary.skipped += 1;
                continue;
            }
            Ok(_) => {}
            Err(e) => {
                summary.errors += 1;
                if verbose {
                    println!("    ✗ {}: {e}", path.display());
                }
                continue;
            }
        }

        let is_refresh = w::was_seen_before(client, path).unwrap_or(false);

        let parsed = match adapter.parse_many(path) {
            Ok(p) => p,
            Err(e) => {
                let _ = client.batch_execute("ROLLBACK");
                summary.errors += 1;
                let _ = w::record_decision(client, path, &content_hash);
                if verbose {
                    println!("    parse error {}: {e:#}", path.display());
                }
                continue;
            }
        };

        // Privacy: project exclusions act after parse, before any write.
        // Excluded conversations are dropped — nothing about them is persisted.
        let mut convs: Vec<NormalisedConversation> = Vec::new();
        let mut project_ignored = 0usize;
        for c in parsed.into_iter().filter(|c| !c.messages.is_empty()) {
            let name = project_name_of(c.project_path.as_deref());
            if rules.matches_project(&name) {
                summary.ignored += 1;
                summary.ignored_project += 1;
                project_ignored += 1;
                if verbose {
                    println!("    ignored (rule=project)");
                }
                continue;
            }
            convs.push(c);
        }

        if convs.is_empty() {
            if project_ignored > 0 {
                // Everything from this file was project-excluded: no
                // conversation, no decision, no ingest row for it.
                continue;
            }
            summary.skipped += 1;
            let _ = w::record_decision(client, path, &content_hash);
            continue;
        }

        let mut total_written = 0usize;
        let mut any_ok = false;
        for conv in convs {
            match write_conversation(client, path, &content_hash, conv, is_refresh) {
                Ok((written, _refreshed)) => {
                    total_written += written;
                    any_ok = true;
                    summary.ingested += 1;
                }
                Err(e) => {
                    let _ = client.batch_execute("ROLLBACK");
                    summary.errors += 1;
                    if verbose {
                        println!("    write error {}: {e:#}", path.display());
                    }
                }
            }
        }
        if any_ok {
            summary.messages_written += total_written;
        }
    }

    if summary.ingested > 0 {
        let _ = w::backfill_projects(client);
    }
    record_ignore_audit(client, adapter.name(), &summary);
    Ok(summary)
}

/// Counters and rule kinds only — never a path or content of an excluded file.
fn record_ignore_audit(client: &mut postgres::Client, adapter: &str, summary: &IngestSummary) {
    for (kind, n) in [
        (crate::ignore::RuleKind::Path, summary.ignored_path),
        (crate::ignore::RuleKind::Project, summary.ignored_project),
    ] {
        if n == 0 {
            continue;
        }
        let marker = format!("__throughline_ignored__:{}:{}", adapter, kind.as_str());
        let _ = client.execute(
            "INSERT INTO ingestion_log (file_path, file_hash, record_count) \
             VALUES ($1, 'v1', $2) \
             ON CONFLICT (file_path, file_hash) DO UPDATE \
             SET record_count = EXCLUDED.record_count, ingested_at = now()",
            &[&marker, &(n as i32)],
        );
    }
}

/// Run every built-in adapter. Returns one summary per adapter.
pub fn run_all(client: &mut postgres::Client, verbose: bool) -> Result<Vec<IngestSummary>> {
    let rules = IgnoreRules::load(&[]);
    run_all_with_rules(client, verbose, &rules)
}

/// Same as [`run_all`], with an explicit rule set (CLI `--ignore` merges in).
pub fn run_all_with_rules(
    client: &mut postgres::Client,
    verbose: bool,
    rules: &IgnoreRules,
) -> Result<Vec<IngestSummary>> {
    let mut out = Vec::new();
    for adapter in crate::adapters::registry::built_ins() {
        out.push(run_adapter_with_rules(
            client,
            adapter.as_ref(),
            verbose,
            rules,
        )?);
    }
    // MiMo/agent memory lives outside tool session dirs — refresh it with ingest.
    match crate::agent_memory::ingest_agent_memory(client) {
        Ok((files, chunks)) => {
            let mut s = IngestSummary::new("agent_memory");
            s.files_seen = files;
            s.ingested = chunks;
            out.push(s);
        }
        Err(e) => {
            eprintln!("[agent_memory] ingest failed: {e:#}");
        }
    }
    // Import AI-assistant config inventory alongside ingest.
    match crate::config_import::run(client) {
        Ok(summary) => {
            let mut s = IngestSummary::new("config_import");
            s.files_seen = summary.seen;
            s.ingested = summary.inserted + summary.updated;
            s.skipped = summary.unchanged;
            s.errors = summary.errors;
            out.push(s);
        }
        Err(e) => {
            eprintln!("[config_import] ingest failed: {e:#}");
        }
    }
    Ok(out)
}

fn write_conversation(
    client: &mut postgres::Client,
    path: &std::path::Path,
    content_hash: &str,
    conv: NormalisedConversation,
    is_refresh: bool,
) -> Result<(usize, bool)> {
    let mut tx = client.transaction()?;
    let conv_id = w::upsert_conversation(&mut tx, &conv)?;
    let written = w::replace_messages(&mut tx, conv_id, &conv)?;
    w::record_ingest(&mut tx, path, content_hash, written)?;
    tx.commit()?;
    Ok((written, is_refresh))
}

/// Per-provider ingest overview for the Operate surface.
#[derive(Debug, serde::Serialize)]
pub struct ProviderIngestStatus {
    pub source_tool: String,
    pub conversations: i64,
    pub messages: i64,
    pub last_session: Option<chrono::DateTime<chrono::Utc>>,
    pub last_ingest: Option<chrono::DateTime<chrono::Utc>>,
    pub adapter_present: bool,
    pub adapter_home: String,
}

#[derive(Debug, serde::Serialize)]
pub struct IngestStatusReport {
    pub providers: Vec<ProviderIngestStatus>,
    pub total_conversations: i64,
    pub total_messages: i64,
    pub total_files_logged: i64,
}

pub fn ingest_status(client: &mut postgres::Client) -> Result<IngestStatusReport> {
    let rows = client.query(
        r#"
        SELECT COALESCE(NULLIF(source_tool, ''), 'unknown') AS source_tool,
               COUNT(*) AS conversations,
               COALESCE(SUM(message_count), 0)::bigint AS messages,
               MAX(started_at) AS last_session
        FROM conversations
        WHERE generated_by IS NULL
        GROUP BY 1
        ORDER BY 1
        "#,
        &[],
    )?;

    let mut providers: Vec<ProviderIngestStatus> = Vec::new();
    for r in rows {
        let source_tool: String = r.get(0);
        providers.push(ProviderIngestStatus {
            source_tool,
            conversations: r.get(1),
            messages: r.get(2),
            last_session: r.get(3),
            last_ingest: None,
            adapter_present: false,
            adapter_home: String::new(),
        });
    }

    // Adapter presence / home (so the UI can show "no data directory").
    for adapter in crate::adapters::registry::built_ins() {
        let name = adapter.name().to_string();
        let present = adapter.is_present();
        let home = adapter.home().display().to_string();
        if let Some(p) = providers.iter_mut().find(|p| p.source_tool == name) {
            p.adapter_present = present;
            p.adapter_home = home;
        } else {
            providers.push(ProviderIngestStatus {
                source_tool: name,
                conversations: 0,
                messages: 0,
                last_session: None,
                last_ingest: None,
                adapter_present: present,
                adapter_home: home,
            });
        }
    }

    // Last successful ingest per file path prefix is expensive; use max(ingested_at).
    if let Ok(row) = client.query_one("SELECT MAX(ingested_at) FROM ingestion_log", &[]) {
        let last: Option<chrono::DateTime<chrono::Utc>> = row.get(0);
        for p in providers.iter_mut() {
            p.last_ingest = last;
        }
    }
    if let Ok(row) = client.query_one("SELECT COUNT(*) FROM ingestion_log", &[]) {
        let files: i64 = row.get(0);
        let total_conversations = providers.iter().map(|p| p.conversations).sum();
        let total_messages = providers.iter().map(|p| p.messages).sum();
        return Ok(IngestStatusReport {
            providers,
            total_conversations,
            total_messages,
            total_files_logged: files,
        });
    }

    let total_conversations = providers.iter().map(|p| p.conversations).sum();
    let total_messages = providers.iter().map(|p| p.messages).sum();
    Ok(IngestStatusReport {
        providers,
        total_conversations,
        total_messages,
        total_files_logged: 0,
    })
}
