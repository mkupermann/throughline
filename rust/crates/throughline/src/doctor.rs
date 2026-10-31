//! Doctor: report whether the pipeline can run here.

use crate::embedding;
use crate::generate_embeddings;
use crate::llm;
use anyhow::Result;
use postgres::GenericClient;

#[derive(Debug, serde::Serialize)]
pub struct DoctorReport {
    pub database: String,
    pub generation: String,
    pub embeddings: String,
    /// Progress of `THROUGHLINE_EMBED_TARGET_MODEL`, when one is set.
    pub embedding_target: Option<String>,
    /// Notes and messages the active model has no vector for.
    pub embedding_missing: String,
    pub generation_local: bool,
    /// Never the key: `agent_key::doctor_line` in local mode,
    /// `team mode, <n> account keys` in team mode.
    pub agent_access: String,
    /// `local`, or `team (<n> accounts, <m> private notes)`.
    pub auth_mode: String,
    pub notes: Vec<String>,
}

/// The report for a client of the server (`GET /api/health-recovery`),
/// running in `mode` with `key`. In team mode the client gets neither the
/// text of a failed `auth mode` or `agent access` check (it goes to the log,
/// without row values) nor the note that the host sets
/// `THROUGHLINE_AGENT_KEY`. Local mode gets what `throughline doctor` gets.
pub fn for_client<C: GenericClient>(
    client: &mut C,
    actor: &crate::access::Actor,
    mode: crate::access::AuthMode,
    key: &crate::agent_key::KeyState,
) -> Result<DoctorReport> {
    let team = mode == crate::access::AuthMode::Team;
    report(client, actor, Ok(mode), key, team)
}

/// The report as `actor` may see it: the embedding progress counts only the
/// notes `actor` sees (the CLI passes [`crate::access::Actor::local`]).
pub fn run<C: GenericClient>(client: &mut C, actor: &crate::access::Actor) -> Result<DoctorReport> {
    let mode =
        crate::access::auth_mode_from(std::env::var("THROUGHLINE_AUTH_MODE").ok().as_deref());
    report(client, actor, mode, &crate::agent_key::key_state(), false)
}

fn report<C: GenericClient>(
    client: &mut C,
    actor: &crate::access::Actor,
    mode: Result<crate::access::AuthMode>,
    key: &crate::agent_key::KeyState,
    team_client: bool,
) -> Result<DoctorReport> {
    let mut notes = Vec::new();

    let database = match client.batch_execute("SELECT 1") {
        Ok(()) => "ok".to_string(),
        Err(e) => {
            notes.push(format!("database: {e}"));
            "failed".to_string()
        }
    };

    let (generation, generation_local) = match llm::resolve_backend() {
        Ok(b) => {
            let local = llm::is_local(&b);
            (
                format!(
                    "{}{}",
                    b.label,
                    if local { " (local)" } else { " (remote)" }
                ),
                local,
            )
        }
        Err(e) => {
            notes.push(format!(
                "generation unavailable ({e}). extract/ask/titles need a model."
            ));
            ("unavailable".to_string(), false)
        }
    };

    let (embeddings, embedding_target, embedding_missing) = match embedding::resolve() {
        Ok(b) => {
            let target = embedding::resolve_target_from(
                std::env::var("THROUGHLINE_EMBED_TARGET_MODEL")
                    .ok()
                    .as_deref(),
                &b,
            );
            match embedding_lines(client, actor, &b, target) {
                Ok((t, m)) => (embedding::label(&b), t, m),
                Err(e) => {
                    notes.push(format!("embedding progress: {e}"));
                    (embedding::label(&b), None, "unavailable".to_string())
                }
            }
        }
        Err(e) => {
            notes.push(format!(
                "embeddings unavailable ({e}). semantic search falls back to lexical."
            ));
            ("unavailable".to_string(), None, "unavailable".to_string())
        }
    };

    let auth_mode = match &mode {
        Ok(mode) => crate::access::doctor_line(client, *mode),
        Err(e) => Err(anyhow::anyhow!("{e:#}")),
    }
    .unwrap_or_else(|e| {
        notes.push(failure_note("auth mode", &e, team_client));
        "unavailable".to_string()
    });
    let agent_access = match agent_access(client, mode.ok(), key) {
        Ok((line, note)) => {
            if !team_client {
                notes.extend(note.map(String::from));
            }
            line
        }
        Err(e) => {
            notes.push(failure_note("agent access", &e, team_client));
            "unavailable".to_string()
        }
    };

    Ok(DoctorReport {
        database,
        generation,
        embeddings,
        embedding_target,
        embedding_missing,
        generation_local,
        agent_access,
        auth_mode,
        notes,
    })
}

/// The note of a failed check. A team-mode client gets no error text, which
/// could repeat database detail; the log gets it without row values.
fn failure_note(what: &str, e: &anyhow::Error, team_client: bool) -> String {
    if team_client {
        eprintln!("doctor {what}: {}", crate::server::error_for_log(e));
        format!("{what}: unavailable")
    } else {
        format!("{what}: {e:#}")
    }
}

/// The `agent access:` line and, when it applies, a note. Local mode (or a
/// mode that could not be read): the state of `THROUGHLINE_AGENT_KEY`.
/// Team mode: the number of account keys that work (not revoked, account
/// enabled), and a note when `THROUGHLINE_AGENT_KEY` is set, because team
/// mode ignores it. Never the key itself.
pub fn agent_access<C: GenericClient>(
    client: &mut C,
    mode: Option<crate::access::AuthMode>,
    key: &crate::agent_key::KeyState,
) -> Result<(String, Option<&'static str>)> {
    if mode != Some(crate::access::AuthMode::Team) {
        return Ok((crate::agent_key::doctor_line(key), None));
    }
    let keys: i64 = client
        .query_one(
            "SELECT count(*) FROM access_agent_keys k \
             JOIN access_users u ON u.id = k.user_id \
             WHERE k.revoked_at IS NULL AND u.enabled",
            &[],
        )?
        .get(0);
    Ok((
        format!("team mode, {keys} account keys"),
        crate::agent_key::team_mode_note(key),
    ))
}

/// The two progress lines: the target's state (when there is a target) and
/// what the active model still lacks. A restart without `OLLAMA_EMBED_MODEL`
/// activates the code default, so the second line must make a gap visible.
pub fn embedding_lines<C: GenericClient>(
    client: &mut C,
    actor: &crate::access::Actor,
    active: &embedding::EmbeddingBackend,
    target: Result<Option<embedding::EmbeddingBackend>>,
) -> Result<(Option<String>, String)> {
    let target_line = match target {
        Ok(None) => None,
        Ok(Some(t)) => {
            let p = generate_embeddings::embed_progress(client, actor, &t)?;
            Some(if p.is_complete() {
                format!(
                    "{}: complete, set OLLAMA_EMBED_MODEL to it, remove the target and run docker compose up -d web",
                    p.model
                )
            } else {
                format!(
                    "{}: notes {}/{}, messages {}/{}",
                    p.model, p.notes_done, p.notes_total, p.messages_done, p.messages_total
                )
            })
        }
        Err(e) => Some(format!("{e:#}")),
    };
    let p = generate_embeddings::embed_progress(client, actor, active)?;
    let missing = if p.is_complete() {
        "complete".to_string()
    } else {
        format!(
            "{} notes and {} messages without a vector for {}",
            p.notes_missing(),
            p.messages_missing(),
            p.model
        )
    };
    Ok((target_line, missing))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::embedding::EmbeddingBackend;

    fn test_db() -> Option<postgres::Client> {
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
        cfg.connect(postgres::NoTls).ok()
    }

    fn backend() -> EmbeddingBackend {
        EmbeddingBackend {
            name: "test".into(),
            model: format!("test-embed-{}", uuid::Uuid::new_v4()),
            base_url: String::new(),
            column: "embedding_768",
            dim: 768,
            profile: embedding::model_profile("nomic-embed-text").unwrap(),
        }
    }

    fn seed_note<C: GenericClient>(client: &mut C, content: &str) {
        let category_type = crate::extract_memory::category_column_type(client).unwrap();
        client
            .execute(
                &format!(
                    "INSERT INTO memory_chunks (source_type, content, category, status) \
                     VALUES ('manual', $1, 'decision'::text::{category_type}, 'active')"
                ),
                &[&content],
            )
            .unwrap();
    }

    /// A vector for every active note and every message with text.
    fn fill<C: GenericClient>(client: &mut C, model: &str) {
        for (source, table, filter) in [
            (
                "memory_chunk",
                "memory_chunks",
                "COALESCE(status, 'active') <> 'forgotten' AND",
            ),
            ("message", "messages", ""),
        ] {
            client
                .execute(
                    &format!(
                        "INSERT INTO embeddings (source_type, source_id, model, embedding_768) \
                         SELECT $1, id, $2, array_fill(0.1::real, ARRAY[768])::vector \
                         FROM {table} WHERE {filter} COALESCE(content, '') <> ''"
                    ),
                    &[&source, &model],
                )
                .unwrap();
        }
    }

    /// Test value, not a secret.
    const KEY: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

    #[test]
    fn agent_access_in_team_mode_counts_account_keys_and_notes_the_ignored_key() {
        use crate::access::AuthMode;
        use crate::agent_key::{key_state_from, KeyState, IGNORED_IN_TEAM_MODE};
        let Some(mut client) = test_db() else {
            eprintln!("skipping agent_access_in_team_mode_counts_account_keys_and_notes_the_ignored_key: no PostgreSQL");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let count = |line: &str| -> i64 {
            line.strip_prefix("team mode, ")
                .and_then(|rest| rest.strip_suffix(" account keys"))
                .unwrap_or_else(|| panic!("{line}"))
                .parse()
                .unwrap_or_else(|_| panic!("{line}"))
        };
        let (line, note) = agent_access(&mut tx, Some(AuthMode::Team), &KeyState::Off).unwrap();
        assert_eq!(note, None, "the shared key is not set");
        let before = count(&line);
        let (a, b) = crate::access::team_test::two_accounts(&mut tx);
        let name = a.username.expect("name");
        let (first, _) = crate::accounts_cli::new_agent_key(&mut tx, &name, None).expect("key");
        crate::accounts_cli::new_agent_key(&mut tx, &name, None).expect("key");
        crate::accounts_cli::revoke_agent_key(&mut tx, &name, first).expect("revoke");
        // B's key stops working with B's account: it is not counted.
        let (b_id, b_name) = (b.user_id.expect("id"), b.username.expect("name"));
        crate::accounts_cli::new_agent_key(&mut tx, &b_name, None).expect("key");
        let line = |tx: &mut postgres::Transaction<'_>| {
            agent_access(tx, Some(AuthMode::Team), &KeyState::Off)
                .unwrap()
                .0
        };
        assert_eq!(count(&line(&mut tx)), before + 2, "B enabled");
        tx.execute(
            "UPDATE access_users SET enabled = false WHERE id = $1",
            &[&b_id],
        )
        .expect("disable B");
        for state in [key_state_from(Some(KEY)), KeyState::TooShort] {
            let (line, note) = agent_access(&mut tx, Some(AuthMode::Team), &state).unwrap();
            assert_eq!(
                count(&line),
                before + 1,
                "one of A's two keys is revoked, B is disabled"
            );
            assert_eq!(note, Some(IGNORED_IN_TEAM_MODE), "{state:?}");
            assert!(!line.contains(KEY) && !IGNORED_IN_TEAM_MODE.contains(KEY));
        }
        // Local mode, and a mode that could not be read: the line as before.
        for mode in [Some(AuthMode::Local), None] {
            assert_eq!(
                agent_access(&mut tx, mode, &key_state_from(Some(KEY))).unwrap(),
                ("on".to_string(), None)
            );
            assert_eq!(
                agent_access(&mut tx, mode, &KeyState::Off).unwrap(),
                ("off: THROUGHLINE_AGENT_KEY is not set".to_string(), None)
            );
        }
    }

    /// A team-mode client gets neither error text nor the note about the
    /// host's `THROUGHLINE_AGENT_KEY`; `throughline doctor` gets both.
    #[test]
    fn team_mode_client_report_has_no_error_text_and_no_key_note() {
        use crate::access::{Actor, AuthMode};
        use crate::agent_key::{key_state_from, IGNORED_IN_TEAM_MODE};
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping team_mode_client_report_has_no_error_text_and_no_key_note: no PostgreSQL"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let key = key_state_from(Some(KEY));
        // Every report in a savepoint of its own: the first failed query
        // would abort the transaction for the rest.
        let notes = |tx: &mut postgres::Transaction<'_>, team_client: bool| {
            let mut sp = tx.transaction().expect("savepoint");
            let report = if team_client {
                for_client(&mut sp, &Actor::shared_only(), AuthMode::Team, &key)
            } else {
                report(
                    &mut sp,
                    &Actor::shared_only(),
                    Ok(AuthMode::Team),
                    &key,
                    false,
                )
            };
            report.expect("report").notes
        };
        let fine = notes(&mut tx, true);
        assert!(
            !fine.iter().any(|n| n.contains("THROUGHLINE_AGENT_KEY")),
            "{fine:?}"
        );
        let cli = notes(&mut tx, false);
        assert!(cli.iter().any(|n| n == IGNORED_IN_TEAM_MODE), "{cli:?}");
        // Accounts and notes without the columns the two checks read.
        tx.batch_execute(
            "CREATE TEMP TABLE access_users (id bigint); \
             CREATE TEMP TABLE memory_chunks (id bigint)",
        )
        .expect("broken tables");
        let checks = |notes: Vec<String>| -> Vec<String> {
            notes
                .into_iter()
                .filter(|n| n.starts_with("auth mode:") || n.starts_with("agent access:"))
                .collect()
        };
        assert_eq!(
            checks(notes(&mut tx, true)),
            ["auth mode: unavailable", "agent access: unavailable"]
        );
        let cli = checks(notes(&mut tx, false));
        assert_eq!(cli.len(), 2, "{cli:?}");
        assert!(
            cli.iter().all(|n| n.contains("db error")),
            "the CLI shows the error: {cli:?}"
        );
    }

    #[test]
    fn doctor_counts_missing_vectors_of_the_active_model() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping doctor_counts_missing_vectors_of_the_active_model: no PostgreSQL");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let active = backend();
        seed_note(&mut tx, "doctor note");
        let p =
            generate_embeddings::embed_progress(&mut tx, &crate::access::Actor::local(), &active)
                .unwrap();
        let (target, missing) =
            embedding_lines(&mut tx, &crate::access::Actor::local(), &active, Ok(None)).unwrap();
        assert_eq!(target, None);
        assert_eq!(
            missing,
            format!(
                "{} notes and {} messages without a vector for {}",
                p.notes_total, p.messages_total, active.model
            )
        );
        assert!(p.notes_total >= 1);
        // A target that could not be resolved shows its error as the line.
        let (target, _) = embedding_lines(
            &mut tx,
            &crate::access::Actor::local(),
            &active,
            Err(anyhow::anyhow!("nope: unreachable")),
        )
        .unwrap();
        assert_eq!(target.as_deref(), Some("nope: unreachable"));
    }

    #[test]
    fn doctor_says_complete_when_target_is_full() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping doctor_says_complete_when_target_is_full: no PostgreSQL");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let active = backend();
        let target = backend();
        seed_note(&mut tx, "doctor note");
        let (line, _) = embedding_lines(
            &mut tx,
            &crate::access::Actor::local(),
            &active,
            Ok(Some(target.clone())),
        )
        .unwrap();
        let line = line.expect("target line");
        assert!(
            line.starts_with(&format!("{}: notes 0/", target.model)),
            "{line}"
        );
        assert!(line.contains(", messages 0/"), "{line}");
        // Fill the target completely.
        fill(&mut tx, &target.model);
        let (line, missing) = embedding_lines(
            &mut tx,
            &crate::access::Actor::local(),
            &active,
            Ok(Some(target.clone())),
        )
        .unwrap();
        assert_eq!(
            line.as_deref(),
            Some(
                format!(
                    "{}: complete, set OLLAMA_EMBED_MODEL to it, remove the target and run docker compose up -d web",
                    target.model
                )
                .as_str()
            )
        );
        // The active model has nothing, so it is still missing everything.
        assert!(missing.contains(&active.model), "{missing}");
        // Fill the active model too: nothing is missing.
        fill(&mut tx, &active.model);
        let (_, missing) =
            embedding_lines(&mut tx, &crate::access::Actor::local(), &active, Ok(None)).unwrap();
        assert_eq!(missing, "complete");
    }
}
