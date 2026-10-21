//! Generate a short title for a conversation via the generation backend.

use crate::extract_memory::{first_user_text, render_transcript};
use crate::llm;
use crate::types::NormalisedMessage;
use anyhow::Result;

pub const TITLE_PROMPT: &str = "Write a short, specific title (max 8 words) for this coding session. \
Prefer concrete nouns over vague phrases. Reply with the title only — no quotes, no punctuation at the end.";

pub fn title_messages(messages: &[NormalisedMessage]) -> Result<String> {
    let backend = llm::resolve_backend()?;
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
        assert_eq!(fallback_title(&msgs), "Implement the payment webhook handler");
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
}
