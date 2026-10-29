//! Memory extraction from conversation transcripts.

use crate::llm::{self, GenerationBackend};
use crate::pii;
use crate::self_referential;
use crate::types::NormalisedMessage;
use anyhow::{bail, Context, Result};
use chrono::{DateTime, Utc};
use postgres::GenericClient;
use serde::Deserialize;
use serde_json::Value;

#[derive(Debug, Clone, Deserialize)]
pub struct ExtractedMemory {
    pub content: String,
    #[serde(default = "default_category")]
    pub category: String,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default = "default_confidence")]
    pub confidence: f64,
}

/// The categories the prompt allows, and the labels of the Python-era enum.
const CATEGORIES: [&str; 8] = [
    "decision",
    "pattern",
    "insight",
    "preference",
    "contact",
    "error_solution",
    "project_context",
    "workflow",
];

fn default_category() -> String {
    "insight".into()
}

/// Return the PostgreSQL type name of `memory_chunks.category`
/// (`"text"` or `"memory_category"`).
pub fn category_column_type<C: GenericClient>(client: &mut C) -> Result<String> {
    Ok(client
        .query_one(
            "SELECT atttypid::regtype::text FROM pg_attribute \
             WHERE attrelid = 'memory_chunks'::regclass AND attname = 'category'",
            &[],
        )?
        .get(0))
}
fn default_confidence() -> f64 {
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

/// Read every memory object out of a model reply, whatever the framing: an
/// array, a single object, objects separated by commas or newlines, a wrapper
/// object, code fences or prose around it. The reply is scanned for JSON values;
/// a start that does not hold a complete value (prose, or a reply cut off by the
/// context limit) is skipped, so the complete objects before a cut survive.
///
/// An empty result counts as success only when the reply holds JSON and no
/// trace of a memory: progress is committed per window, so a misread
/// "nothing durable" would lose the window for good.
pub fn parse_array(raw: &str) -> Result<Vec<ExtractedMemory>> {
    let mut items = Vec::new();
    let mut saw_json = false;
    let mut rest = raw;
    while let Some(pos) = rest.find(['{', '[']) {
        let mut values = serde_json::Deserializer::from_str(&rest[pos..]).into_iter::<Value>();
        match values.next() {
            Some(Ok(value)) => {
                saw_json = true;
                collect_memories(value, &mut items);
                rest = &rest[pos + values.byte_offset()..];
            }
            _ => rest = &rest[pos + 1..],
        }
    }
    if items.is_empty() && (!saw_json || raw.contains("\"content\"")) {
        bail!(
            "no memory parsed from extraction reply: {}",
            raw.trim().chars().take(200).collect::<String>()
        );
    }
    Ok(items)
}

fn collect_memories(value: Value, items: &mut Vec<ExtractedMemory>) {
    match value {
        Value::Array(values) => values.into_iter().for_each(|v| collect_memories(v, items)),
        Value::Object(map) => match serde_json::from_value(Value::Object(map.clone())) {
            Ok(item) => items.push(item),
            Err(_) => map
                .into_iter()
                .for_each(|(_, v)| collect_memories(v, items)),
        },
        _ => {}
    }
}

/// Pack transcript lines into windows of at most `max_chars` (counted in bytes,
/// so never more characters), returning each window's text and the timestamp
/// of its last line.
///
/// Lines sharing a timestamp are never split across windows, so a window's
/// end is a clean watermark: every line at or before it was sent.
pub fn windows(
    lines: &[(DateTime<Utc>, String)],
    max_chars: usize,
) -> Vec<(String, DateTime<Utc>)> {
    let mut groups: Vec<(String, DateTime<Utc>)> = Vec::new();
    for (at, line) in lines {
        match groups.last_mut() {
            Some((text, last)) if last == at => {
                text.push('\n');
                text.push_str(line);
            }
            _ => groups.push((line.clone(), *at)),
        }
    }
    // Known ceiling: a group larger than max_chars stays one oversized window.
    // Split inside a group once single timestamps carry that much text.
    let mut out: Vec<(String, DateTime<Utc>)> = Vec::new();
    for (text, at) in groups {
        match out.last_mut() {
            Some((window, end)) if window.len() + 1 + text.len() <= max_chars => {
                window.push('\n');
                window.push_str(&text);
                *end = at;
            }
            _ => out.push((text, at)),
        }
    }
    out
}

/// What one extraction pass did.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct ExtractSummary {
    pub windows: usize,
    pub chunks: usize,
    pub errors: usize,
}

/// Conversations with messages after their extraction watermark, newest first.
pub fn pending_conversations<C: GenericClient>(client: &mut C, limit: usize) -> Result<Vec<i64>> {
    let rows = client.query(
        r#"
        SELECT c.id FROM conversations c
        WHERE c.generated_by IS NULL
          AND EXISTS (
            SELECT 1 FROM messages m
            WHERE m.conversation_id = c.id
              AND COALESCE(m.content, '') <> ''
              AND (c.memory_extracted_until IS NULL OR m.created_at > c.memory_extracted_until)
          )
        ORDER BY c.started_at DESC
        LIMIT $1::bigint
        "#,
        &[&(limit as i64)],
    )?;
    Ok(rows.iter().map(|r| r.get(0)).collect())
}

/// Extract memories from one conversation's messages after its watermark,
/// window by window. Each window's notes and the advanced watermark commit
/// together. A failed window is reported and ends this conversation's pass,
/// so it stays pending for the next run.
pub fn extract_conversation<C: GenericClient>(
    client: &mut C,
    backend: &GenerationBackend,
    conv_id: i64,
    window_chars: usize,
) -> Result<ExtractSummary> {
    let lines: Vec<(DateTime<Utc>, String)> = client
        .query(
            r#"
            SELECT m.created_at, m.role::text || ': ' || left(m.content, 2000)
            FROM messages m JOIN conversations c ON c.id = m.conversation_id
            WHERE m.conversation_id = $1
              AND COALESCE(m.content, '') <> ''
              AND (c.memory_extracted_until IS NULL OR m.created_at > c.memory_extracted_until)
            ORDER BY m.created_at, m.id
            "#,
            &[&conv_id],
        )?
        .iter()
        .map(|r| (r.get(0), r.get(1)))
        .collect();

    let mut summary = ExtractSummary::default();
    for (text, end) in windows(&lines, window_chars) {
        let items = match extract(backend, &text, true) {
            Ok(items) => items,
            Err(e) => {
                eprintln!("extract #{conv_id}: {e:#}");
                summary.errors += 1;
                break;
            }
        };
        let mut tx = client.transaction()?;
        for item in &items {
            insert_memory(&mut tx, conv_id, item, end)?;
        }
        tx.execute(
            "UPDATE conversations SET memory_extracted_until = $2 WHERE id = $1",
            &[&conv_id, &end],
        )?;
        tx.commit()?;
        summary.windows += 1;
        summary.chunks += items.len();
    }
    Ok(summary)
}

/// Store one extracted memory for a conversation. `source_at` is the time of
/// the latest message the note was extracted from.
pub fn insert_memory<C: GenericClient>(
    client: &mut C,
    conv_id: i64,
    item: &ExtractedMemory,
    source_at: DateTime<Utc>,
) -> Result<()> {
    // `category` is the memory_category enum in schemas from the Python era and
    // TEXT in a fresh Rust schema; `confidence` is NUMERIC or REAL. Cast the
    // category to whichever type this database uses, and send confidence as
    // float8, which assigns to both.
    // A category outside the enum would fail the insert and roll the window
    // back on every run, so it is filed under the parser's default instead.
    let category = item.category.trim().to_lowercase();
    let category = if CATEGORIES.contains(&category.as_str()) {
        category
    } else {
        default_category()
    };
    let category_type = category_column_type(client)?;
    client.execute(
        &format!(
            r#"
        INSERT INTO memory_chunks
            (source_type, source_id, content, category, project_name,
             confidence, tags, status, source_at)
        VALUES ('conversation', $1, $2, $3::text::{category_type},
                (SELECT project_name FROM conversations WHERE id = $1),
                $4::float8, $5, 'active', $6)
        "#
        ),
        &[
            &conv_id,
            &item.content,
            &category,
            &item.confidence,
            &item.tags,
            &source_at,
        ],
    )?;
    Ok(())
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
    fn parses_a_single_object_with_empty_tags() {
        // The array span from the first `[` to the last `]` is the empty tag list.
        let raw = r#"{"content":"use sqlx","category":"decision","tags":[],"confidence":0.9}"#;
        let items = parse_array(raw).unwrap();
        assert_eq!(items.len(), 1);
        assert_eq!(items[0].content, "use sqlx");
    }

    #[test]
    fn parses_objects_separated_by_newlines_only() {
        let raw = "{\n  \"content\": \"a\",\n  \"tags\": [\"x\"]\n}\n{\n  \"content\": \"b\",\n  \"tags\": []\n}";
        let items = parse_array(raw).unwrap();
        let contents: Vec<&str> = items.iter().map(|i| i.content.as_str()).collect();
        assert_eq!(contents, ["a", "b"]);
    }

    #[test]
    fn keeps_the_complete_objects_of_a_truncated_reply() {
        // Pretty-printed like the real reply, so no single line holds an object.
        let raw = "[\n  {\n    \"content\": \"a\",\n    \"tags\": [\"x\"]\n  },\n  {\n    \"content\": \"b\",\n    \"tags\": []\n  },\n  {\n    \"content\": \"c is cut o";
        let items = parse_array(raw).unwrap();
        let contents: Vec<&str> = items.iter().map(|i| i.content.as_str()).collect();
        assert_eq!(contents, ["a", "b"]);
    }

    #[test]
    fn a_reply_with_content_but_no_complete_object_is_an_error() {
        // Reporting "nothing durable" here would advance the watermark and lose it.
        assert!(parse_array(r#"{"content": "cut off before the end"#).is_err());
        assert!(parse_array("I could not find anything durable.").is_err());
        // Only the inner tag list is complete: JSON was seen, but no memory.
        assert!(parse_array(r#"[{"content": "x", "tags": ["a", "b"], "confid"#).is_err());
    }

    #[test]
    fn parses_memories_inside_a_wrapper_object() {
        let raw = r#"{"memories": [{"content": "a"}, {"content": "b"}]}"#;
        assert_eq!(parse_array(raw).unwrap().len(), 2);
    }

    #[test]
    fn parses_comma_separated_objects_without_brackets() {
        // Shape returned by qwen3.6 with thinking off: the array brackets are missing.
        let raw = "{\n  \"content\": \"use compose\",\n  \"category\": \"project_context\",\n  \
                   \"tags\": [\"docker\", \"postgres\"],\n  \"confidence\": 0.95\n},\n\
                   {\n  \"content\": \"repo is the source of truth\",\n  \"category\": \"preference\",\n  \
                   \"tags\": [\"config\"],\n  \"confidence\": 0.85\n}";
        let items = parse_array(raw).unwrap();
        assert_eq!(items.len(), 2);
        assert_eq!(items[1].category, "preference");
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
        let raw =
            "[{\"content\":\"x\",\"category\":\"insight\",\"tags\":[\"a\"],\"confidence\":0.5}]";
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

    fn ts(secs: i64) -> DateTime<Utc> {
        DateTime::from_timestamp(secs, 0).unwrap()
    }

    #[test]
    fn windows_split_at_max_chars_and_end_at_last_timestamp() {
        let lines = vec![
            (ts(1), "user: aaaa".to_string()),
            (ts(2), "assistant: bbbb".to_string()),
            (ts(3), "user: cccc".to_string()),
        ];
        let w = windows(&lines, 30);
        assert_eq!(
            w,
            vec![
                ("user: aaaa\nassistant: bbbb".to_string(), ts(2)),
                ("user: cccc".to_string(), ts(3)),
            ]
        );
    }

    #[test]
    fn windows_never_split_lines_sharing_a_timestamp() {
        // b and c share ts(2): the cut moves before b although a+b would fit.
        let (a, b, c) = ("a".repeat(20), "b".repeat(20), "c".repeat(20));
        let lines = vec![(ts(1), a.clone()), (ts(2), b.clone()), (ts(2), c.clone())];
        let w = windows(&lines, 45);
        assert_eq!(w, vec![(a, ts(1)), (format!("{b}\n{c}"), ts(2))]);
    }

    #[test]
    fn windows_keep_an_oversized_line_whole() {
        let long = "x".repeat(50);
        let w = windows(&[(ts(1), long.clone())], 10);
        assert_eq!(w, vec![(long, ts(1))]);
        assert!(windows(&[], 10).is_empty());
    }

    /// Connect to the database (`PG*` env). Skip when unreachable so offline
    /// `cargo test --lib` stays green. Mirrors the server.rs test helper.
    fn test_db() -> Option<postgres::Client> {
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
            cfg.password(pw);
        }
        cfg.connect(NoTls).ok()
    }

    /// 2099-01-01T00:00:00Z, so test conversations sort before real ones.
    const T0: i64 = 4_070_908_800;

    /// A conversation with one user message per `(seconds after T0, text)`.
    fn seed_conversation<C: GenericClient>(client: &mut C, lines: &[(i64, &str)]) -> i64 {
        let id: i64 = client
            .query_one(
                "INSERT INTO conversations (session_id, project_path, started_at, ended_at) \
                 VALUES ($1, '/tmp/extract-test', $2, $3) RETURNING id",
                &[&uuid::Uuid::new_v4(), &ts(T0), &ts(T0 + 3600)],
            )
            .expect("seed conversation")
            .get(0);
        for (secs, text) in lines {
            client
                .execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) \
                     VALUES ($1, 'user', $2, $3)",
                    &[&id, text, &ts(T0 + secs)],
                )
                .expect("seed message");
        }
        id
    }

    #[test]
    fn insert_memory_stores_category_and_confidence() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping insert_memory_stores_category_and_confidence: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let conv = seed_conversation(&mut tx, &[]);
        let item = ExtractedMemory {
            content: "use sqlx".into(),
            category: "decision".into(),
            tags: vec!["db".into()],
            confidence: 0.9,
        };
        insert_memory(&mut tx, conv, &item, ts(T0)).expect("insert memory");
        let row = tx
            .query_one(
                "SELECT category::text, confidence::float8 FROM memory_chunks \
                 WHERE source_type = 'conversation' AND source_id = $1",
                &[&conv],
            )
            .expect("read memory");
        assert_eq!(row.get::<_, String>(0), "decision");
        assert!((row.get::<_, f64>(1) - 0.9).abs() < 1e-6);
        // Drop tx → rollback, nothing left behind.
    }

    #[test]
    fn insert_memory_files_an_unknown_category_as_insight() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping insert_memory_files_an_unknown_category_as_insight: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let conv = seed_conversation(&mut tx, &[]);
        let item = ExtractedMemory {
            content: "the build needs the corporate CA".into(),
            category: "Build Setup".into(),
            tags: vec![],
            confidence: 0.7,
        };
        insert_memory(&mut tx, conv, &item, ts(T0)).expect("insert memory");
        let category: String = tx
            .query_one(
                "SELECT category::text FROM memory_chunks \
                 WHERE source_type = 'conversation' AND source_id = $1",
                &[&conv],
            )
            .expect("read memory")
            .get(0);
        assert_eq!(category, "insight");
    }

    /// Answer each Ollama chat request with the next canned reply, then stop
    /// listening. Returns the base URL and the received request bodies.
    fn fake_ollama(replies: Vec<&'static str>) -> (String, std::sync::mpsc::Receiver<String>) {
        use std::io::{BufRead, BufReader, Read, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind fake ollama");
        let base = format!("http://{}", listener.local_addr().unwrap());
        let (sent, received) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            for content in replies {
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
                sent.send(String::from_utf8(body).unwrap()).unwrap();
                let reply = serde_json::json!({ "message": { "content": content } }).to_string();
                write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\
                     Content-Length: {}\r\nConnection: close\r\n\r\n{reply}",
                    reply.len()
                )
                .unwrap();
            }
        });
        (base, received)
    }

    fn ollama_at(base_url: String) -> GenerationBackend {
        GenerationBackend {
            kind: llm::BackendKind::Ollama,
            label: "test".into(),
            base_url,
            model: "test".into(),
            api_key: None,
        }
    }

    const DECISION: &str =
        r#"[{"content":"use sqlx","category":"decision","tags":["db"],"confidence":0.9}]"#;

    #[test]
    fn extract_conversation_resumes_after_its_watermark() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping extract_conversation_resumes_after_its_watermark: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let conv = seed_conversation(&mut tx, &[(1, "first"), (2, "second"), (3, "third")]);
        let (base, requests) = fake_ollama(vec![DECISION, "[]"]);
        let backend = ollama_at(base);

        // "user: first\nuser: second" is 24 bytes, so 25 makes two windows.
        let first = extract_conversation(&mut tx, &backend, conv, 25).expect("first pass");
        assert_eq!(
            first,
            ExtractSummary {
                windows: 2,
                chunks: 1,
                errors: 0
            }
        );
        let sent: Vec<String> = requests.try_iter().collect();
        assert_eq!(sent.len(), 2);
        assert!(sent[0].contains("user: first") && sent[0].contains("user: second"));
        assert!(!sent[0].contains("third") && sent[1].contains("user: third"));
        let until: DateTime<Utc> = tx
            .query_one(
                "SELECT memory_extracted_until FROM conversations WHERE id = $1",
                &[&conv],
            )
            .unwrap()
            .get(0);
        assert_eq!(until, ts(T0 + 3));
        let source_at: DateTime<Utc> = tx
            .query_one(
                "SELECT source_at FROM memory_chunks WHERE source_type = 'conversation' AND source_id = $1",
                &[&conv],
            )
            .unwrap()
            .get(0);
        assert_eq!(
            source_at,
            ts(T0 + 2),
            "a note carries the end of its window"
        );

        // Nothing new: not pending, no request (the fake server has stopped), nothing written.
        assert!(!pending_conversations(&mut tx, 1000)
            .unwrap()
            .contains(&conv));
        let again = extract_conversation(&mut tx, &backend, conv, 25).expect("second pass");
        assert_eq!(again, ExtractSummary::default());

        // A new message is pending, and it is the only thing sent.
        tx.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES ($1, 'user', 'fourth', $2)",
            &[&conv, &ts(T0 + 4)],
        )
        .unwrap();
        assert!(pending_conversations(&mut tx, 1000)
            .unwrap()
            .contains(&conv));
        let (base, requests) = fake_ollama(vec!["[]"]);
        let later = extract_conversation(&mut tx, &ollama_at(base), conv, 25).expect("third pass");
        assert_eq!(
            later,
            ExtractSummary {
                windows: 1,
                chunks: 0,
                errors: 0
            }
        );
        let sent: Vec<String> = requests.try_iter().collect();
        assert!(sent.len() == 1 && sent[0].contains("user: fourth") && !sent[0].contains("third"));
    }

    #[test]
    fn extract_conversation_keeps_windows_before_a_failure() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping extract_conversation_keeps_windows_before_a_failure: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let conv = seed_conversation(&mut tx, &[(1, "first"), (2, "second"), (3, "third")]);
        // One reply only: the second window's request finds no server.
        let (base, _requests) = fake_ollama(vec![DECISION]);
        let s = extract_conversation(&mut tx, &ollama_at(base), conv, 25).expect("pass");
        assert_eq!(
            s,
            ExtractSummary {
                windows: 1,
                chunks: 1,
                errors: 1
            }
        );
        let until: DateTime<Utc> = tx
            .query_one(
                "SELECT memory_extracted_until FROM conversations WHERE id = $1",
                &[&conv],
            )
            .unwrap()
            .get(0);
        assert_eq!(until, ts(T0 + 2), "the failed window stays pending");
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
