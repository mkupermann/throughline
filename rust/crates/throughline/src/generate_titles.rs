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
    let transcript = render_transcript(messages, 4000);
    let mut title = llm::complete(&backend, Some(TITLE_PROMPT), &transcript)?;
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
}
