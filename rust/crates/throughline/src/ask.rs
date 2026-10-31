//! Cited Q&A over the corpus.
//!
//! Fail-soft: when generation is unavailable, returns retrieved excerpts with
//! source references so the caller still gets useful context.

use crate::access::Actor;
use crate::llm::{self, GenerationBackend};
use crate::search::{self, SearchHit};
use anyhow::Result;
use postgres::GenericClient;

#[derive(Debug, serde::Serialize)]
pub struct AskAnswer {
    pub question: String,
    pub answer: String,
    pub model: String,
    pub local: bool,
    pub cited: bool,
    pub sources: Vec<SourceRef>,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct SourceRef {
    pub conversation_id: i64,
    pub session_id: uuid::Uuid,
    pub project_name: Option<String>,
    pub snippet: String,
}

/// Build excerpt text with [n] citation labels from hits.
fn build_excerpts(hits: &[SearchHit]) -> (String, Vec<SourceRef>) {
    let mut sources = Vec::new();
    let mut excerpts = String::new();
    for (i, h) in hits.iter().enumerate() {
        sources.push(SourceRef {
            conversation_id: h.conversation_id,
            session_id: h.session_id,
            project_name: h.project_name.clone(),
            snippet: h.snippet.clone(),
        });
        excerpts.push_str(&format!(
            "[{}] ({} | {}) {}\n",
            i + 1,
            h.project_name.as_deref().unwrap_or("unknown"),
            h.role,
            h.snippet
        ));
    }
    (excerpts, sources)
}

/// Fail-soft listing when generation fails or no hits exist.
fn fail_soft_listing(sources: &[SourceRef], model_label: &str, err: Option<&str>) -> AskAnswer {
    let mut listing =
        String::from("Generation unavailable — returning retrieved excerpts only.\n\n");
    for (i, s) in sources.iter().enumerate() {
        listing.push_str(&format!(
            "[{}] ({}) {}\n\n",
            i + 1,
            s.project_name.as_deref().unwrap_or("unknown"),
            s.snippet
        ));
    }
    if let Some(e) = err {
        listing.push_str(&format!("(model error: {e})"));
    }
    AskAnswer {
        question: String::new(),
        answer: listing,
        model: format!("{model_label} (failed)"),
        local: false,
        cited: false,
        sources: sources.to_vec(),
    }
}

pub fn ask<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    question: &str,
    backend: &GenerationBackend,
    limit: i32,
) -> Result<AskAnswer> {
    llm::ensure_local_or_allowed(backend)?;
    ask_with(client, actor, question, backend, limit, |prompt| {
        llm::complete(
            backend,
            Some("You answer from local memory excerpts. Cite [n] for each claim."),
            prompt,
        )
    })
}

/// [`ask`] with the model call passed in: the prompt holds only excerpts
/// of what `actor` may see.
fn ask_with<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    question: &str,
    backend: &GenerationBackend,
    limit: i32,
    generate: impl FnOnce(&str) -> Result<String>,
) -> Result<AskAnswer> {
    // Hybrid retrieval: semantic (when embeddings exist) + lexical + memory/instructions.
    // Pure lexical `search_from` misses natural-language questions (whole-string ILIKE).
    let embed = crate::embedding::resolve().ok();
    let hits: Vec<SearchHit> = search::hybrid(client, actor, embed.as_ref(), question, limit)?;
    let (excerpts, sources) = build_excerpts(&hits);

    if sources.is_empty() {
        return Ok(AskAnswer {
            question: question.to_string(),
            answer: "No relevant records found in the corpus. Try a different query, or run ingest first.".into(),
            model: backend.label.clone(),
            local: llm::is_local(backend),
            cited: false,
            sources: vec![],
        });
    }

    let prompt = format!(
        "Answer using ONLY the excerpts. Cite sources as [n]. If the excerpts do not contain the answer, say so.\n\nQuestion: {question}\n\nExcerpts:\n{excerpts}"
    );
    match generate(&prompt) {
        Ok(answer) => {
            // Trim and clean the answer; ensure it cites at least one source.
            let clean = answer.trim().to_string();
            let has_citation = clean.contains('[') && clean.contains(']');
            Ok(AskAnswer {
                question: question.to_string(),
                answer: clean,
                model: backend.label.clone(),
                local: llm::is_local(backend),
                cited: has_citation,
                sources,
            })
        }
        Err(e) => {
            // Generation can fail (remote model, rate limit). The retrieval
            // path is still useful — return unverified sources rather than 500.
            let mut fallback = fail_soft_listing(&sources, &backend.label, Some(&e.to_string()));
            fallback.question = question.to_string();
            fallback.local = llm::is_local(backend);
            Ok(fallback)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_excerpts_labels_sources() {
        let hits = vec![
            SearchHit {
                message_id: Some(1),
                conversation_id: 10,
                session_id: uuid::Uuid::nil(),
                project_name: Some("proj".into()),
                source_tool: Some("claude".into()),
                role: "user".into(),
                snippet: "we chose postgres".into(),
                created_at: chrono::Utc::now(),
                score: 0.9,
                kind: "memory",
            },
            SearchHit {
                message_id: Some(2),
                conversation_id: 11,
                session_id: uuid::Uuid::nil(),
                project_name: None,
                source_tool: None,
                role: "memory".into(),
                snippet: "prefer sqlx".into(),
                created_at: chrono::Utc::now(),
                score: 0.8,
                kind: "memory",
            },
        ];
        let (excerpts, sources) = build_excerpts(&hits);
        assert_eq!(sources.len(), 2);
        assert!(excerpts.contains("[1]"));
        assert!(excerpts.contains("[2]"));
        assert!(excerpts.contains("we chose postgres"));
    }

    #[test]
    fn fail_soft_listing_shows_all_sources() {
        let sources = vec![
            SourceRef {
                conversation_id: 1,
                session_id: uuid::Uuid::nil(),
                project_name: Some("p".into()),
                snippet: "first".into(),
            },
            SourceRef {
                conversation_id: 2,
                session_id: uuid::Uuid::nil(),
                project_name: None,
                snippet: "second".into(),
            },
        ];
        let a = fail_soft_listing(&sources, "gpt-4", Some("rate limit"));
        assert!(!a.cited);
        assert!(a.answer.contains("[1]"));
        assert!(a.answer.contains("[2]"));
        assert!(a.answer.contains("rate limit"));
        assert_eq!(a.sources.len(), 2);
    }

    #[test]
    fn ask_refuses_a_non_local_backend_before_anything_else() {
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
            cfg.password(&pw);
        }
        let Ok(mut client) = cfg.connect(NoTls) else {
            eprintln!("skipping ask_refuses_a_non_local_backend_before_anything_else: no PostgreSQL reachable");
            return;
        };
        let backend = GenerationBackend {
            kind: llm::BackendKind::OpenAiCompatible,
            label: "compatible/m".into(),
            base_url: "https://api.example.com/v1".into(),
            model: "m".into(),
            api_key: None,
        };
        let err = ask(&mut client, &Actor::local(), "q", &backend, 5).unwrap_err();
        assert!(
            err.to_string().contains("THROUGHLINE_ALLOW_REMOTE_LLM"),
            "{err}"
        );
    }

    #[test]
    fn ask_sources_hide() {
        use crate::access::team_test::{assert_hidden, note, two_accounts};
        let Some(mut client) = crate::config::DbConfig::from_env().connect().ok() else {
            eprintln!("skipping ask_sources_hide: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let (a, b) = two_accounts(&mut tx);
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let secret = format!("secret-{tag}");
        note(
            &mut tx,
            a.user_id,
            "private",
            &format!("plan {tag} {secret}"),
            "decision",
            None,
        );
        note(
            &mut tx,
            None,
            "shared",
            &format!("plan {tag} shared"),
            "decision",
            None,
        );
        // The model is a closure that keeps the prompt it was given.
        let backend = GenerationBackend {
            kind: llm::BackendKind::Ollama,
            label: "fake".into(),
            base_url: "http://127.0.0.1:9".into(),
            model: "fake".into(),
            api_key: None,
        };
        let mut asked = |actor: &Actor| {
            let mut prompt = String::new();
            let answer = ask_with(&mut tx, actor, &tag, &backend, 12, |p| {
                prompt = p.to_string();
                Ok("from [1]".into())
            })
            .expect("ask");
            (prompt, answer)
        };
        let (prompt, _) = asked(&a);
        assert!(prompt.contains(&secret), "A's own note reaches her prompt");
        let (prompt, answer) = asked(&b);
        assert_hidden(&prompt, &secret, "the prompt");
        assert_hidden(&format!("{:?}", answer.sources), &secret, "the sources");
        assert!(prompt.contains(&format!("plan {tag} shared")), "{prompt}");
    }
}
