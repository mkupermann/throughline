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

/// The report as `actor` may see it: the embedding progress counts only the
/// notes `actor` sees (the CLI passes [`crate::access::Actor::local`]).
pub fn run<C: GenericClient>(client: &mut C, actor: &crate::access::Actor) -> Result<DoctorReport> {
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

    let mode =
        crate::access::auth_mode_from(std::env::var("THROUGHLINE_AUTH_MODE").ok().as_deref());
    let auth_mode = match &mode {
        Ok(mode) => crate::access::doctor_line(client, *mode),
        Err(e) => Err(anyhow::anyhow!("{e:#}")),
    }
    .unwrap_or_else(|e| {
        notes.push(format!("auth mode: {e:#}"));
        "unavailable".to_string()
    });
    let agent_access = match agent_access(client, mode.ok(), &crate::agent_key::key_state()) {
        Ok((line, note)) => {
            notes.extend(note.map(String::from));
            line
        }
        Err(e) => {
            notes.push(format!("agent access: {e:#}"));
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

/// The `agent access:` line and, when it applies, a note. Local mode (or a
/// mode that could not be read): the state of `THROUGHLINE_AGENT_KEY`.
/// Team mode: the number of account keys not revoked, and a note when
/// `THROUGHLINE_AGENT_KEY` is set, because team mode ignores it. Never the
/// key itself.
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
            "SELECT count(*) FROM access_agent_keys WHERE revoked_at IS NULL",
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
        let (a, _) = crate::access::team_test::two_accounts(&mut tx);
        let name = a.username.expect("name");
        let (first, _) = crate::accounts_cli::new_agent_key(&mut tx, &name, None).expect("key");
        crate::accounts_cli::new_agent_key(&mut tx, &name, None).expect("key");
        crate::accounts_cli::revoke_agent_key(&mut tx, &name, first).expect("revoke");
        for state in [key_state_from(Some(KEY)), KeyState::TooShort] {
            let (line, note) = agent_access(&mut tx, Some(AuthMode::Team), &state).unwrap();
            assert_eq!(count(&line), before + 1, "one of two keys is revoked");
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
