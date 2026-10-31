//! Throughline Rust CLI.

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use throughline::types::IngestSummary;
use throughline::{
    access, accounts_cli, adapters, agent_key, answer_eval, ask, audit, config, conflicts,
    context_pack, costs, decisions, doctor, embedding, export_markdown, export_otel,
    extract_memory, generate_embeddings, generate_titles, handoff, ingest_run, lifecycle, llm, mcp,
    memory_writeback, migrate, ops_jobs, project_instructions, recall, recall_eval, reflect_memory,
    repair, retention, search, server, stale_digest, temporal, types,
};

#[derive(Parser)]
#[command(
    name = "throughline",
    version,
    about = "Local memory layer for AI coding sessions"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Apply schema migrations.
    Migrate {
        /// Show what would run without writing.
        #[arg(long)]
        status: bool,
    },
    /// Ingest session transcripts.
    Ingest {
        /// Adapter name, or `all`.
        #[arg(long, default_value = "all")]
        source: String,
        /// Print one line per file.
        #[arg(long, short = 'v')]
        verbose: bool,
        /// Exclude a rule (repeatable): `path:<glob>`, `project:<name>`, or a bare glob.
        #[arg(long = "ignore")]
        ignore: Vec<String>,
    },
    /// Search ingested messages.
    Search {
        /// Free-text query (ILIKE over message content).
        query: String,
        #[arg(long, default_value_t = 20)]
        limit: i32,
        /// Prefer semantic search when an embedding backend is available.
        #[arg(long)]
        semantic: bool,
        /// Source filter: memory | messages | all (default).
        #[arg(long, default_value = "all")]
        from: String,
    },
    /// Recall memory notes ranked by meaning, with the reason for each hit.
    Recall {
        /// The question or topic.
        text: String,
        #[arg(long)]
        project: Option<String>,
        /// current | as_of | associative | grounded
        #[arg(long, default_value = "current")]
        mode: String,
        /// Valid-at date (YYYY-MM-DD) for as_of.
        #[arg(long = "at")]
        at: Option<String>,
        /// Known-at date (YYYY-MM-DD) for as_of.
        #[arg(long = "known-at")]
        known_at: Option<String>,
        #[arg(long, default_value_t = recall::DEFAULT_LIMIT)]
        limit: usize,
        /// fulltext (default) | weighted
        #[arg(long, default_value = "fulltext")]
        rank: String,
        /// Print the full result as JSON.
        #[arg(long)]
        json: bool,
    },
    /// Answer a question from the corpus with citations.
    Ask {
        question: String,
        #[arg(long, default_value_t = 12)]
        limit: i32,
    },
    /// Extract durable memory from messages not yet extracted, newest conversations first.
    ExtractMemory {
        /// Conversations per run.
        #[arg(long, default_value_t = 20)]
        limit: usize,
        /// Transcript bytes sent to the model per request.
        #[arg(long, default_value_t = 24_000)]
        window_chars: usize,
    },
    /// Embed unembedded messages for semantic search.
    Embed {
        #[arg(long, default_value_t = 500)]
        limit: usize,
    },
    /// Generate titles for conversations that lack one.
    GenerateTitles {
        #[arg(long, default_value_t = 20)]
        limit: usize,
    },
    /// Export the corpus as a Markdown vault.
    ExportMarkdown {
        #[arg(long)]
        out: String,
        #[arg(long)]
        project: Option<String>,
        #[arg(long)]
        include_generated: bool,
        #[arg(long)]
        redact: bool,
        /// Also write a machine-readable evidence-manifest.json (provenance).
        #[arg(long)]
        evidence: bool,
    },
    /// Export the corpus as OTLP/JSON traces (OpenTelemetry GenAI conventions).
    ExportOtel {
        /// Output JSON file path.
        #[arg(long)]
        out: String,
        /// Restrict to one project name.
        #[arg(long)]
        project: Option<String>,
        /// Only conversations started on or after this date (YYYY-MM-DD).
        #[arg(long)]
        since: Option<String>,
        /// Include raw message text as gen_ai.message.content.
        #[arg(long = "include-content")]
        include_content: bool,
    },
    /// Report corpus counts.
    Status,
    /// Check backends and database.
    Doctor,
    /// Print a new key for agents that reach Throughline over HTTP.
    AgentKey,
    /// Team-mode accounts: create, list, roles, passwords, agent keys.
    Account {
        #[command(subcommand)]
        command: AccountCommand,
    },
    /// Print a resume brief for a new agent session.
    Handoff {
        #[arg(long)]
        project: Option<String>,
        #[arg(long, default_value_t = 8)]
        limit: usize,
        /// Also copy the brief to the system clipboard.
        #[arg(long)]
        copy: bool,
        /// Write the brief as Markdown to this path (refuses to overwrite).
        #[arg(long)]
        out: Option<std::path::PathBuf>,
    },
    /// Token / cost ledger by tool and model.
    Costs {
        #[arg(long, default_value_t = 30)]
        days: i32,
    },
    /// List active decisions with tool provenance and supersession chains.
    Decisions {
        #[arg(long)]
        project: Option<String>,
        #[arg(long)]
        include_superseded: bool,
        #[arg(long, default_value_t = 40)]
        limit: usize,
    },
    /// Index AGENTS.md / CLAUDE.md / .cursorrules into the memory store.
    ScanInstructions {
        #[arg(long)]
        root: Option<String>,
        #[arg(long, default_value_t = 4)]
        depth: usize,
    },
    /// Repair message timestamps that were set to import time.
    RepairTimestamps {
        /// Apply the fixes instead of just counting.
        #[arg(long)]
        apply: bool,
    },
    /// Import AI-assistant configuration inventory into the database.
    ImportConfig,
    /// Agent accountability: Throughline self-actions and MCP writes.
    Audit {
        #[arg(long, default_value_t = 30)]
        since: i32,
    },
    /// Find cross-tool memory conflicts.
    Conflicts {
        #[arg(long)]
        project: Option<String>,
    },
    /// Reflect on memory (dedup / stale / contradictions).
    Reflect {
        #[arg(long, default_value = "all")]
        mode: String,
        #[arg(long)]
        dry_run: bool,
        #[arg(long, default_value_t = 30)]
        limit: usize,
    },
    /// pg_dump the database into a backup directory.
    Backup {
        #[arg(long)]
        dir: Option<String>,
        #[arg(long, default_value_t = 14)]
        keep: usize,
    },
    /// Extract entities from memory chunks.
    ExtractEntities {
        #[arg(long, default_value_t = 100)]
        limit: usize,
    },
    /// Evaluate ask() answer quality (citations, hallucinations).
    AnswerEval {
        #[arg(long)]
        cases: Option<String>,
        #[arg(long, default_value_t = 20)]
        limit: usize,
    },
    /// Pack a project's decisions, notes and sessions into a token budget.
    Context {
        #[arg(long)]
        project: String,
        /// What the work is about; pulls in matching notes.
        #[arg(long)]
        task: Option<String>,
        /// Estimated tokens, 64 to 32768.
        #[arg(long, default_value_t = context_pack::DEFAULT_BUDGET)]
        budget: usize,
        /// Print the full pack as JSON.
        #[arg(long)]
        json: bool,
    },
    /// Find notes that say nearly the same thing and merge them on approval.
    Consolidate {
        /// List the groups, change nothing.
        #[arg(long)]
        dry_run: bool,
        /// Merge every group into one new note holding the longest text.
        #[arg(long)]
        apply: bool,
        /// Cosine at or above which two notes belong together (default: the
        /// active embedding model's profile, 0.92 for the baseline).
        #[arg(long)]
        min_similarity: Option<f64>,
        /// Print the groups as JSON (with --dry-run).
        #[arg(long)]
        json: bool,
    },
    /// Measure recall against cosine, full text and random on a test set.
    RecallEval {
        /// JSON file: [{"q": "...", "project": "..." | null, "expected": [id, ...]}]
        #[arg(long)]
        set: String,
        #[arg(long, default_value_t = 5)]
        k: usize,
        #[arg(long, default_value_t = 7)]
        seed: u64,
        #[arg(long)]
        json: bool,
    },
    /// Digest of stale / superseded / never-opened memory.
    StaleDigest {
        #[arg(long, default_value_t = 14)]
        since: i32,
    },
    /// Write distilled decisions into AGENTS.md / CLAUDE.md.
    Writeback {
        #[arg(long)]
        root: Option<String>,
        #[arg(long)]
        project: Option<String>,
        #[arg(long, default_value_t = 15)]
        limit: usize,
    },
    /// Run the MCP stdio server.
    Mcp,
    /// Serve the local web UI (loopback by default).
    Serve {
        #[arg(long, default_value = "127.0.0.1")]
        host: String,
        #[arg(long, default_value_t = 8787)]
        port: u16,
    },
    /// Retention / archiving. Dry-run by default; --apply archives first, then deletes.
    Retention {
        /// Age threshold in days, measured on ended_at (fallback started_at).
        #[arg(long = "older-than")]
        older_than: i64,
        /// Restrict to one project name.
        #[arg(long)]
        project: Option<String>,
        /// Archive directory; required together with --apply.
        #[arg(long = "archive-dir")]
        archive_dir: Option<String>,
        /// Actually archive and delete (default is dry-run).
        #[arg(long)]
        apply: bool,
        /// Allow windows shorter than 180 days (EU AI Act Art. 19 six-month mindset).
        #[arg(long = "allow-short-retention")]
        allow_short_retention: bool,
    },
}

const ROLES: [&str; 3] = ["viewer", "editor", "admin"];

/// Passwords never come from arguments: they are typed at the terminal
/// (hidden) or read from an owner-only file.
#[derive(Subcommand)]
enum AccountCommand {
    /// Create an account; asks for its password.
    Add {
        name: String,
        #[arg(long, default_value = "viewer", value_parser = ROLES)]
        role: String,
        /// Shown in the web UI (default: the account name).
        #[arg(long)]
        display_name: Option<String>,
        /// Read the password from this file (owner-only, chmod 600).
        #[arg(long)]
        password_file: Option<std::path::PathBuf>,
    },
    /// List accounts and their active agent keys.
    List,
    /// Change an account's role; ends its sessions.
    Role {
        name: String,
        #[arg(value_parser = ROLES)]
        role: String,
    },
    /// Enable an account.
    Enable { name: String },
    /// Disable an account; ends its sessions, its agent keys stop working.
    Disable { name: String },
    /// Clear an account's failed sign-ins.
    Unlock { name: String },
    /// Set a new password; ends the account's sessions.
    Password {
        name: String,
        /// Read the password from this file (owner-only, chmod 600).
        #[arg(long)]
        password_file: Option<std::path::PathBuf>,
    },
    /// Create an agent key for an account and print it once.
    AgentKey {
        name: String,
        /// What the key is for, shown by `account list`.
        #[arg(long)]
        label: Option<String>,
    },
    /// Revoke an account's agent key by its id (see `account list`).
    AgentKeyRevoke { name: String, id: i64 },
}

/// Output leaves account names out except in `list` and in errors.
fn run_account(command: AccountCommand) -> Result<()> {
    let connect = || {
        config::DbConfig::from_env()
            .connect()
            .context("connect to PostgreSQL")
    };
    match command {
        AccountCommand::Add {
            name,
            role,
            display_name,
            password_file,
        } => {
            accounts_cli::validate_username(&name)?;
            let role = access::Role::parse(&role)?;
            let mut client = connect()?;
            if accounts_cli::exists(&mut client, &name)? {
                anyhow::bail!("account `{name}` already exists");
            }
            let password = accounts_cli::read_password(password_file.as_deref(), true)?;
            accounts_cli::add(&mut client, &name, display_name.as_deref(), role, &password)?;
            println!("Account created with role {}.", role.as_str());
            if access::auth_mode_from(std::env::var("THROUGHLINE_AUTH_MODE").ok().as_deref())
                .is_ok_and(|mode| mode == access::AuthMode::Local)
            {
                println!("With accounts in the database the server starts only with THROUGHLINE_AUTH_MODE=team.");
            }
        }
        AccountCommand::List => {
            let mut client = connect()?;
            let accounts = accounts_cli::list(&mut client)?;
            if accounts.is_empty() {
                println!("No accounts.");
                return Ok(());
            }
            let mut rows = vec![[
                "NAME".to_string(),
                "DISPLAY NAME".to_string(),
                "ROLE".to_string(),
                "ENABLED".to_string(),
            ]];
            rows.extend(accounts.into_iter().map(|a| {
                [
                    a.username,
                    a.display_name,
                    a.role.as_str().to_string(),
                    if a.enabled { "yes" } else { "no" }.to_string(),
                ]
            }));
            print_table(&rows);
            let keys = accounts_cli::active_agent_keys(&mut client)?;
            if !keys.is_empty() {
                println!();
                let mut rows = vec![[
                    "KEY ID".to_string(),
                    "ACCOUNT".to_string(),
                    "LABEL".to_string(),
                ]];
                rows.extend(
                    keys.into_iter()
                        .map(|k| [k.id.to_string(), k.username, k.label.unwrap_or_default()]),
                );
                print_table(&rows);
            }
        }
        AccountCommand::Role { name, role } => {
            let role = access::Role::parse(&role)?;
            let ended = accounts_cli::set_role(&mut connect()?, &name, role)?;
            println!("Role set to {}; {ended} session(s) ended.", role.as_str());
        }
        AccountCommand::Enable { name } => {
            accounts_cli::enable(&mut connect()?, &name)?;
            println!("Account enabled.");
        }
        AccountCommand::Disable { name } => {
            let ended = accounts_cli::disable(&mut connect()?, &name)?;
            println!("Account disabled; {ended} session(s) ended, its agent keys no longer work.");
        }
        AccountCommand::Unlock { name } => {
            if accounts_cli::unlock(&mut connect()?, &name)? {
                println!("Failed sign-ins cleared.");
            } else {
                println!("No failed sign-ins were recorded.");
            }
        }
        AccountCommand::Password {
            name,
            password_file,
        } => {
            let mut client = connect()?;
            if !accounts_cli::exists(&mut client, &name)? {
                anyhow::bail!("no account named `{name}`");
            }
            let password = accounts_cli::read_password(password_file.as_deref(), true)?;
            let ended = accounts_cli::set_password(&mut client, &name, &password)?;
            println!("Password changed; {ended} session(s) ended.");
        }
        AccountCommand::AgentKey { name, label } => {
            let (id, key) = accounts_cli::new_agent_key(&mut connect()?, &name, label.as_deref())?;
            println!("{key}");
            println!(
                "Agent key {id}. It is shown only now; the agent sends it as \
                 `Authorization: Bearer <key>`."
            );
        }
        AccountCommand::AgentKeyRevoke { name, id } => {
            accounts_cli::revoke_agent_key(&mut connect()?, &name, id)?;
            println!("Agent key {id} revoked.");
        }
    }
    Ok(())
}

/// Left-aligned columns, two spaces apart.
fn print_table<const N: usize>(rows: &[[String; N]]) {
    let mut widths = [0usize; N];
    for row in rows {
        for (width, cell) in widths.iter_mut().zip(row) {
            *width = (*width).max(cell.chars().count());
        }
    }
    for row in rows {
        let line: Vec<String> = row
            .iter()
            .zip(widths)
            .map(|(cell, width)| format!("{cell:<width$}"))
            .collect();
        println!("{}", line.join("  ").trim_end());
    }
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Command::Migrate { status } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            if status {
                for (name, applied) in migrate::status(&mut client)? {
                    println!("{name}: {}", if applied { "applied" } else { "pending" });
                }
            } else {
                let n = migrate::migrate(&mut client)?;
                println!("applied {n} migration(s)");
            }
        }
        Command::Ingest {
            source,
            verbose,
            ignore,
        } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let rules = throughline::ignore::IgnoreRules::load(&ignore);
            if source == "all" {
                for summary in ingest_run::run_all_with_rules(&mut client, verbose, &rules)? {
                    print_summary(&summary);
                }
            } else {
                let adapter = adapters::registry::by_name(&source)
                    .ok_or_else(|| anyhow::anyhow!("unknown source {source:?}"))?;
                let summary = ingest_run::run_adapter_with_rules(
                    &mut client,
                    adapter.as_ref(),
                    verbose,
                    &rules,
                )?;
                print_summary(&summary);
            }
        }
        Command::Recall {
            text,
            project,
            mode,
            at,
            known_at,
            limit,
            rank,
            json,
        } => {
            let mode: recall::RecallMode = mode.parse().map_err(|e: String| anyhow::anyhow!(e))?;
            let rank: recall::Rank = rank.parse().map_err(|e: String| anyhow::anyhow!(e))?;
            recall::check_query(&text).map_err(|e| anyhow::anyhow!(e))?;
            let date =
                |name: &str, v: Option<String>| -> Result<Option<chrono::DateTime<chrono::Utc>>> {
                    match v.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
                        None => Ok(None),
                        Some(s) => temporal::parse_as_of_instant(s).map(Some).ok_or_else(|| {
                            anyhow::anyhow!(
                                "{name} must be YYYY-MM-DD or an RFC 3339 timestamp, got {s:?}"
                            )
                        }),
                    }
                };
            let valid_at = date("--at", at)?;
            let known_at = date("--known-at", known_at)?;
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let emb = if recall::needs_embedding(mode, rank) {
                recall::query_embedding(&text)?
            } else {
                None
            };
            let q = recall::RecallQuery {
                text: &text,
                project: project.as_deref(),
                mode,
                valid_at,
                known_at,
                limit: limit.clamp(1, 50),
                rank,
            };
            let result = recall::recall(&mut client, &q, emb.as_ref())?;
            let ids: Vec<i64> = result.hits.iter().map(|h| h.id).collect();
            lifecycle::record_use_or_log(&mut client, &ids);
            if json {
                println!("{}", serde_json::to_string_pretty(&result)?);
                return Ok(());
            }
            if let Some(note) = &result.degraded {
                eprintln!("{note}");
            }
            if result.hits.is_empty() {
                println!("no matches");
            }
            for h in &result.hits {
                println!("{}", recall::render_line(h));
            }
        }
        Command::Context {
            project,
            task,
            budget,
            json,
        } => {
            if !(context_pack::MIN_BUDGET..=context_pack::MAX_BUDGET).contains(&budget) {
                anyhow::bail!(
                    "--budget must be between {} and {}",
                    context_pack::MIN_BUDGET,
                    context_pack::MAX_BUDGET
                );
            }
            let project = project.trim();
            if project.is_empty() {
                anyhow::bail!("--project must not be empty");
            }
            let task = task.as_deref().map(str::trim).filter(|t| !t.is_empty());
            if let Some(t) = task {
                recall::check_query(t).map_err(|e| anyhow::anyhow!(e))?;
            }
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let pack = context_pack::build_context(
                &mut client,
                &context_pack::ContextRequest {
                    project,
                    task,
                    budget,
                },
                None,
            )?;
            let ids = context_pack::cited_note_ids(&pack);
            lifecycle::record_use_or_log(&mut client, &ids);
            if json {
                println!("{}", serde_json::to_string_pretty(&pack)?);
                return Ok(());
            }
            println!("{}", pack.text);
            println!(
                "tokens: {} of {} (estimated), skipped: {}",
                pack.tokens_used, pack.budget, pack.skipped
            );
        }
        Command::Search {
            query,
            limit,
            semantic,
            from,
        } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            if semantic {
                match embedding::resolve() {
                    Ok(backend) => {
                        let hits = generate_embeddings::semantic_search(
                            &mut client,
                            &backend,
                            &query,
                            limit,
                        )?;
                        if hits.is_empty() {
                            println!("no matches");
                            return Ok(());
                        }
                        for (id, snippet, score) in hits {
                            println!("[{score:.3}] #{id} {}", snippet.replace('\n', " "));
                        }
                        return Ok(());
                    }
                    Err(e) => eprintln!("semantic search unavailable ({e}); using lexical"),
                }
            }
            let source = search::SearchSource::parse(&from);
            let hits = search::search_from(&mut client, &query, limit, source)?;
            if hits.is_empty() {
                println!("no matches");
                return Ok(());
            }
            for h in hits {
                println!(
                    "[{} | {} | {} | {}] {}",
                    h.project_name.as_deref().unwrap_or("unknown"),
                    h.source_tool.as_deref().unwrap_or("?"),
                    h.role,
                    h.created_at.to_rfc3339(),
                    h.snippet.replace('\n', " ")
                );
                println!(
                    "    conversation={} session={}",
                    h.conversation_id, h.session_id
                );
            }
        }
        Command::Status => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let convs = search::count_conversations(&mut client)?;
            let msgs = search::count_messages(&mut client)?;
            println!("conversations: {convs}");
            println!("messages:      {msgs}");
        }
        Command::Ask { question, limit } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let backend = llm::resolve_backend().context("resolve generation backend")?;
            llm::ensure_local_or_allowed(&backend)?;
            let answer = ask::ask(&mut client, &question, &backend, limit)?;
            println!("{}", answer.answer);
            println!();
            println!(
                "model={} local={} cited={}",
                answer.model, answer.local, answer.cited
            );
            for (i, s) in answer.sources.iter().enumerate() {
                println!(
                    "[{}] #{} {} — {}",
                    i + 1,
                    s.conversation_id,
                    s.project_name.as_deref().unwrap_or("unknown"),
                    s.snippet.chars().take(100).collect::<String>()
                );
            }
        }
        Command::ExtractMemory {
            limit,
            window_chars,
        } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let backend = llm::resolve_backend()?;
            llm::ensure_local_or_allowed(&backend)?;
            let mut total = extract_memory::ExtractSummary::default();
            for conv_id in extract_memory::pending_conversations(&mut client, limit)? {
                let s = extract_memory::extract_conversation(
                    &mut client,
                    &backend,
                    conv_id,
                    window_chars,
                )?;
                total.windows += s.windows;
                total.chunks += s.chunks;
                total.errors += s.errors;
            }
            println!(
                "extracted {} memory chunk(s) from {} window(s), {} error(s)",
                total.chunks, total.windows, total.errors
            );
        }
        Command::Embed { limit } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let backend = embedding::resolve()?;
            let target = embedding::resolve_target();
            let job = generate_embeddings::embed_job_with(
                &mut client,
                &backend,
                target,
                limit,
                |b, text| embedding::embed_text(b, text, embedding::TextKind::Document),
            )?;
            for (label, summary) in &job.runs {
                println!(
                    "embed[{label}]: embedded={} skipped={} errors={}",
                    summary.embedded, summary.skipped, summary.errors
                );
            }
            if let Some(e) = &job.target_error {
                println!("target model: {e}");
            }
        }
        Command::GenerateTitles { limit } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let rows = client.query(
                r#"
                SELECT id, summary FROM conversations
                WHERE summary IS NULL OR summary = ''
                ORDER BY started_at DESC
                LIMIT $1::bigint
                "#,
                &[&(limit as i64)],
            )?;
            if let Ok(backend) = llm::resolve_backend() {
                llm::ensure_local_or_allowed(&backend)?;
            }
            let mut titled = 0usize;
            for row in rows {
                let id: i64 = row.get(0);
                let msgs = client.query(
                    r#"
                    SELECT role::text, COALESCE(content,'') FROM messages
                    WHERE conversation_id = $1 ORDER BY created_at, id
                    "#,
                    &[&id],
                )?;
                let messages: Vec<types::NormalisedMessage> = msgs
                    .into_iter()
                    .map(|r| types::NormalisedMessage {
                        role: r.get(0),
                        content: r.get(1),
                        ..Default::default()
                    })
                    .collect();
                let title = generate_titles::title_messages(&messages)
                    .unwrap_or_else(|_| generate_titles::fallback_title(&messages));
                client.execute(
                    "UPDATE conversations SET summary = $2, updated_at = now() WHERE id = $1",
                    &[&id, &title],
                )?;
                titled += 1;
            }
            println!("titled {titled} conversation(s)");
        }
        Command::ExportMarkdown {
            out,
            project,
            include_generated,
            redact,
            evidence,
        } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let summary = export_markdown::export_markdown(
                &mut client,
                std::path::Path::new(&out),
                project.as_deref(),
                include_generated,
                redact,
                evidence,
                None,
            )?;
            println!(
                "export: projects={} sessions={} messages={} files={}",
                summary.projects, summary.sessions, summary.messages, summary.files_written
            );
        }
        Command::ExportOtel {
            out,
            project,
            since,
            include_content,
        } => {
            let since_date = match since {
                Some(s) => Some(
                    chrono::NaiveDate::parse_from_str(&s, "%Y-%m-%d")
                        .with_context(|| format!("parse --since {s:?} as YYYY-MM-DD"))?,
                ),
                None => None,
            };
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let summary = export_otel::export_otel(
                &mut client,
                std::path::Path::new(&out),
                project.as_deref(),
                since_date,
                include_content,
            )?;
            println!(
                "export-otel: traces={} spans={} out={}",
                summary.traces, summary.spans, out
            );
        }
        Command::Handoff {
            project,
            limit,
            copy,
            out,
        } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let brief = handoff::build_handoff(&mut client, project.as_deref(), limit)?;
            let text = handoff::render_text(&brief);
            print!("{text}");
            if let Some(path) = out {
                handoff::write_handoff_file(&path, &text)?;
            }
            if copy {
                let n = text.chars().count();
                handoff::copy_to_clipboard(&text)?;
                eprintln!("handoff copied to clipboard, {n} chars");
            }
        }
        Command::Costs { days } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let report = costs::cost_report(&mut client, days)?;
            println!(
                "costs ({}d): token_in={} token_out={} cost_usd={:.4}",
                report.days, report.total_token_in, report.total_token_out, report.total_cost_usd
            );
            for r in &report.rows {
                if let Some(day) = &r.day {
                    println!("  {day}: in={} out={}", r.token_in, r.token_out);
                } else {
                    println!(
                        "  {} / {}: sessions={} in={} out={} ${:.4}",
                        r.source_tool,
                        r.model.as_deref().unwrap_or("-"),
                        r.conversations,
                        r.token_in,
                        r.token_out,
                        r.cost_usd
                    );
                }
            }
        }
        Command::Decisions {
            project,
            include_superseded,
            limit,
        } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let report = decisions::list_decisions(
                &mut client,
                project.as_deref(),
                include_superseded,
                limit,
            )?;
            print!("{}", decisions::render_text(&report));
        }
        Command::ScanInstructions { root, depth } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let roots = match root {
                Some(r) => vec![std::path::PathBuf::from(r)],
                None => {
                    let mut r = project_instructions::default_roots();
                    r.extend(project_instructions::roots_from_corpus(&mut client)?);
                    r
                }
            };
            let summary = project_instructions::ingest_instructions(&mut client, &roots, depth)?;
            println!(
                "instructions: seen={} upserted={} skipped={} errors={}",
                summary.files_seen, summary.upserted, summary.skipped, summary.errors
            );
        }
        Command::RepairTimestamps { apply } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let summary = repair::repair_timestamps(&mut client, apply)?;
            let mode = if apply { "applied" } else { "dry-run" };
            println!(
                "repair-timestamps: conversations={} messages={} ({})",
                summary.conversations, summary.messages, mode
            );
        }
        Command::ImportConfig => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let summary = throughline::config_import::run(&mut client)?;
            println!("{summary}");
        }
        Command::Audit { since } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let report = audit::audit(&mut client, since)?;
            print!("{}", audit::render_text(&report));
        }
        Command::AnswerEval { cases, limit } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let backend = llm::resolve_backend().context("resolve generation backend")?;
            llm::ensure_local_or_allowed(&backend)?;
            let case_list = match cases {
                Some(path) => answer_eval::load_cases(std::path::Path::new(&path))?,
                None => {
                    // Default suite: pull open questions from memory as probes.
                    let rows = client.query(
                        "SELECT content FROM memory_chunks WHERE status='active' AND category='decision' ORDER BY created_at DESC LIMIT $1::bigint",
                        &[&(limit as i64)],
                    )?;
                    rows.into_iter()
                        .enumerate()
                        .map(|(i, r)| {
                            let c: String = r.get(0);
                            answer_eval::EvalCase {
                                id: format!("mem-{i}"),
                                question: format!(
                                    "What did we decide about: {}",
                                    c.chars().take(60).collect::<String>()
                                ),
                                must_cite_conversation_ids: vec![],
                                expected_phrases: vec![],
                                forbidden_phrases: vec!["I think".into(), "probably".into()],
                            }
                        })
                        .collect()
                }
            };
            let report = answer_eval::run_eval(&mut client, &backend, &case_list)?;
            print!("{}", answer_eval::render_text(&report));
        }
        Command::Consolidate {
            dry_run,
            apply,
            min_similarity,
            json,
        } => {
            if dry_run == apply {
                anyhow::bail!(
                    "choose one: --dry-run lists the groups, --apply merges them \
                     (for example: throughline consolidate --dry-run)"
                );
            }
            if let Some(v) = min_similarity {
                lifecycle::check_min_similarity(v).map_err(|m| anyhow::anyhow!(m))?;
            }
            let backend = embedding::resolve()
                .context("consolidate needs the embedding model to find similar notes")?;
            let min_similarity =
                min_similarity.unwrap_or_else(|| lifecycle::default_min_similarity(Some(&backend)));
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let groups = lifecycle::consolidation_candidates(
                &mut client,
                &backend.model,
                backend.column,
                min_similarity,
            )?;
            if apply {
                let mut failed = 0usize;
                for g in &groups {
                    let ids: Vec<i64> = g.notes.iter().map(|n| n.id).collect();
                    match lifecycle::consolidate(&mut client, &ids, &g.proposal, None) {
                        Ok(new_id) => println!("{ids:?} -> {new_id}"),
                        Err(e) => {
                            failed += 1;
                            eprintln!("{ids:?} not merged: {e}");
                        }
                    }
                }
                println!("{} group(s) found", groups.len());
                if failed > 0 {
                    anyhow::bail!("{failed} of {} group(s) could not be merged", groups.len());
                }
            } else if json {
                println!("{}", serde_json::to_string_pretty(&groups)?);
            } else {
                for g in &groups {
                    println!(
                        "{} notes, similarity >= {:.3}",
                        g.notes.len(),
                        g.min_similarity
                    );
                    for n in &g.notes {
                        println!(
                            "  #{} [{}] {}",
                            n.id,
                            n.category,
                            n.content
                                .chars()
                                .take(100)
                                .collect::<String>()
                                .replace('\n', " ")
                        );
                    }
                }
                println!("{} group(s)", groups.len());
            }
        }
        Command::RecallEval { set, k, seed, json } => {
            let cases = recall_eval::load_cases(std::path::Path::new(&set))?;
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let report = recall_eval::run_eval(&mut client, &cases, k, seed)?;
            if json {
                println!("{}", serde_json::to_string_pretty(&report)?);
            } else {
                print!("{}", recall_eval::render_text(&report));
            }
        }
        Command::StaleDigest { since } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let digest = stale_digest::build_digest(&mut client, since)?;
            print!("{}", stale_digest::render_text(&digest));
        }
        Command::Writeback {
            root,
            project,
            limit,
        } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let root = root
                .map(std::path::PathBuf::from)
                .unwrap_or_else(|| std::env::current_dir().unwrap_or_default());
            let summary =
                memory_writeback::writeback(&mut client, &root, project.as_deref(), limit)?;
            println!(
                "writeback: {} items → {} ({})",
                summary.items_written,
                summary.file,
                if summary.replaced {
                    "replaced"
                } else if summary.created {
                    "created"
                } else {
                    "appended"
                }
            );
        }
        Command::Account { command } => run_account(command)?,
        Command::AgentKey => {
            println!("{}", agent_key::generate());
            println!("Put it into .env as THROUGHLINE_AGENT_KEY and restart the server.");
        }
        Command::Doctor => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let report = doctor::run(&mut client)?;
            println!("database:   {}", report.database);
            println!("generation: {}", report.generation);
            println!("embeddings: {}", report.embeddings);
            if let Some(t) = &report.embedding_target {
                println!("embedding target: {t}");
            }
            println!("embedding missing: {}", report.embedding_missing);
            println!("agent access: {}", report.agent_access);
            println!("auth mode: {}", report.auth_mode);
            for n in report.notes {
                println!("note: {n}");
            }
        }
        Command::Conflicts { project } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let report = conflicts::find_conflicts(&mut client, project.as_deref(), 0.85)?;
            println!(
                "conflicts: {} (checked {})",
                report.conflicts.len(),
                report.checked
            );
            for c in &report.conflicts {
                println!(
                    "[{}] {} — {} / {}",
                    c.kind,
                    c.detail,
                    c.left_content.chars().take(60).collect::<String>(),
                    c.right_content.chars().take(60).collect::<String>()
                );
            }
        }
        Command::Reflect {
            mode,
            dry_run,
            limit,
        } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let backend = llm::resolve_backend().ok();
            let summaries = match mode.as_str() {
                "dedup" => vec![reflect_memory::dedup(&mut client, dry_run)?],
                "stale" => vec![reflect_memory::expire_stale(&mut client, dry_run)?],
                "contradictions" => {
                    let b = backend.as_ref().ok_or_else(|| {
                        anyhow::anyhow!("contradictions need a generation backend")
                    })?;
                    llm::ensure_local_or_allowed(b)?;
                    vec![reflect_memory::contradictions(
                        &mut client,
                        b,
                        dry_run,
                        limit,
                    )?]
                }
                _ => {
                    // "all" skips the model step on a non-local backend instead of failing the rest.
                    let usable =
                        backend
                            .as_ref()
                            .filter(|b| match llm::ensure_local_or_allowed(b) {
                                Ok(()) => true,
                                Err(e) => {
                                    eprintln!("reflect: skipping contradictions: {e}");
                                    false
                                }
                            });
                    reflect_memory::run_all(&mut client, usable, dry_run, limit)?
                }
            };
            let mut failed = 0usize;
            for s in summaries {
                println!(
                    "{}: examined={} acted={}{}{}",
                    s.mode,
                    s.examined,
                    s.acted,
                    if s.failed > 0 {
                        format!(" failed={}", s.failed)
                    } else {
                        String::new()
                    },
                    if s.dry_run { " (dry-run)" } else { "" }
                );
                failed += s.failed;
            }
            if failed > 0 {
                anyhow::bail!("{failed} group(s) could not be merged");
            }
        }
        Command::Backup { dir, keep } => {
            let dir = dir.unwrap_or_else(|| {
                std::env::var("THROUGHLINE_BACKUP_DIR").unwrap_or_else(|_| {
                    dirs::home_dir()
                        .unwrap_or_default()
                        .join(".local/share/throughline/backups")
                        .to_string_lossy()
                        .to_string()
                })
            });
            let out = ops_jobs::backup_database(std::path::Path::new(&dir), keep)?;
            println!("backup written to {}", out.display());
        }
        Command::ExtractEntities { limit } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let n = ops_jobs::extract_entities_from_chunks(&mut client, limit)?;
            println!("entity mentions upserted: {n}");
        }
        Command::Mcp => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let ctx = mcp::McpContext {
                transport: throughline::mcp_protocol::Transport::Stdio,
                read_only: mcp::read_only_from(
                    std::env::var("THROUGHLINE_MCP_READ_ONLY").ok().as_deref(),
                ),
            };
            mcp::run_stdio(&mut client, &ctx)?;
        }
        Command::Serve { host, port } => {
            let client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let runtime = tokio::runtime::Runtime::new().context("tokio runtime")?;
            runtime.block_on(server::serve(&host, port, client))?;
        }
        Command::Retention {
            older_than,
            project,
            archive_dir,
            apply,
            allow_short_retention,
        } => {
            // Validate before connecting: a refused policy never opens the DB.
            retention::check_retention_window(older_than, allow_short_retention)?;
            let archive_path = archive_dir.as_deref().map(std::path::Path::new);
            retention::check_apply_archive(apply, archive_path)?;
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let report = retention::run_retention(
                &mut client,
                older_than,
                project.as_deref(),
                archive_path,
                apply,
                allow_short_retention,
            )?;
            print!("{}", retention::render_report(&report));
        }
    }
    Ok(())
}

fn print_summary(s: &IngestSummary) {
    // Without ignore matches the line keeps its historical shape (byte-identical).
    if s.ignored > 0 {
        println!(
            "[{}] files={} ingested={} refreshed={} skipped={} self_ref={} errors={} messages={} ignored={}",
            s.adapter,
            s.files_seen,
            s.ingested,
            s.refreshed,
            s.skipped,
            s.self_referential,
            s.errors,
            s.messages_written,
            s.ignored
        );
    } else {
        println!(
            "[{}] files={} ingested={} refreshed={} skipped={} self_ref={} errors={} messages={}",
            s.adapter,
            s.files_seen,
            s.ingested,
            s.refreshed,
            s.skipped,
            s.self_referential,
            s.errors,
            s.messages_written
        );
    }
}
