//! Generate a short title for a conversation via the generation backend.

use crate::extract_memory::{first_user_text, render_transcript};
use crate::llm;
use crate::types::NormalisedMessage;
use anyhow::Result;

pub const TITLE_PROMPT: &str = "Write a short, specific title (max 8 words) for this coding session. \
Prefer concrete nouns over vague phrases. Reply with the title only — no quotes, no punctuation at the end.";

pub fn title_messages(messages: &[NormalisedMessage]) -> Result<String> {
    let backend = llm::resolve_backend()?;
    llm::ensure_local_or_allowed(&backend)?;
    title_messages_with(&backend, messages)
}

/// [`title_messages`] with the backend already resolved.
pub fn title_messages_with(
    backend: &llm::GenerationBackend,
    messages: &[NormalisedMessage],
) -> Result<String> {
    let transcript = render_transcript(messages, 4000);
    let mut title = llm::complete(backend, Some(TITLE_PROMPT), &transcript)?;
    title = clean_title(&title);
    if title.len() > 120 {
        title.truncate(120);
    }
    Ok(title)
}

/// Strip quotes, trailing periods, and whitespace from a generated title.
fn clean_title(raw: &str) -> String {
    let t = raw.trim().trim_matches('"').trim_matches('\'').trim();
    let t = t.trim_end_matches(['.', '!', '?']).trim();
    t.to_string()
}

/// Conversations without a summary, newest first. A summary an adapter set,
/// such as a document's title, is never replaced.
pub fn untitled_conversations<C: postgres::GenericClient>(
    client: &mut C,
    limit: usize,
) -> Result<Vec<i64>> {
    Ok(client
        .query(
            "SELECT id FROM conversations
             WHERE summary IS NULL OR summary = ''
             ORDER BY started_at DESC
             LIMIT $1::bigint",
            &[&(limit as i64)],
        )?
        .into_iter()
        .map(|r| r.get(0))
        .collect())
}

pub fn fallback_title(messages: &[NormalisedMessage]) -> String {
    let t = first_user_text(messages);
    let mut s: String = t.chars().take(80).collect();
    if t.chars().count() > 80 {
        s.push('…');
    }
    if s.trim().is_empty() {
        s = "Untitled session".into();
    }
    s
}

/// Title up to `limit` untitled conversations. With a backend, a failed
/// title is not saved (summary stays NULL) and counted as failed; without
/// a backend the fallback title is used. Returns `(titled, failed)`.
pub fn run_titling<C: postgres::GenericClient>(
    client: &mut C,
    limit: usize,
    backend: Option<&llm::GenerationBackend>,
) -> Result<(usize, usize)> {
    let ids = untitled_conversations(client, limit)?;
    let mut titled = 0usize;
    let mut failed = 0usize;
    for id in ids {
        let msgs = client.query(
            r#"
            SELECT role::text, COALESCE(content,'') FROM messages
            WHERE conversation_id = $1 ORDER BY created_at, id
            "#,
            &[&id],
        )?;
        let messages: Vec<NormalisedMessage> = msgs
            .into_iter()
            .map(|r| NormalisedMessage {
                role: r.get(0),
                content: r.get(1),
                ..Default::default()
            })
            .collect();
        match backend {
            Some(b) => match title_messages_with(b, &messages) {
                Ok(title) => {
                    client.execute(
                        "UPDATE conversations SET summary = $2, updated_at = now() WHERE id = $1",
                        &[&id, &title],
                    )?;
                    titled += 1;
                }
                Err(_) => {
                    failed += 1;
                }
            },
            None => {
                let title = fallback_title(&messages);
                client.execute(
                    "UPDATE conversations SET summary = $2, updated_at = now() WHERE id = $1",
                    &[&id, &title],
                )?;
                titled += 1;
            }
        }
    }
    Ok((titled, failed))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_msg(role: &str, content: &str) -> NormalisedMessage {
        NormalisedMessage {
            role: role.into(),
            content: content.into(),
            ..Default::default()
        }
    }

    #[test]
    fn clean_title_strips_quotes_and_punctuation() {
        assert_eq!(clean_title("\"Fix login bug.\""), "Fix login bug");
        assert_eq!(clean_title("Add OAuth flow!"), "Add OAuth flow");
        assert_eq!(clean_title("  'Refactor DB layer'  "), "Refactor DB layer");
    }

    #[test]
    fn fallback_title_from_first_user() {
        let msgs = vec![
            make_msg("system", "sys"),
            make_msg("user", "Implement the payment webhook handler"),
        ];
        assert_eq!(
            fallback_title(&msgs),
            "Implement the payment webhook handler"
        );
    }

    #[test]
    fn fallback_title_truncates_long_text() {
        let msgs = vec![make_msg("user", &"a".repeat(200))];
        let t = fallback_title(&msgs);
        assert!(t.chars().count() <= 81); // 80 + ellipsis
    }

    #[test]
    fn fallback_title_empty_when_no_user() {
        let msgs = vec![make_msg("system", "hello")];
        assert_eq!(fallback_title(&msgs), "Untitled session");
    }

    /// A document's adapter sets its summary (front matter, first heading,
    /// file name). Titling picks only conversations without one, so that
    /// summary stays.
    #[test]
    fn titles_keep_an_adapter_summary() {
        let Some(mut client) = crate::config::DbConfig::from_env().connect().ok() else {
            eprintln!("skipping titles_keep_an_adapter_summary: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let insert = "INSERT INTO conversations (session_id, project_path, started_at, source_tool, summary) \
                      VALUES (gen_random_uuid(), '/fictional/notes', $1, 'markdown', $2) RETURNING id";
        let late = |d: u32| {
            chrono::DateTime::parse_from_rfc3339(&format!("2999-01-{d:02}T00:00:00Z")).unwrap()
        };
        let titled: i64 = tx
            .query_one(insert, &[&late(2), &Some("Field notes")])
            .expect("titled")
            .get(0);
        let untitled: i64 = tx
            .query_one(insert, &[&late(1), &None::<&str>])
            .expect("untitled")
            .get(0);
        let picked = untitled_conversations(&mut tx, 1000).expect("pick");
        assert!(picked.contains(&untitled), "{picked:?}");
        assert!(!picked.contains(&titled), "{picked:?}");
    }

    // ---- helpers for the failure test ----

    /// Answer Ollama chat requests with a canned reply (200) or an error (500).
    fn fake_ollama_mixed(replies: Vec<Result<String, ()>>) -> String {
        use std::io::{BufRead, BufReader, Read, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind fake ollama");
        let base = format!("http://{}", listener.local_addr().unwrap());
        std::thread::spawn(move || {
            for reply in replies {
                let (mut stream, _) = listener.accept().expect("accept");
                let mut reader = BufReader::new(stream.try_clone().unwrap());
                let mut len = 0;
                loop {
                    let mut line = String::new();
                    reader.read_line(&mut line).unwrap();
                    if line == "\r\n" {
                        break;
                    }
                    if let Some(v) = line.to_lowercase().strip_prefix("content-length:") {
                        len = v.trim().parse().unwrap();
                    }
                }
                let mut body = vec![0; len];
                reader.read_exact(&mut body).unwrap();
                match reply {
                    Ok(content) => {
                        let resp =
                            serde_json::json!({ "message": { "content": content } }).to_string();
                        write!(
                            stream,
                            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\
                             Content-Length: {}\r\nConnection: close\r\n\r\n{resp}",
                            resp.len()
                        )
                        .unwrap();
                    }
                    Err(()) => {
                        write!(
                            stream,
                            "HTTP/1.1 500 Internal Server Error\r\n\
                             Content-Length: 0\r\nConnection: close\r\n\r\n"
                        )
                        .unwrap();
                    }
                }
            }
        });
        base
    }

    fn ollama_at(base_url: String) -> llm::GenerationBackend {
        llm::GenerationBackend {
            kind: llm::BackendKind::Ollama,
            label: "test".into(),
            base_url,
            model: "test".into(),
            api_key: None,
        }
    }

    fn seed_conv<C: postgres::GenericClient>(client: &mut C, label: &str, day: u32) -> i64 {
        let started = chrono::DateTime::parse_from_rfc3339(&format!("2999-01-{day:02}T00:00:00Z"))
            .unwrap()
            .with_timezone(&chrono::Utc);
        let id: i64 = client
            .query_one(
                "INSERT INTO conversations (session_id, project_path, started_at) \
                 VALUES (gen_random_uuid(), '/fictional/titles', $1) RETURNING id",
                &[&started],
            )
            .expect("seed conv")
            .get(0);
        client
            .execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) \
                 VALUES ($1, 'user', $2, $3)",
                &[&id, &format!("{label} needs a title"), &started],
            )
            .expect("seed message");
        id
    }

    /// A title failure leaves the summary NULL and the run continues with
    /// the next conversation; a second run with a working stub sets it.
    #[test]
    fn failed_title_is_not_saved_and_next_run_fixes_it() {
        let Some(mut client) = crate::config::DbConfig::from_env().connect().ok() else {
            eprintln!(
                "skipping failed_title_is_not_saved_and_next_run_fixes_it: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        // A is newer (day 2) so it comes first in untitled_conversations.
        let a = seed_conv(&mut tx, "conversation A", 2);
        let b = seed_conv(&mut tx, "conversation B", 1);

        // First run: stub fails for A (HTTP 500), succeeds for B.
        let base = fake_ollama_mixed(vec![Err(()), Ok("Title for B".into())]);
        let backend = ollama_at(base);
        let (titled, failed) = run_titling(&mut tx, 10, Some(&backend)).expect("first run");
        assert_eq!(titled, 1, "B is titled");
        assert_eq!(failed, 1, "A fails");
        let summary_a: Option<String> = tx
            .query_one("SELECT summary FROM conversations WHERE id = $1", &[&a])
            .expect("read A")
            .get(0);
        assert_eq!(summary_a, None, "A.summary stays NULL after failure");
        let summary_b: Option<String> = tx
            .query_one("SELECT summary FROM conversations WHERE id = $1", &[&b])
            .expect("read B")
            .get(0);
        assert_eq!(summary_b.as_deref(), Some("Title for B"), "B has the title");

        // Second run: working stub for the remaining conversation A.
        let base2 = fake_ollama_mixed(vec![Ok("Title for A".into())]);
        let backend2 = ollama_at(base2);
        let (titled2, failed2) = run_titling(&mut tx, 10, Some(&backend2)).expect("second run");
        assert_eq!(titled2, 1, "A is titled on retry");
        assert_eq!(failed2, 0, "no failures on retry");
        let summary_a2: Option<String> = tx
            .query_one("SELECT summary FROM conversations WHERE id = $1", &[&a])
            .expect("read A again")
            .get(0);
        assert_eq!(
            summary_a2.as_deref(),
            Some("Title for A"),
            "A has the title"
        );
    }
}
