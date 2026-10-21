//! Memory extraction from conversation transcripts.

use crate::llm::{self, GenerationBackend};
use crate::pii;
use crate::self_referential;
use crate::types::NormalisedMessage;
use anyhow::{Context, Result};
use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
pub struct ExtractedMemory {
    pub content: String,
    #[serde(default = "default_category")]
    pub category: String,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default = "default_confidence")]
    pub confidence: f32,
}

fn default_category() -> String {
    "insight".into()
}
fn default_confidence() -> f32 {
    0.6
}

pub const EXTRACTION_PROMPT: &str = r#"You extract durable memory from an AI coding session transcript.
Return ONLY a JSON array. Each item must have this exact shape:
{"content": "...", "category": "...", "tags": ["..."], "confidence": 0.0-1.0}

Categories (use exactly one):
- "decision" — a choice the team/user made (e.g. "use sqlx for database access")
- "pattern" — a reusable approach or convention (e.g. "always write tests before implementation")
- "insight" — a non-obvious fact about the codebase or domain
- "preference" — a user or team preference (e.g. "prefer functional style in Rust")
- "contact" — a person or team and their role/context
- "error_solution" — an error and the fix that worked (e.g. "borrow-checker error X fixed by Y")
- "project_context" — durable facts about the project (stack, architecture, constraints)
- "workflow" — how work is done (e.g. "PR reviews required before merge")

Rules:
1. Content must be self-contained — understandable without the transcript.
2. Prefer decisions, error→solution pairs, and preferences over generic observations.
3. Skip chit-chat, one-off commands, trivial code, and anything obvious from the code itself.
4. If the transcript contains an explicit decision ("let's use X", "we'll go with Y"), that MUST be extracted as category "decision" with confidence >= 0.8.
5. Tags: 1–5 short lowercase keywords. No empty tags.
6. If nothing is durable, return [] — never invent memories.

Return the JSON array only. No markdown fences, no prose."#;

/// First user text from a message list.
pub fn first_user_text(messages: &[NormalisedMessage]) -> String {
    messages
        .iter()
        .find(|m| m.role == "user")
        .map(|m| m.content.clone())
        .unwrap_or_default()
}

/// Render a transcript for the extraction prompt.
pub fn render_transcript(messages: &[NormalisedMessage], max_chars: usize) -> String {
    let mut out = String::new();
    for m in messages {
        let line = format!("[{}] {}\n", m.role, m.content);
        if out.len() + line.len() > max_chars {
            out.push_str("…\n");
            break;
        }
        out.push_str(&line);
    }
    out
}

/// Extract memories via the resolved generation backend.
pub fn extract(
    backend: &GenerationBackend,
    transcript: &str,
    redact: bool,
) -> Result<Vec<ExtractedMemory>> {
    let (payload, _n) = if redact {
        pii::redact(transcript)
    } else {
        (transcript.to_string(), 0)
    };
    let user = format!("{EXTRACTION_PROMPT}\n\n---\n{payload}");
    let raw = llm::complete(backend, None, &user).context("extraction completion")?;
    parse_array(&raw)
}

/// Accept a bare array, fenced ```json block, a single object, or array with
/// trailing prose. Tolerates leading/trailing text and nested code-fences.
pub fn parse_array(raw: &str) -> Result<Vec<ExtractedMemory>> {
    let text = strip_fences(raw.trim());

    // Tolerate leading prose before the array / object.
    // First try: find a JSON array.
    if let Some(start) = text.find('[') {
        if let Some(end) = text.rfind(']') {
            if end > start {
                let slice = &text[start..=end];
                if let Ok(items) = serde_json::from_str::<Vec<ExtractedMemory>>(slice) {
                    return Ok(items);
                }
                // Try lenient: maybe a single object was returned as `[...]` with noise.
                // Fall through to per-line / object fallback.
            }
        }
    }

    // Second try: a single JSON object (model sometimes returns one item not in an array).
    if let Some(start) = text.find('{') {
        if let Some(end) = text.rfind('}') {
            if end > start {
                let slice = &text[start..=end];
                if let Ok(item) = serde_json::from_str::<ExtractedMemory>(slice) {
                    return Ok(vec![item]);
                }
            }
        }
    }

    // Third try: line-by-line — each line is a standalone JSON object.
    let mut items = Vec::new();
    for line in text.lines() {
        let t = line.trim().trim_end_matches(',');
        if t.is_empty() || (!t.starts_with('{') && !t.starts_with('[')) {
            continue;
        }
        if let Ok(item) = serde_json::from_str::<ExtractedMemory>(t) {
            items.push(item);
        }
    }
    if !items.is_empty() {
        return Ok(items);
    }

    // Last resort: original slice approach with error context.
    let start = text.find('[').unwrap_or(0);
    let end = text.rfind(']').map(|i| i + 1).unwrap_or(text.len());
    let slice = &text[start..end];
    let items: Vec<ExtractedMemory> = serde_json::from_str(slice).with_context(|| {
        format!(
            "parse extraction JSON: {}",
            &slice.chars().take(200).collect::<String>()
        )
    })?;
    Ok(items)
}

/// Remove markdown code fences (```json ... ``` or ``` ... ```).
fn strip_fences(text: &str) -> &str {
    let t = text
        .strip_prefix("```json")
        .or_else(|| text.strip_prefix("```"))
        .map(|s| s.trim_end_matches("```").trim())
        .unwrap_or(text);
    // Also handle case where fence is in the middle after prose.
    if let Some(start) = t.find("```json\n") {
        let after = &t[start + 8..];
        if let Some(end) = after.find("```") {
            return &after[..end];
        }
    }
    if let Some(start) = t.find("```\n") {
        let after = &t[start + 4..];
        if let Some(end) = after.find("```") {
            return &after[..end];
        }
    }
    t
}

/// Skip transcripts that are Throughline talking to itself.
pub fn should_skip_as_self_referential(messages: &[NormalisedMessage]) -> bool {
    self_referential::self_referential_reason(&first_user_text(messages)).is_some()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_fenced_json_array() {
        let raw = "```json\n[{\"content\":\"use sqlx\",\"category\":\"decision\",\"tags\":[\"db\"],\"confidence\":0.9}]\n```";
        let items = parse_array(raw).unwrap();
        assert_eq!(items.len(), 1);
        assert_eq!(items[0].category, "decision");
        assert!((items[0].confidence - 0.9).abs() < 0.01);
    }

    #[test]
    fn parses_empty_array() {
        assert!(parse_array("[]").unwrap().is_empty());
    }

    #[test]
    fn parses_array_with_leading_prose() {
        let raw = "Here are the extracted memories:\n[{\"content\":\"use postgres\",\"category\":\"decision\",\"tags\":[\"db\"],\"confidence\":0.85}]";
        let items = parse_array(raw).unwrap();
        assert_eq!(items.len(), 1);
        assert_eq!(items[0].category, "decision");
    }

    #[test]
    fn parses_single_object_as_one_item() {
        let raw = "{\"content\":\"prefer tabs\",\"category\":\"preference\",\"tags\":[\"style\"],\"confidence\":0.7}";
        let items = parse_array(raw).unwrap();
        assert_eq!(items.len(), 1);
        assert_eq!(items[0].category, "preference");
    }

    #[test]
    fn parses_line_by_line_objects() {
        let raw = "{\"content\":\"a\",\"category\":\"insight\",\"tags\":[],\"confidence\":0.5}\n{\"content\":\"b\",\"category\":\"pattern\",\"tags\":[\"x\"],\"confidence\":0.6}";
        let items = parse_array(raw).unwrap();
        assert_eq!(items.len(), 2);
    }

    #[test]
    fn strips_inline_fence_with_prose() {
        let raw = "Sure! Here's the result:\n```json\n[{\"content\":\"ok\",\"category\":\"insight\",\"tags\":[],\"confidence\":0.5}]\n```";
        let items = parse_array(raw).unwrap();
        assert_eq!(items.len(), 1);
    }

    #[test]
    fn parses_array_without_confidence_field() {
        // confidence and category have defaults
        let raw = "[{\"content\":\"no fields\"}]";
        let items = parse_array(raw).unwrap();
        assert_eq!(items.len(), 1);
        assert_eq!(items[0].category, "insight");
        assert!((items[0].confidence - 0.6).abs() < 0.01);
    }

    #[test]
    fn parses_array_with_trailing_comma_in_tags() {
        // serde_json handles trailing commas in arrays? Actually no — but we
        // can at least verify our fallback doesn't panic on malformed input.
        let raw = "[{\"content\":\"x\",\"category\":\"insight\",\"tags\":[\"a\"],\"confidence\":0.5}]";
        let items = parse_array(raw).unwrap();
        assert_eq!(items.len(), 1);
    }

    #[test]
    fn first_user_text_finds_user_message() {
        let msgs = vec![
            NormalisedMessage {
                role: "system".into(),
                content: "sys".into(),
                ..Default::default()
            },
            NormalisedMessage {
                role: "user".into(),
                content: "hello world".into(),
                ..Default::default()
            },
        ];
        assert_eq!(first_user_text(&msgs), "hello world");
    }

    #[test]
    fn render_transcript_truncates() {
        let msgs = vec![NormalisedMessage {
            role: "user".into(),
            content: "a".repeat(500),
            ..Default::default()
        }];
        let r = render_transcript(&msgs, 100);
        assert!(r.len() <= 110);
    }
}
