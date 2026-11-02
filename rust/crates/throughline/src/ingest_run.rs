//! Shared ingest runner used by the CLI and the Operate UI.

use crate::adapters::{sha256_file, Adapter};
use crate::ignore::{project_name_of, IgnoreRules};
use crate::types::{IngestSummary, NormalisedConversation};
use crate::writer as w;
use anyhow::{Context, Result};

/// One adapter run against the open connection. A file whose hash equals the
/// hash stored last for its path is skipped ([`w::current_hash`]); refreshes
/// replace messages in place.
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
/// Refuses a database that lacks the newest migration of the code list
/// before it reads a file ([`crate::migrate::require_latest`]).
pub fn run_adapter_with_rules(
    client: &mut postgres::Client,
    adapter: &dyn Adapter,
    verbose: bool,
    rules: &IgnoreRules,
) -> Result<IngestSummary> {
    crate::migrate::require_latest(client)?;
    run_one(client, adapter, verbose, rules)
}

fn run_one(
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

        // Unchanged means the hash of the version stored last, not any
        // hash the ledger has seen for this path: v1 restored after v2
        // replaces v2.
        match w::current_hash(client, path) {
            Ok(Some(current)) if current == content_hash => {
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
pub(crate) fn record_ignore_audit<C: postgres::GenericClient>(
    client: &mut C,
    adapter: &str,
    summary: &IngestSummary,
) {
    let _ = try_record_ignore_audit(client, adapter, summary);
}

/// [`record_ignore_audit`] that reports a failed insert, so a caller inside
/// a transaction can roll back.
pub(crate) fn try_record_ignore_audit<C: postgres::GenericClient>(
    client: &mut C,
    adapter: &str,
    summary: &IngestSummary,
) -> Result<()> {
    for (kind, n) in [
        (crate::ignore::RuleKind::Path, summary.ignored_path),
        (crate::ignore::RuleKind::Project, summary.ignored_project),
    ] {
        if n == 0 {
            continue;
        }
        let marker = format!("__throughline_ignored__:{}:{}", adapter, kind.as_str());
        client.execute(
            "INSERT INTO ingestion_log (file_path, file_hash, record_count) \
             VALUES ($1, 'v1', $2) \
             ON CONFLICT (file_path, file_hash) DO UPDATE \
             SET record_count = EXCLUDED.record_count, ingested_at = now()",
            &[&marker, &(n as i32)],
        )?;
    }
    Ok(())
}

/// Run every built-in adapter. Returns one summary per adapter.
pub fn run_all(client: &mut postgres::Client, verbose: bool) -> Result<Vec<IngestSummary>> {
    let rules = IgnoreRules::load(&[]);
    run_all_with_rules(client, verbose, &rules)
}

/// Same as [`run_all`], with an explicit rule set (CLI `--ignore` merges in).
/// Refuses a database that lacks the newest migration, as
/// [`run_adapter_with_rules`] does.
pub fn run_all_with_rules(
    client: &mut postgres::Client,
    verbose: bool,
    rules: &IgnoreRules,
) -> Result<Vec<IngestSummary>> {
    crate::migrate::require_latest(client)?;
    let mut out = Vec::new();
    for adapter in crate::adapters::registry::built_ins() {
        out.push(run_one(client, adapter.as_ref(), verbose, rules)?);
    }
    // Git repositories: their own runner (a folder has no file hash). No
    // roots or no git binary: an empty summary, and the rest goes on.
    let git = crate::sources::git::GitSource::from_env();
    match crate::sources::git::run(client, &git, verbose, rules) {
        Ok(summary) => out.push(summary),
        Err(e) => eprintln!("[{}] ingest failed: {e:#}", crate::sources::git::NAME),
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
    // Secrets in the new messages and the config files (M24). Counts only.
    match crate::secrets::scan(client, false) {
        Ok(summary) => {
            let mut s = IngestSummary::new("secrets");
            s.files_seen = summary.messages_scanned + summary.configs_scanned;
            s.ingested = summary.new_secrets;
            out.push(s);
        }
        Err(e) => {
            eprintln!("[secrets] scan failed: {e:#}");
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::adapters::markdown::MarkdownAdapter;

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

    /// The runner commits per file, so these tests write to the database.
    /// This removes what they wrote under `root`, also when an assertion
    /// fails.
    struct Cleanup {
        root: std::path::PathBuf,
    }

    impl Drop for Cleanup {
        fn drop(&mut self) {
            let Some(mut client) = test_db() else { return };
            let root = self.root.to_string_lossy().into_owned();
            let name = root.rsplit('/').next().unwrap_or_default().to_string();
            let _ = client.execute(
                "DELETE FROM conversations WHERE source_tool = 'markdown' AND project_path = $1",
                &[&root],
            );
            let _ = client.execute(
                "DELETE FROM ingestion_log WHERE starts_with(file_path, $1)",
                &[&format!("{root}/")],
            );
            let _ = client.execute("DELETE FROM projects WHERE name = $1", &[&name]);
        }
    }

    /// A Markdown root with a unique folder name below a fresh temp folder.
    fn root_in(tmp: &std::path::Path) -> std::path::PathBuf {
        let root = tmp.join(format!("ledger-notes-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir(&root).expect("root");
        root
    }

    fn sections<C: postgres::GenericClient>(client: &mut C, root: &std::path::Path) -> Vec<String> {
        client
            .query(
                "SELECT m.content FROM messages m JOIN conversations c ON c.id = m.conversation_id \
                 WHERE c.source_tool = 'markdown' AND c.project_path = $1 ORDER BY m.created_at",
                &[&root.to_string_lossy().into_owned()],
            )
            .expect("sections")
            .iter()
            .map(|r| r.get(0))
            .collect()
    }

    /// A database whose ledger lacks the newest migration (a native install
    /// migrated by the Python CLI only) is refused before a file is read,
    /// with one line that names the migration and the command.
    #[test]
    fn ingest_refuses_unmigrated_database() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping ingest_refuses_unmigrated_database: no PostgreSQL reachable");
            return;
        };
        let tmp = tempfile::tempdir().unwrap();
        let root = root_in(tmp.path());
        let _cleanup = Cleanup { root: root.clone() };
        std::fs::write(root.join("tide.md"), "# Tide\n\nThe gate opens at six.\n").unwrap();
        let adapter = MarkdownAdapter::new(vec![root.clone()]);
        let rules = IgnoreRules::default();

        // A temporary ledger shadows applied_migrations for this session.
        client
            .batch_execute(
                "CREATE TEMP TABLE applied_migrations (migration_name text PRIMARY KEY); \
                 INSERT INTO applied_migrations VALUES ('015_account_stars_untimed.sql')",
            )
            .expect("ledger without the latest migration");
        let latest = crate::migrate::LATEST;
        let refused = [
            run_adapter_with_rules(&mut client, &adapter, false, &rules)
                .map(|_| ())
                .expect_err("one adapter"),
            run_all_with_rules(&mut client, false, &rules)
                .map(|_| ())
                .expect_err("all sources"),
            crate::sources::git::run(
                &mut client,
                &crate::sources::git::GitSource::new(vec![root.clone()]),
                false,
                &rules,
            )
            .map(|_| ())
            .expect_err("git"),
            crate::sources::web::run(
                &mut client,
                &crate::sources::web::WebSource::new(None),
                false,
                false,
                &rules,
            )
            .map(|_| ())
            .expect_err("web"),
        ];
        for err in refused {
            let line = format!("{err:#}");
            assert!(line.contains(latest), "{line}");
            assert!(line.contains("throughline migrate"), "{line}");
            assert!(!line.contains('\n'), "one line: {line}");
        }
        assert!(sections(&mut client, &root).is_empty(), "nothing written");

        // Control: with the latest migration in the ledger the same run reads
        // the file, so the refusal above came from the ledger.
        client
            .execute("INSERT INTO applied_migrations VALUES ($1)", &[&latest])
            .expect("latest migration");
        let s = run_adapter_with_rules(&mut client, &adapter, false, &rules).expect("migrated");
        assert_eq!((s.ingested, s.errors), (1, 0));
        assert_eq!(
            sections(&mut client, &root),
            ["# Tide\n\nThe gate opens at six."]
        );
        client
            .batch_execute("DROP TABLE pg_temp.applied_migrations")
            .expect("drop the temporary ledger");
    }

    /// v1 → v2 → v1: the runner compares a file with the hash stored last
    /// for its path, not with any earlier one, so the restored v1 replaces
    /// v2 in the store. An unchanged file is still skipped.
    #[test]
    fn file_ledger_restores_an_earlier_version() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping file_ledger_restores_an_earlier_version: no PostgreSQL reachable");
            return;
        };
        let tmp = tempfile::tempdir().unwrap();
        let root = root_in(tmp.path());
        let _cleanup = Cleanup { root: root.clone() };
        let file = root.join("gate.md");
        let adapter = MarkdownAdapter::new(vec![root.clone()]);
        let rules = IgnoreRules::default();
        let run = |client: &mut postgres::Client| {
            let s = run_adapter_with_rules(client, &adapter, false, &rules).expect("ingest");
            (s.ingested, s.skipped, s.errors)
        };
        const V1: &str = "# Gate\n\nThe gate opens at six.\n";
        const V2: &str = "# Gate\n\nThe gate opens at seven.\n";

        std::fs::write(&file, V1).unwrap();
        assert_eq!(run(&mut client), (1, 0, 0), "v1 is new");
        assert_eq!(run(&mut client), (0, 1, 0), "v1 unchanged: skipped");
        std::fs::write(&file, V2).unwrap();
        assert_eq!(run(&mut client), (1, 0, 0), "v2 replaces v1");
        assert_eq!(
            sections(&mut client, &root),
            ["# Gate\n\nThe gate opens at seven."]
        );

        std::fs::write(&file, V1).unwrap();
        assert_eq!(run(&mut client), (1, 0, 0), "v1 again replaces v2");
        assert_eq!(
            sections(&mut client, &root),
            ["# Gate\n\nThe gate opens at six."]
        );
        assert_eq!(
            run(&mut client),
            (0, 1, 0),
            "the restored v1 is now current"
        );
    }
}
