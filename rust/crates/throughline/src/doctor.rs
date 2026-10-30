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
    /// Only the text of `agent_key::doctor_line`, never the key.
    pub agent_access: String,
    pub notes: Vec<String>,
}

pub fn run<C: GenericClient>(client: &mut C) -> Result<DoctorReport> {
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
            match embedding_lines(client, &b, target) {
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

    Ok(DoctorReport {
        database,
        generation,
        embeddings,
        embedding_target,
        embedding_missing,
        generation_local,
        agent_access: crate::agent_key::doctor_line(&crate::agent_key::key_state()),
        notes,
    })
}

/// The two progress lines: the target's state (when there is a target) and
/// what the active model still lacks. A restart without `OLLAMA_EMBED_MODEL`
/// activates the code default, so the second line must make a gap visible.
pub fn embedding_lines<C: GenericClient>(
    client: &mut C,
    active: &embedding::EmbeddingBackend,
    target: Result<Option<embedding::EmbeddingBackend>>,
) -> Result<(Option<String>, String)> {
    let target_line = match target {
        Ok(None) => None,
        Ok(Some(t)) => {
            let p = generate_embeddings::embed_progress(client, &t)?;
            Some(if p.is_complete() {
                format!(
                    "{}: complete, switch OLLAMA_EMBED_MODEL and restart web",
                    p.model
                )
            } else {
                format!(
                    "{}: notes {}/{}, messages {}/{}",
                    p.model, p.notes_done, p.notes_total, p.messages_done, p.messages_total
                )
            })
        }
        Err(e) => Some(e.to_string()),
    };
    let p = generate_embeddings::embed_progress(client, active)?;
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
                "COALESCE(status, 'active') = 'active' AND",
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

    #[test]
    fn doctor_counts_missing_vectors_of_the_active_model() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping doctor_counts_missing_vectors_of_the_active_model: no PostgreSQL");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let active = backend();
        seed_note(&mut tx, "doctor note");
        let p = generate_embeddings::embed_progress(&mut tx, &active).unwrap();
        let (target, missing) = embedding_lines(&mut tx, &active, Ok(None)).unwrap();
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
        let (target, _) =
            embedding_lines(&mut tx, &active, Err(anyhow::anyhow!("nope: unreachable"))).unwrap();
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
        let (line, _) = embedding_lines(&mut tx, &active, Ok(Some(target.clone()))).unwrap();
        let line = line.expect("target line");
        assert!(
            line.starts_with(&format!("{}: notes 0/", target.model)),
            "{line}"
        );
        assert!(line.contains(", messages 0/"), "{line}");
        // Fill the target completely.
        fill(&mut tx, &target.model);
        let (line, missing) = embedding_lines(&mut tx, &active, Ok(Some(target.clone()))).unwrap();
        assert_eq!(
            line.as_deref(),
            Some(
                format!(
                    "{}: complete, switch OLLAMA_EMBED_MODEL and restart web",
                    target.model
                )
                .as_str()
            )
        );
        // The active model has nothing, so it is still missing everything.
        assert!(missing.contains(&active.model), "{missing}");
        // Fill the active model too: nothing is missing.
        fill(&mut tx, &active.model);
        let (_, missing) = embedding_lines(&mut tx, &active, Ok(None)).unwrap();
        assert_eq!(missing, "complete");
    }
}
