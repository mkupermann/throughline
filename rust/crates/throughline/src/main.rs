//! Throughline Rust CLI.

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use throughline::adapters::{sha256_file, Adapter};
use throughline::types::IngestSummary;
use throughline::{
    adapters, answer_eval, ask, audit, config, conflicts, costs, decisions, doctor, embedding,
    export_markdown, extract_memory, generate_embeddings, generate_titles, handoff, ingest_run,
    llm, mcp, memory_writeback, migrate, ops_jobs, project_instructions, reflect_memory, search,
    server, stale_digest, types, writer,
};

#[derive(Parser)]
#[command(name = "throughline", version, about = "Local memory layer for AI coding sessions")]
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
    /// Answer a question from the corpus with citations.
    Ask {
        question: String,
        #[arg(long, default_value_t = 12)]
        limit: i32,
    },
    /// Extract durable memory from recent conversations.
    ExtractMemory {
        #[arg(long, default_value_t = 20)]
        limit: usize,
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
    },
    /// Report corpus counts.
    Status,
    /// Check backends and database.
    Doctor,
    /// Print a resume brief for a new agent session.
    Handoff {
        #[arg(long)]
        project: Option<String>,
        #[arg(long, default_value_t = 8)]
        limit: usize,
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
        Command::Ingest { source, verbose } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            if source == "all" {
                for summary in ingest_run::run_all(&mut client, verbose)? {
                    print_summary(&summary);
                }
            } else {
                let adapter = adapters::registry::by_name(&source)
                    .ok_or_else(|| anyhow::anyhow!("unknown source {source:?}"))?;
                let summary = ingest_run::run_adapter(&mut client, adapter.as_ref(), verbose)?;
                print_summary(&summary);
            }
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
        Command::ExtractMemory { limit } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let backend = llm::resolve_backend()?;
            let rows = client.query(
                r#"
                SELECT c.id,
                       (SELECT string_agg(m.role || ': ' || left(COALESCE(m.content,''), 2000), E'\n' ORDER BY m.id)
                        FROM messages m WHERE m.conversation_id = c.id) AS transcript
                FROM conversations c
                WHERE c.generated_by IS NULL
                ORDER BY c.started_at DESC
                LIMIT $1::bigint
                "#,
                &[&(limit as i64)],
            )?;
            let mut extracted = 0usize;
            for row in rows {
                let conv_id: i64 = row.get(0);
                let transcript: Option<String> = row.get(1);
                let Some(transcript) = transcript else { continue };
                match extract_memory::extract(&backend, &transcript, true) {
                    Ok(items) => {
                        for item in items {
                            client.execute(
                                r#"
                                INSERT INTO memory_chunks
                                    (source_type, source_id, content, category, project_name,
                                     confidence, tags, status)
                                VALUES ('conversation', $1, $2, $3::memory_category,
                                        (SELECT project_name FROM conversations WHERE id = $1),
                                        $4, $5, 'active')
                                "#,
                                &[
                                    &conv_id,
                                    &item.content,
                                    &item.category,
                                    &item.confidence,
                                    &item.tags,
                                ],
                            )?;
                            extracted += 1;
                        }
                    }
                    Err(e) => eprintln!("extract #{conv_id}: {e}"),
                }
            }
            println!("extracted {extracted} memory chunk(s)");
        }
        Command::Embed { limit } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let backend = embedding::resolve()?;
            let summary = generate_embeddings::embed_pending(&mut client, &backend, limit)?;
            println!(
                "embed[{}]: embedded={} skipped={} errors={}",
                embedding::label(&backend),
                summary.embedded,
                summary.skipped,
                summary.errors
            );
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
                    .map(|r| {
                        let mut m = types::NormalisedMessage::default();
                        m.role = r.get(0);
                        m.content = r.get(1);
                        m
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
            )?;
            println!(
                "export: projects={} sessions={} messages={} files={}",
                summary.projects, summary.sessions, summary.messages, summary.files_written
            );
        }
        Command::Handoff { project, limit } => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let brief = handoff::build_handoff(&mut client, project.as_deref(), limit)?;
            print!("{}", handoff::render_text(&brief));
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
            let report =
                decisions::list_decisions(&mut client, project.as_deref(), include_superseded, limit)?;
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
                                question: format!("What did we decide about: {}", c.chars().take(60).collect::<String>()),
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
            let summary = memory_writeback::writeback(
                &mut client,
                &root,
                project.as_deref(),
                limit,
            )?;
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
        Command::Doctor => {
            let mut client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let report = doctor::run(&mut client)?;
            println!("database:   {}", report.database);
            println!("generation: {}", report.generation);
            println!("embeddings: {}", report.embeddings);
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
                    let b = backend
                        .as_ref()
                        .ok_or_else(|| anyhow::anyhow!("contradictions need a generation backend"))?;
                    vec![reflect_memory::contradictions(&mut client, b, dry_run, limit)?]
                }
                _ => reflect_memory::run_all(&mut client, backend.as_ref(), dry_run, limit)?,
            };
            for s in summaries {
                println!(
                    "{}: examined={} acted={}{}",
                    s.mode,
                    s.examined,
                    s.acted,
                    if s.dry_run { " (dry-run)" } else { "" }
                );
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
            mcp::run_stdio(&mut client)?;
        }
        Command::Serve { host, port } => {
            let client = config::DbConfig::from_env()
                .connect()
                .context("connect to PostgreSQL")?;
            let runtime = tokio::runtime::Runtime::new().context("tokio runtime")?;
            runtime.block_on(server::serve(&host, port, client))?;
        }
    }
    Ok(())
}

fn print_summary(s: &IngestSummary) {
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

