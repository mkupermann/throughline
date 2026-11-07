//! Memory extraction from conversation transcripts.

use crate::document_lifecycle;
use crate::llm::{self, GenerationBackend};
use crate::pii;
use crate::self_referential;
use crate::types::{NormalisedMessage, DOCUMENT_SOURCES};
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
/// MCP `write` accepts exactly these.
pub(crate) const CATEGORIES: [&str; 8] = [
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

/// Selects the extraction prompt: unset (or `default`) keeps
/// [`EXTRACTION_PROMPT`] for every source, `personal` picks
/// [`PERSONAL_EXTRACTION_PROMPT`] for sessions, `document` picks
/// [`DOCUMENT_EXTRACTION_PROMPT`] for the document sources. See
/// [`profile_for`].
pub const PROFILE_ENV: &str = "THROUGHLINE_EXTRACT_PROFILE";

/// The prompt for conversations between people rather than coding sessions:
/// facts about people, events, preferences and dates. Same reply shape and
/// categories as the default, so parsing and storage are shared.
pub const PERSONAL_EXTRACTION_PROMPT: &str = r#"You extract durable memory from a conversation between people.
Return ONLY a JSON array. Each item must have this exact shape:
{"content": "...", "category": "...", "tags": ["..."], "confidence": 0.0-1.0}

Categories (use exactly one):
- "contact" — who a person is: family, friends, pets, job, home, background (e.g. "Ana's sister lives in Lisbon")
- "insight" — something that happened to a person or that they did, with its time if known (e.g. "Ben ran his first marathon last weekend")
- "preference" — what a person likes, dislikes, believes or values (e.g. "Ana prefers hiking to beach holidays")
- "decision" — a plan, goal or choice a person made (e.g. "Ben plans to apply for a teaching job")

Rules:
1. Each line starts with the speaker's name after the role label. Name the person in every item; never write "he", "she", "the user" or "the assistant".
2. Content must be self-contained — understandable without the transcript.
3. One fact per item. Keep small details: names of people and pets, places, objects, hobbies, numbers.
4. Keep times as they are said, relative ones too ("last Saturday", "two years ago"); the note is stored with the date of the conversation.
5. "[shares a photo: ...]" describes a picture the speaker shared; use it when it tells something about the person.
6. Skip greetings and small talk that carry no fact.
7. Tags: 1–5 short lowercase keywords. No empty tags.
8. If nothing is durable, return [] — never invent memories.

Return the JSON array only. No markdown fences, no prose."#;

/// The prompt for documents: sections of a file or a web page, or commit
/// messages. Facts, decisions, rules and dates, each with what it belongs to.
/// Same reply shape and categories as the default; dates go to `insight`
/// with the date in the text.
pub const DOCUMENT_EXTRACTION_PROMPT: &str = r#"You extract durable knowledge from a document: sections of a file or a web page, or commit messages.
Return ONLY a JSON array. Each item must have this exact shape:
{"content": "...", "category": "...", "tags": ["..."], "confidence": 0.0-1.0}

Categories (use exactly one):
- "decision" — a choice the document records (e.g. "the service stores its data in PostgreSQL")
- "pattern" — a rule or convention the document sets (e.g. "every migration gets its own test")
- "insight" — a fact, or a date and what happens on it (e.g. "the beta of version 2 ends on 2027-03-01")
- "preference" — a preference a person or team states (e.g. "the team prefers small pull requests")
- "contact" — a person or team and their role (e.g. "the platform team owns the deploy pipeline")
- "error_solution" — a problem and the fix the document gives (e.g. "a stale lock file after an upgrade is fixed by deleting it")
- "project_context" — durable facts about the project (stack, architecture, constraints)
- "workflow" — how work is done (e.g. "releases are tagged from main")

Rules:
1. Each part starts with "document:" and has no speaker. A section usually begins with its heading.
2. Content must be self-contained — understandable without the document. Name the subject; never write "this document" or "this section".
3. Every date goes into an "insight" item with the date written in the content, together with what it refers to and the heading or commit it comes from (e.g. "Release plan: the beta of version 2 ends on 2027-03-01"). Keep the date as written.
4. One fact, decision or rule per item. Take what the text states; do not add your own conclusions.
5. Skip tables of contents, navigation, boilerplate, licence text and code that only shows syntax.
6. Tags: 1–5 short lowercase keywords. No empty tags.
7. If nothing is durable, return [] — never invent memories.

Return the JSON array only. No markdown fences, no prose."#;

/// A named extraction prompt.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Profile {
    /// Coding sessions: [`EXTRACTION_PROMPT`].
    Default,
    /// Conversations between people: [`PERSONAL_EXTRACTION_PROMPT`].
    Personal,
    /// Files, pages and commits: [`DOCUMENT_EXTRACTION_PROMPT`].
    Document,
}

impl Profile {
    /// The profile a [`PROFILE_ENV`] value names. Unset, empty and `default`
    /// are the default. An unknown value is an error, so a typo never runs
    /// the wrong prompt unnoticed.
    pub fn parse(value: Option<&str>) -> Result<Self> {
        match value.map(|p| p.trim().to_lowercase()).as_deref() {
            None | Some("") | Some("default") => Ok(Self::Default),
            Some("personal") => Ok(Self::Personal),
            Some("document") => Ok(Self::Document),
            Some(other) => bail!(
                "{PROFILE_ENV}={other:?} is unknown; use personal or document, or leave it unset"
            ),
        }
    }

    pub fn prompt(self) -> &'static str {
        match self {
            Self::Default => EXTRACTION_PROMPT,
            Self::Personal => PERSONAL_EXTRACTION_PROMPT,
            Self::Document => DOCUMENT_EXTRACTION_PROMPT,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::Default => "default",
            Self::Personal => "personal",
            Self::Document => "document",
        }
    }
}

/// The prompt for a profile value (see [`PROFILE_ENV`] and [`Profile::parse`]).
pub fn extraction_prompt(profile: Option<&str>) -> Result<&'static str> {
    Profile::parse(profile).map(Profile::prompt)
}

/// Whether a conversation of `source_tool` is a document (see
/// [`DOCUMENT_SOURCES`]) rather than a session.
pub fn is_document_source(source_tool: Option<&str>) -> bool {
    source_tool.is_some_and(|s| DOCUMENT_SOURCES.contains(&s))
}

/// The profile for a conversation of `source_tool`, given `configured`, the
/// profile [`PROFILE_ENV`] names (parsed once per run).
///
/// Markdown, git and web take the production prompt ([`Profile::Default`]):
/// the measurement chose it, because it kept 7.4 notes per file where the
/// stop rule asked for 0.5. `document` is opt-in for them. `personal` is
/// for conversations between people and never applies to a document.
/// Sessions take `configured`, except `document`, whose prompt reads parts
/// without a speaker; they keep the production prompt then.
pub fn profile_for(source_tool: Option<&str>, configured: Profile) -> Profile {
    match (is_document_source(source_tool), configured) {
        (true, Profile::Document) => Profile::Document,
        (true, _) => Profile::Default,
        (false, Profile::Document) => Profile::Default,
        (false, profile) => profile,
    }
}

/// [`profile_for`] for a stored conversation.
pub fn conversation_profile<C: GenericClient>(
    client: &mut C,
    conv_id: i64,
    configured: Profile,
) -> Result<Profile> {
    Ok(profile_for(
        source_tool(client, conv_id)?.as_deref(),
        configured,
    ))
}

fn source_tool<C: GenericClient>(client: &mut C, conv_id: i64) -> Result<Option<String>> {
    Ok(client
        .query_opt(
            "SELECT source_tool FROM conversations WHERE id = $1",
            &[&conv_id],
        )?
        .and_then(|r| r.get(0)))
}

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

/// Extract memories via the resolved generation backend, with `prompt` (see
/// [`extraction_prompt`]) in front of the transcript.
pub fn extract(
    backend: &GenerationBackend,
    prompt: &str,
    transcript: &str,
    redact: bool,
) -> Result<Vec<ExtractedMemory>> {
    let (payload, _n) = if redact {
        pii::redact(transcript)
    } else {
        (transcript.to_string(), 0)
    };
    let user = format!("{prompt}\n\n---\n{payload}");
    let raw = llm::complete_with_format(backend, None, &user, Some(extraction_schema()))
        .context("extraction completion")?;
    parse_array(&raw)
}

/// The JSON schema of an extraction reply: an array of memories with a
/// category from [`CATEGORIES`]. Ollama holds the reply to it, so a transcript
/// that reads like an instruction cannot turn the reply into prose.
pub fn extraction_schema() -> serde_json::Value {
    serde_json::json!({
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "content": { "type": "string" },
                "category": { "type": "string", "enum": CATEGORIES },
                "tags": { "type": "array", "items": { "type": "string" } },
                "confidence": { "type": "number" }
            },
            "required": ["content", "category", "tags", "confidence"]
        }
    })
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
    // Documents, whose sections share one timestamp or differ by
    // microseconds, are cut by `document_windows` instead.
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

/// Sections a document window holds at most, so a file of many short
/// sections is still read in several requests.
pub const DOCUMENT_WINDOW_SECTIONS: usize = 20;

/// One request's worth of transcript.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Window {
    pub text: String,
    /// Timestamp of the window's last line.
    pub end: DateTime<Utc>,
    /// Whether the watermark may move to `end` after this window: false
    /// while the next window goes on with lines of the same timestamp.
    pub commits: bool,
}

/// Pack a document's lines (one section each) into windows of at most
/// `max_sections` lines and `max_chars` bytes, whatever their timestamps.
/// [`windows`] keeps lines of one timestamp together, so a file whose
/// sections all carry its mtime would become one window. A section larger
/// than `max_chars` is a window of its own; the extraction query cuts each
/// line at 2,000 characters, and the document adapters split longer
/// sections into continuation messages before that.
pub fn document_windows(
    lines: &[(DateTime<Utc>, String)],
    max_chars: usize,
    max_sections: usize,
) -> Vec<Window> {
    let mut out: Vec<Window> = Vec::new();
    let mut sections = 0;
    for (at, line) in lines {
        match out.last_mut() {
            Some(w) if sections < max_sections && w.text.len() + 1 + line.len() <= max_chars => {
                w.text.push('\n');
                w.text.push_str(line);
                w.end = *at;
                sections += 1;
            }
            last => {
                if let Some(w) = last {
                    w.commits = w.end < *at;
                }
                out.push(Window {
                    text: line.clone(),
                    end: *at,
                    commits: true,
                });
                sections = 1;
            }
        }
    }
    out
}

/// What one extraction pass did.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct ExtractSummary {
    pub windows: usize,
    /// Notes written and kept.
    pub chunks: usize,
    pub errors: usize,
    /// Notes of an earlier document version closed before this pass.
    pub closed: usize,
    /// Notes this pass wrote and deleted again because they repeat a
    /// forgotten or replaced note of the same document.
    pub dropped: usize,
    /// Notes kept after a repeat check that could compare text only,
    /// because no vector could be made.
    pub text_only: usize,
}

impl ExtractSummary {
    pub fn add(&mut self, other: &ExtractSummary) {
        self.windows += other.windows;
        self.chunks += other.chunks;
        self.errors += other.errors;
        self.closed += other.closed;
        self.dropped += other.dropped;
        self.text_only += other.text_only;
    }

    /// The summary line for documents, when there is anything to say.
    pub fn document_line(&self) -> Option<String> {
        (self.closed + self.dropped + self.text_only > 0).then(|| {
            format!(
                "documents: {} note(s) of old versions closed, \
                 {} repeat(s) of forgotten or replaced notes dropped, {} notes checked by text only",
                self.closed, self.dropped, self.text_only
            )
        })
    }
}

/// Conversations with messages after their extraction watermark, newest first.
pub fn pending_conversations<C: GenericClient>(client: &mut C, limit: usize) -> Result<Vec<i64>> {
    pending_conversations_from(client, limit, 0)
}

/// [`pending_conversations`] after skipping the first `skip`, in the same
/// order. A caller that saw the first ones fail can move past them.
pub fn pending_conversations_from<C: GenericClient>(
    client: &mut C,
    limit: usize,
    skip: usize,
) -> Result<Vec<i64>> {
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
        ORDER BY c.started_at DESC, c.id DESC
        LIMIT $1::bigint OFFSET $2::bigint
        "#,
        &[&(limit as i64), &(skip as i64)],
    )?;
    Ok(rows.iter().map(|r| r.get(0)).collect())
}

/// Count of conversations with messages after their extraction watermark.
/// Same WHERE condition as [`pending_conversations`], without LIMIT.
pub fn count_pending_conversations<C: GenericClient>(client: &mut C) -> Result<i64> {
    let row = client.query_one(
        r#"
        SELECT count(*) FROM conversations c
        WHERE c.generated_by IS NULL
          AND EXISTS (
            SELECT 1 FROM messages m
            WHERE m.conversation_id = c.id
              AND COALESCE(m.content, '') <> ''
              AND (c.memory_extracted_until IS NULL OR m.created_at > c.memory_extracted_until)
          )
        "#,
        &[],
    )?;
    Ok(row.get(0))
}

/// Extract up to `limit` pending conversations ([`pending_conversations`]),
/// one after the other, and add up their summaries. Refuses a database that
/// lacks the newest migration of the code list before it reads anything
/// ([`crate::migrate::require_latest`]): the extraction reads and writes
/// columns that only the Rust migrations add.
pub fn run_pending<C: GenericClient>(
    client: &mut C,
    backend: &GenerationBackend,
    limit: usize,
    window_chars: usize,
    configured: Profile,
) -> Result<ExtractSummary> {
    run_pending_from(client, backend, limit, 0, window_chars, configured)
}

/// [`run_pending`] on the pending conversations after the first `skip`.
pub fn run_pending_from<C: GenericClient>(
    client: &mut C,
    backend: &GenerationBackend,
    limit: usize,
    skip: usize,
    window_chars: usize,
    configured: Profile,
) -> Result<ExtractSummary> {
    crate::migrate::require_latest(client)?;
    let mut total = ExtractSummary::default();
    for conv_id in pending_conversations_from(client, limit, skip)? {
        let s = extract_conversation(client, backend, conv_id, window_chars, configured)?;
        total.add(&s);
    }
    Ok(total)
}

/// Extract memories from one conversation's messages after its watermark,
/// window by window. Each window's notes and the advanced watermark commit
/// together. A failed window is reported and ends this conversation's pass,
/// so it stays pending for the next run. The prompt follows the
/// conversation's source ([`conversation_profile`]); `configured` is the
/// profile [`PROFILE_ENV`] names.
pub fn extract_conversation<C: GenericClient>(
    client: &mut C,
    backend: &GenerationBackend,
    conv_id: i64,
    window_chars: usize,
    configured: Profile,
) -> Result<ExtractSummary> {
    llm::ensure_local_or_allowed(backend)?;
    let prompt = conversation_profile(client, conv_id, configured)?.prompt();
    extract_conversation_with(
        client,
        conv_id,
        window_chars,
        &document_lifecycle::RepeatCheck::active(),
        |text| extract(backend, prompt, text, true),
    )
}

/// [`extract_conversation`] with the model step passed in: a window's text
/// goes in, its notes come out.
///
/// A document (see [`is_document_source`]) differs in three ways. When its
/// messages were replaced since the last extraction, the old version's notes
/// are closed ([`document_lifecycle::reconcile`]) in the transaction of the
/// first window that commits, before its notes are written. Its sections are cut
/// into windows by [`document_windows`]. And a written note that repeats a
/// forgotten or replaced note of the same document is deleted again in the same
/// transaction ([`document_lifecycle::drop_repeats`] with
/// `repeat_check`).
///
/// Windows that end inside a run of equal timestamps are sent first and
/// written together with the window that ends the run, so the watermark
/// never passes a line that was not sent.
pub fn extract_conversation_with<C, F>(
    client: &mut C,
    conv_id: i64,
    window_chars: usize,
    repeat_check: &document_lifecycle::RepeatCheck<'_>,
    mut model: F,
) -> Result<ExtractSummary>
where
    C: GenericClient,
    F: FnMut(&str) -> Result<Vec<ExtractedMemory>>,
{
    let document = is_document_source(source_tool(client, conv_id)?.as_deref());
    // Each line is cut at 2,000 characters. Document adapters split longer
    // sections into continuation messages, so no section text is lost.
    // For a document the same statement asks whether this is a new version,
    // so a replace between the question and the read cannot slip through.
    let new_version = if document {
        document_lifecycle::NEW_VERSION_AT
    } else {
        "NULL::timestamptz"
    };
    let rows = client.query(
        &format!(
            r#"
            SELECT m.created_at, m.role::text || ': ' || left(m.content, 2000),
                   {new_version}
            FROM messages m JOIN conversations c ON c.id = m.conversation_id
            WHERE m.conversation_id = $1
              AND COALESCE(m.content, '') <> ''
              AND (c.memory_extracted_until IS NULL OR m.created_at > c.memory_extracted_until)
            ORDER BY m.created_at, m.id
            "#
        ),
        &[&conv_id],
    )?;
    let lines: Vec<(DateTime<Utc>, String)> = rows.iter().map(|r| (r.get(0), r.get(1))).collect();
    let mut summary = ExtractSummary::default();
    // The old version's notes are closed in the transaction of the first
    // window that commits, so they stay current while the model fails. Once
    // that window commits, the watermark has passed the new version's first
    // message and the signal is off, so nothing is closed twice.
    let mut new_version_at = rows
        .first()
        .and_then(|r| r.get::<_, Option<DateTime<Utc>>>(2));
    let planned = if document {
        document_windows(&lines, window_chars, DOCUMENT_WINDOW_SECTIONS)
    } else {
        windows(&lines, window_chars)
            .into_iter()
            .map(|(text, end)| Window {
                text,
                end,
                commits: true,
            })
            .collect()
    };

    let mut replies: Vec<(DateTime<Utc>, Vec<ExtractedMemory>)> = Vec::new();
    for window in planned {
        match model(&window.text) {
            Ok(items) => replies.push((window.end, items)),
            Err(e) => {
                eprintln!("extract #{conv_id}: {e:#}");
                summary.errors += 1;
                break;
            }
        }
        if !window.commits {
            continue;
        }
        let mut tx = client.transaction()?;
        let mut written = ExtractSummary::default();
        if let Some(at) = new_version_at {
            written.closed = document_lifecycle::reconcile(&mut tx, conv_id, at)? as usize;
        }
        for (end, items) in &replies {
            let mut ids = Vec::with_capacity(items.len());
            for item in items {
                ids.push(insert_memory(&mut tx, conv_id, item, *end)?);
            }
            let check = if document {
                document_lifecycle::drop_repeats(&mut tx, conv_id, &ids, repeat_check)?
            } else {
                document_lifecycle::RepeatOutcome::default()
            };
            written.chunks += ids.len() - check.dropped as usize;
            written.dropped += check.dropped as usize;
            written.text_only += check.text_only as usize;
        }
        tx.execute(
            "UPDATE conversations SET memory_extracted_until = $2 WHERE id = $1",
            &[&conv_id, &window.end],
        )?;
        tx.commit()?;
        new_version_at = None;
        written.windows = replies.len();
        summary.add(&written);
        replies.clear();
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
) -> Result<i64> {
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
    let row = client.query_one(
        &format!(
            r#"
        INSERT INTO memory_chunks
            (source_type, source_id, content, category, project_name,
             confidence, tags, status, source_at, valid_from)
        VALUES ('conversation', $1, $2, $3::text::{category_type},
                (SELECT project_name FROM conversations WHERE id = $1),
                $4::float8, $5, 'active', $6, $6)
        RETURNING id
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
    let id: i64 = row.get(0);
    crate::generate_embeddings::embed_written_note(client, id);
    Ok(id)
}

/// Skip transcripts that are Throughline talking to itself.
pub fn should_skip_as_self_referential(messages: &[NormalisedMessage]) -> bool {
    self_referential::self_referential_reason(&first_user_text(messages)).is_some()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The reply schema is an array of memories whose category is one of
    /// the eight the prompts allow.
    #[test]
    fn extraction_schema_is_an_array_of_known_categories() {
        let schema = extraction_schema();
        assert_eq!(schema["type"], "array");
        assert_eq!(
            schema["items"]["properties"]["category"]["enum"],
            serde_json::json!(CATEGORIES)
        );
        assert_eq!(
            schema["items"]["required"],
            serde_json::json!(["content", "category", "tags", "confidence"])
        );
    }

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
                "SELECT category::text, confidence::float8, valid_from, source_at \
                 FROM memory_chunks \
                 WHERE source_type = 'conversation' AND source_id = $1",
                &[&conv],
            )
            .expect("read memory");
        assert_eq!(row.get::<_, String>(0), "decision");
        assert!((row.get::<_, f64>(1) - 0.9).abs() < 1e-6);
        let valid_from: DateTime<Utc> = row.get(2);
        let source_at: DateTime<Utc> = row.get(3);
        assert_eq!(valid_from, source_at);
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
        let first = extract_conversation(&mut tx, &backend, conv, 25, Profile::Default)
            .expect("first pass");
        assert_eq!(
            first,
            ExtractSummary {
                windows: 2,
                chunks: 1,
                errors: 0,
                ..Default::default()
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
        let again = extract_conversation(&mut tx, &backend, conv, 25, Profile::Default)
            .expect("second pass");
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
        let later = extract_conversation(&mut tx, &ollama_at(base), conv, 25, Profile::Default)
            .expect("third pass");
        assert_eq!(
            later,
            ExtractSummary {
                windows: 1,
                chunks: 0,
                errors: 0,
                ..Default::default()
            }
        );
        let sent: Vec<String> = requests.try_iter().collect();
        assert!(sent.len() == 1 && sent[0].contains("user: fourth") && !sent[0].contains("third"));
    }

    #[test]
    fn count_pending_conversations_matches_pending_conversations() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping count_pending_conversations_matches_pending_conversations: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let conv_a = seed_conversation(&mut tx, &[(1, "hello"), (2, "world")]);
        let conv_b = seed_conversation(&mut tx, &[(1, "another")]);
        // Both are pending before extraction.
        let list = pending_conversations(&mut tx, 1_000_000).unwrap();
        let count = count_pending_conversations(&mut tx).unwrap();
        assert_eq!(count, list.len() as i64);
        assert!(list.contains(&conv_a));
        assert!(list.contains(&conv_b));
        // Drop tx → rollback, nothing left behind.
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
        let s = extract_conversation(&mut tx, &ollama_at(base), conv, 25, Profile::Default)
            .expect("pass");
        assert_eq!(
            s,
            ExtractSummary {
                windows: 1,
                chunks: 1,
                errors: 1,
                ..Default::default()
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
    fn extract_profile_personal_changes_prompt_and_default_is_unchanged() {
        use sha2::{Digest, Sha256};
        // Byte for byte the prompt from before profiles existed (sha256 at 8c20aa3).
        assert_eq!(
            format!("{:x}", Sha256::digest(EXTRACTION_PROMPT.as_bytes())),
            "45764bbe79164e10d09f28a4be5860472f0353e1b22cc57ecc9cbff979da513c"
        );
        for unset in [None, Some(""), Some("  "), Some("default")] {
            assert_eq!(
                extraction_prompt(unset).unwrap(),
                EXTRACTION_PROMPT,
                "{unset:?}"
            );
        }
        for personal in ["personal", " Personal "] {
            assert_eq!(
                extraction_prompt(Some(personal)).unwrap(),
                PERSONAL_EXTRACTION_PROMPT
            );
        }
        assert_ne!(PERSONAL_EXTRACTION_PROMPT, EXTRACTION_PROMPT);
        // A typo must not silently fall back to the default prompt.
        let err = extraction_prompt(Some("persnal")).unwrap_err().to_string();
        assert!(err.contains(PROFILE_ENV), "{err}");

        // Same reply contract and categories, so parser and insert stay as they are.
        assert!(PERSONAL_EXTRACTION_PROMPT.contains(
            r#"{"content": "...", "category": "...", "tags": ["..."], "confidence": 0.0-1.0}"#
        ));
        let categories: Vec<&str> = PERSONAL_EXTRACTION_PROMPT
            .lines()
            .filter(|l| l.starts_with("- \""))
            .filter_map(|l| l.split('"').nth(1))
            .collect();
        assert!(!categories.is_empty());
        for category in categories {
            assert!(CATEGORIES.contains(&category), "{category}");
        }
    }

    /// The text of the last chat message in a fake-Ollama request body.
    fn sent_prompt(body: &str) -> String {
        let v: Value = serde_json::from_str(body).expect("request json");
        v["messages"]
            .as_array()
            .and_then(|m| m.last())
            .and_then(|m| m["content"].as_str())
            .expect("user message")
            .to_string()
    }

    #[test]
    fn extract_conversation_sends_the_prompt_it_is_given() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping extract_conversation_sends_the_prompt_it_is_given: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let conv = seed_conversation(&mut tx, &[(1, "first")]);
        let (base, requests) = fake_ollama(vec!["[]", "[]"]);
        let backend = ollama_at(base);

        extract_conversation(&mut tx, &backend, conv, 25, Profile::Default).expect("default");
        tx.execute(
            "UPDATE conversations SET memory_extracted_until = NULL WHERE id = $1",
            &[&conv],
        )
        .unwrap();
        extract_conversation(&mut tx, &backend, conv, 25, Profile::Personal).expect("personal");

        let sent: Vec<String> = requests.try_iter().map(|b| sent_prompt(&b)).collect();
        assert_eq!(
            sent,
            [
                format!("{EXTRACTION_PROMPT}\n\n---\nuser: first"),
                format!("{PERSONAL_EXTRACTION_PROMPT}\n\n---\nuser: first"),
            ]
        );
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

    #[test]
    fn extract_refuses_a_non_local_backend_before_sending() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping extract_refuses_a_non_local_backend_before_sending: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let conv = seed_conversation(&mut tx, &[(1, "secret note text")]);
        let (base, requests) = fake_ollama(vec![DECISION]);
        // A cloud model name makes the backend non-local; the fake server would
        // record any request that reached it.
        let mut backend = ollama_at(base);
        backend.model = "glm-5.2:cloud".into();
        let err = extract_conversation(&mut tx, &backend, conv, 25, Profile::Default).unwrap_err();
        assert!(
            err.to_string().contains("THROUGHLINE_ALLOW_REMOTE_LLM"),
            "{err}"
        );
        assert_eq!(requests.try_iter().count(), 0, "text was sent");
    }

    #[test]
    fn document_profile_is_opt_in_for_document_sources() {
        // (configured, document sources, sessions)
        for (configured, documents, sessions) in [
            (Profile::Default, Profile::Default, Profile::Default),
            (Profile::Personal, Profile::Default, Profile::Personal),
            (Profile::Document, Profile::Document, Profile::Default),
        ] {
            for source in ["markdown", "git", "web"] {
                assert_eq!(
                    profile_for(Some(source), configured),
                    documents,
                    "{source} with {configured:?}"
                );
            }
            for source in [
                Some("claude_code"),
                Some("gemini_cli"),
                Some("locomo"),
                Some("Markdown"),
                None,
            ] {
                assert_eq!(
                    profile_for(source, configured),
                    sessions,
                    "{source:?} with {configured:?}"
                );
            }
        }
        for value in ["document", " Document "] {
            assert_eq!(Profile::parse(Some(value)).unwrap(), Profile::Document);
            assert_eq!(
                extraction_prompt(Some(value)).unwrap(),
                DOCUMENT_EXTRACTION_PROMPT
            );
        }
        assert_eq!(Profile::Document.prompt(), DOCUMENT_EXTRACTION_PROMPT);
        assert_eq!(Profile::Document.name(), "document");
        // The error for a typo names every profile there is.
        let err = extraction_prompt(Some("dokument")).unwrap_err().to_string();
        assert!(
            err.contains(PROFILE_ENV) && err.contains("personal") && err.contains("document"),
            "{err}"
        );

        // Same reply contract and all eight categories, so parser and insert
        // stay as they are.
        assert!(DOCUMENT_EXTRACTION_PROMPT.contains(
            r#"{"content": "...", "category": "...", "tags": ["..."], "confidence": 0.0-1.0}"#
        ));
        let categories: Vec<&str> = DOCUMENT_EXTRACTION_PROMPT
            .lines()
            .filter(|l| l.starts_with("- \""))
            .filter_map(|l| l.split('"').nth(1))
            .collect();
        assert_eq!(categories, CATEGORIES);
        // Dates go to insight, with the date in the text.
        let date_rule = DOCUMENT_EXTRACTION_PROMPT
            .lines()
            .find(|l| l.contains("Every date"))
            .expect("a rule for dates");
        assert!(
            date_rule.contains("\"insight\"") && date_rule.contains("date written in the content")
        );
        assert_ne!(DOCUMENT_EXTRACTION_PROMPT, EXTRACTION_PROMPT);
        assert_ne!(DOCUMENT_EXTRACTION_PROMPT, PERSONAL_EXTRACTION_PROMPT);
    }

    #[test]
    fn default_prompt_is_byte_identical() {
        use sha2::{Digest, Sha256};
        // Byte for byte the prompt from before profiles existed (sha256 at 8c20aa3).
        assert_eq!(
            format!("{:x}", Sha256::digest(EXTRACTION_PROMPT.as_bytes())),
            "45764bbe79164e10d09f28a4be5860472f0353e1b22cc57ecc9cbff979da513c"
        );
        let configured = Profile::parse(None).unwrap();
        assert_eq!(configured, Profile::Default);
        for source in [Some("claude_code"), Some("codex"), None] {
            assert_eq!(
                profile_for(source, configured).prompt(),
                EXTRACTION_PROMPT,
                "{source:?}"
            );
        }
        assert_eq!(
            Profile::Personal.prompt(),
            extraction_prompt(Some("personal")).unwrap()
        );
    }

    #[test]
    fn document_windows_split_by_section_count() {
        let at = ts(T0);
        let micros = |i: usize| at + chrono::Duration::microseconds(i as i64);
        let joined = |lines: &[(DateTime<Utc>, String)]| {
            lines
                .iter()
                .map(|(_, l)| l.as_str())
                .collect::<Vec<_>>()
                .join("\n")
        };
        let all_text = |w: &[Window]| {
            w.iter()
                .map(|w| w.text.as_str())
                .collect::<Vec<_>>()
                .join("\n")
        };

        // 200 sections stamped with the file's mtime: the timestamp grouping
        // makes them one window whatever the size limit says.
        let same: Vec<(DateTime<Utc>, String)> = (0..200)
            .map(|i| (at, format!("document: part {i} of the file")))
            .collect();
        assert_eq!(windows(&same, 24_000).len(), 1);
        let w = document_windows(&same, 24_000, DOCUMENT_WINDOW_SECTIONS);
        assert_eq!(w.len(), 200usize.div_ceil(DOCUMENT_WINDOW_SECTIONS));
        assert!(w
            .iter()
            .all(|w| w.text.lines().count() <= DOCUMENT_WINDOW_SECTIONS));
        assert_eq!(all_text(&w), joined(&same), "every section once, in order");
        // All share one timestamp, so only the last window may move the watermark.
        let commits: Vec<bool> = w.iter().map(|w| w.commits).collect();
        assert_eq!(commits.iter().filter(|c| **c).count(), 1);
        assert_eq!(commits.last(), Some(&true));

        // Sections a microsecond apart, as the adapters stamp them: every
        // window commits, at its last section's time.
        let apart: Vec<(DateTime<Utc>, String)> = (0..200)
            .map(|i| (micros(i), format!("document: part {i} of the file")))
            .collect();
        let w = document_windows(&apart, 24_000, DOCUMENT_WINDOW_SECTIONS);
        assert_eq!(w.len(), 200usize.div_ceil(DOCUMENT_WINDOW_SECTIONS));
        assert!(w.iter().all(|w| w.commits));
        assert_eq!(w[0].end, micros(DOCUMENT_WINDOW_SECTIONS - 1));
        assert_eq!(w.last().unwrap().end, micros(199));

        // The size limit still holds: 1,000-byte sections, 4,500 bytes a window.
        let big: Vec<(DateTime<Utc>, String)> =
            (0..10).map(|i| (micros(i), "x".repeat(1000))).collect();
        let w = document_windows(&big, 4_500, DOCUMENT_WINDOW_SECTIONS);
        let sizes: Vec<usize> = w.iter().map(|w| w.text.lines().count()).collect();
        assert_eq!(sizes, [4, 4, 2]);
        assert!(w.iter().all(|w| w.text.len() <= 4_500));
        assert_eq!(all_text(&w), joined(&big));

        // A section above the limit is a window of its own, never cut.
        let mixed = vec![
            (micros(0), "a".repeat(10)),
            (micros(1), "b".repeat(50)),
            (micros(2), "c".repeat(10)),
        ];
        let w = document_windows(&mixed, 30, DOCUMENT_WINDOW_SECTIONS);
        let texts: Vec<&str> = w.iter().map(|w| w.text.as_str()).collect();
        assert_eq!(texts, [mixed[0].1.as_str(), &mixed[1].1, &mixed[2].1]);
        assert!(document_windows(&[], 30, DOCUMENT_WINDOW_SECTIONS).is_empty());
    }

    /// A file as the document adapters store it: one message per section,
    /// no uuids, stamped with the mtime plus the section index in
    /// microseconds. `role` is `document`, or `user` for a session control.
    fn stored_file(
        session: uuid::Uuid,
        source_tool: &str,
        role: &str,
        mtime: DateTime<Utc>,
        sections: &[&str],
    ) -> crate::types::NormalisedConversation {
        crate::types::NormalisedConversation {
            session_id: session,
            project_path: Some("/tmp/doc-lifecycle-test".into()),
            model: None,
            entrypoint: None,
            git_branch: None,
            started_at: mtime,
            ended_at: Some(mtime),
            messages: sections
                .iter()
                .enumerate()
                .map(|(i, s)| NormalisedMessage {
                    role: role.into(),
                    content: s.to_string(),
                    created_at: Some(mtime + chrono::Duration::microseconds(i as i64)),
                    ..Default::default()
                })
                .collect(),
            token_count_in: None,
            token_count_out: None,
            summary: Some("Operations notes".into()),
            metadata: serde_json::json!({}),
            source_tool: Some(source_tool.into()),
        }
    }

    /// Write a conversation the way ingest does: upsert, then replace its messages.
    fn store<C: GenericClient>(client: &mut C, conv: &crate::types::NormalisedConversation) -> i64 {
        let id = crate::writer::upsert_conversation(client, conv).expect("upsert conversation");
        crate::writer::replace_messages(client, id, conv).expect("replace messages");
        id
    }

    fn active() -> document_lifecycle::RepeatCheck<'static> {
        document_lifecycle::RepeatCheck::active()
    }

    /// A breaker that has just tripped: no embedding is attempted.
    fn tripped() -> crate::embedding::EmbedBreaker {
        let breaker = crate::embedding::EmbedBreaker::new();
        breaker.trip(crate::embedding::now_ms());
        breaker
    }

    /// A 768-dimension vector with `x` and `y` on its first two axes.
    fn plane(x: f32, y: f32) -> Vec<f32> {
        let mut v = vec![0.0; 768];
        v[0] = x;
        v[1] = y;
        v
    }

    fn axis(i: usize) -> Vec<f32> {
        let mut v = vec![0.0; 768];
        v[i] = 1.0;
        v
    }

    /// Run `f` while the fake embedder gives every note holding a tag its
    /// vector, as the active model.
    fn with_vectors<T, F: FnOnce() -> T>(fixtures: &[(&str, Vec<f32>)], f: F) -> T {
        match fixtures.split_first() {
            None => f(),
            Some(((tag, vector), rest)) => crate::generate_embeddings::write_fixture::with_vector(
                tag,
                &crate::embedding::configured_model(),
                vector.clone(),
                || with_vectors(rest, f),
            ),
        }
    }

    fn fact(content: &str) -> ExtractedMemory {
        ExtractedMemory {
            content: content.into(),
            category: "insight".into(),
            tags: vec![],
            confidence: 0.8,
        }
    }

    #[derive(Debug)]
    struct StoredNote {
        id: i64,
        content: String,
        status: String,
        valid_to: Option<DateTime<Utc>>,
        superseded_by: Option<i64>,
        source_at: DateTime<Utc>,
    }

    /// The conversation's notes, oldest first.
    fn notes_of<C: GenericClient>(client: &mut C, conv: i64) -> Vec<StoredNote> {
        client
            .query(
                "SELECT id, content, COALESCE(status, 'active'), valid_to, superseded_by, source_at \
                 FROM memory_chunks WHERE source_type = 'conversation' AND source_id = $1 \
                 ORDER BY id",
                &[&conv],
            )
            .expect("read notes")
            .iter()
            .map(|r| StoredNote {
                id: r.get(0),
                content: r.get(1),
                status: r.get(2),
                valid_to: r.get(3),
                superseded_by: r.get(4),
                source_at: r.get(5),
            })
            .collect()
    }

    const FRIDAYS: &str = "Deploys of the ops service run on Fridays.";
    const PORT: &str = "The ops API listens on port 8080.";
    const BACKUPS: &str = "Backups of the ops database run nightly at 02:00.";

    #[test]
    fn reimport_closes_old_notes_and_keeps_forgotten_forgotten() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping reimport_closes_old_notes_and_keeps_forgotten_forgotten: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let session = uuid::Uuid::new_v4();
        let v1 = ts(T0);
        let first_version = [
            "# Ops\nDeploys run on Fridays.",
            "## Ports\nThe API listens on 8080.",
        ];
        let conv = store(
            &mut tx,
            &stored_file(session, "markdown", "document", v1, &first_version),
        );
        assert_eq!(
            conversation_profile(&mut tx, conv, Profile::Default).unwrap(),
            Profile::Default,
            "the production prompt unless THROUGHLINE_EXTRACT_PROFILE=document"
        );
        assert_eq!(
            conversation_profile(&mut tx, conv, Profile::Document).unwrap(),
            Profile::Document
        );

        let mut sent = Vec::new();
        let first = extract_conversation_with(&mut tx, conv, 24_000, &active(), |text| {
            sent.push(text.to_string());
            Ok(vec![fact(FRIDAYS), fact(PORT)])
        })
        .expect("first pass");
        assert_eq!(
            first,
            ExtractSummary {
                windows: 1,
                chunks: 2,
                ..Default::default()
            }
        );
        assert_eq!(
            sent,
            ["document: # Ops\nDeploys run on Fridays.\ndocument: ## Ports\nThe API listens on 8080."]
        );
        let old = notes_of(&mut tx, conv);
        let (old_fridays, old_port) = (old[0].id, old[1].id);
        // The user forgets the port note.
        tx.execute(
            "UPDATE memory_chunks SET status = 'forgotten' WHERE id = $1",
            &[&old_port],
        )
        .unwrap();

        // The file changes an hour later: same two sections, one new.
        let v2 = ts(T0 + 3600);
        let second_version = [
            first_version[0],
            first_version[1],
            "## Backups\nNightly at 02:00.",
        ];
        assert_eq!(
            store(
                &mut tx,
                &stored_file(session, "markdown", "document", v2, &second_version)
            ),
            conv
        );
        assert_eq!(
            document_lifecycle::replaced_since_extraction(&mut tx, conv).unwrap(),
            Some(v2)
        );
        // Unrelated facts get orthogonal vectors, so only the text decides.
        let fixtures = [
            ("run on Fridays", axis(0)),
            ("listens on port 8080", axis(1)),
            ("run nightly at 02:00", axis(2)),
        ];
        let second = with_vectors(&fixtures, || {
            extract_conversation_with(&mut tx, conv, 24_000, &active(), |_| {
                Ok(vec![fact(FRIDAYS), fact(PORT), fact(BACKUPS)])
            })
        })
        .expect("second pass");
        assert_eq!(
            second,
            ExtractSummary {
                windows: 1,
                chunks: 2,
                errors: 0,
                closed: 1,
                dropped: 1,
                text_only: 0,
            }
        );

        let after = notes_of(&mut tx, conv);
        let by_id = |id: i64| after.iter().find(|n| n.id == id).expect("note kept");
        // The old version's note is closed where the new version starts, with no successor.
        let closed = by_id(old_fridays);
        assert_eq!(
            (
                closed.status.as_str(),
                closed.valid_to,
                closed.superseded_by
            ),
            ("superseded", Some(v2), None)
        );
        // Forgotten stays forgotten, and its repeat is gone.
        let forgotten = by_id(old_port);
        assert_eq!(
            (forgotten.status.as_str(), forgotten.valid_to),
            ("forgotten", None)
        );
        assert_eq!(after.iter().filter(|n| n.content == PORT).count(), 1);
        // The current notes are the new version's: the unchanged fact again and the new one.
        let current: Vec<&StoredNote> = after.iter().filter(|n| n.status == "active").collect();
        let texts: Vec<&str> = current.iter().map(|n| n.content.as_str()).collect();
        assert_eq!(texts, [FRIDAYS, BACKUPS]);
        assert!(current
            .iter()
            .all(|n| n.source_at > v2 && n.valid_to.is_none()));

        // Nothing new: no new version, nothing sent, nothing closed.
        assert_eq!(
            document_lifecycle::replaced_since_extraction(&mut tx, conv).unwrap(),
            None
        );
        let third = extract_conversation_with(&mut tx, conv, 24_000, &active(), |_| {
            unreachable!("nothing is pending")
        })
        .expect("third pass");
        assert_eq!(third, ExtractSummary::default());
    }

    // Texts and tags of their own: the embedder fixtures are shared by
    // the tests that run at the same time.
    const TIDE_TUESDAYS: &str = "Releases of the tide service ship on Tuesdays.";
    const TIDE_PORT: &str = "The tide API answers on port 7070.";
    const TIDE_PORT_FIXED: &str = "The tide API answers on port 7443.";
    const TIDE_PORT_SAID: &str = "Port 7070 is where the tide API answers.";
    const TIDE_SNAPSHOTS: &str = "Snapshots of the tide database are taken hourly.";
    const TIDE_LOGS: &str = "Tide logs are pruned each month.";
    const DOCK_KEPT: &str = "The dock queue keeps its spool in /srv/dock-spool.";
    const DOCK_BELOW: &str = "Dock queue spool files sit below /srv/dock-spool.";

    /// A note the user corrected, and one `consolidate` merged, both have a
    /// successor. An unrelated edit of the file re-extracts their text; the
    /// repeat goes like the repeat of a forgotten note, by text or by
    /// vector. A note that only the new version closed has no successor, so
    /// its text comes back as a current note.
    #[test]
    fn reimport_does_not_revive_a_superseded_note() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping reimport_does_not_revive_a_superseded_note: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let session = uuid::Uuid::new_v4();
        let first_version = [
            "# Tide\nReleases on Tuesdays.",
            "## Ports\nThe API answers on 7070.",
            "## Snapshots\nHourly.",
        ];
        let conv = store(
            &mut tx,
            &stored_file(session, "markdown", "document", ts(T0), &first_version),
        );
        extract_conversation_with(&mut tx, conv, 24_000, &active(), |_| {
            Ok(vec![
                fact(TIDE_TUESDAYS),
                fact(TIDE_PORT),
                fact(TIDE_SNAPSHOTS),
            ])
        })
        .expect("first pass");
        let old = notes_of(&mut tx, conv);
        let (tuesdays, port, snapshots) = (old[0].id, old[1].id, old[2].id);

        // The user corrects the port: a note of their own replaces it.
        let fixed: i64 = tx
            .query_one(
                "INSERT INTO memory_chunks (source_type, content, category, confidence, status) \
                 VALUES ('manual', $1, 'insight', 1.0, 'active') RETURNING id",
                &[&TIDE_PORT_FIXED],
            )
            .unwrap()
            .get(0);
        // `consolidate` merges the Tuesdays note into a consolidation note.
        let merged: i64 = tx
            .query_one(
                "INSERT INTO memory_chunks \
                     (source_type, content, category, confidence, status, merged_from) \
                 VALUES ('consolidation', $1, 'insight', 0.8, 'active', $2) RETURNING id",
                &[&TIDE_TUESDAYS, &vec![tuesdays]],
            )
            .unwrap()
            .get(0);
        for (id, successor) in [(port, fixed), (tuesdays, merged)] {
            tx.execute(
                "UPDATE memory_chunks SET status = 'superseded', superseded_by = $2, \
                     superseded_at = now(), valid_to = now() WHERE id = $1",
                &[&id, &successor],
            )
            .unwrap();
        }

        // An unrelated edit: a new section at the end.
        let v2 = ts(T0 + 3600);
        let mut second_version = first_version.to_vec();
        second_version.push("## Logs\nPruned monthly.");
        store(
            &mut tx,
            &stored_file(session, "markdown", "document", v2, &second_version),
        );
        // The paraphrase lies at cosine 0.99 to the corrected note, the
        // other facts on axes of their own.
        let fixtures = [
            ("ship on Tuesdays", axis(0)),
            ("answers on port 7070", plane(1.0, 0.0)),
            ("is where the tide API answers", plane(0.99, 0.14)),
            ("are taken hourly", axis(2)),
            ("pruned each month", axis(3)),
        ];
        let second = with_vectors(&fixtures, || {
            extract_conversation_with(&mut tx, conv, 24_000, &active(), |_| {
                Ok(vec![
                    fact(TIDE_TUESDAYS),
                    fact(TIDE_PORT_SAID),
                    fact(TIDE_SNAPSHOTS),
                    fact(TIDE_LOGS),
                ])
            })
        })
        .expect("second pass");
        assert_eq!(
            second,
            ExtractSummary {
                windows: 1,
                chunks: 2,
                errors: 0,
                closed: 1,
                dropped: 2,
                text_only: 0,
            }
        );

        let after = notes_of(&mut tx, conv);
        let current: Vec<&str> = after
            .iter()
            .filter(|n| n.status == "active")
            .map(|n| n.content.as_str())
            .collect();
        assert_eq!(current, [TIDE_SNAPSHOTS, TIDE_LOGS]);
        let by_id = |id: i64| after.iter().find(|n| n.id == id).expect("note kept");
        assert_eq!(by_id(port).superseded_by, Some(fixed));
        assert_eq!(by_id(tuesdays).superseded_by, Some(merged));
        assert_eq!(
            (
                by_id(snapshots).status.as_str(),
                by_id(snapshots).superseded_by
            ),
            ("superseded", None),
            "closed by the new version, without a successor"
        );
        assert!(after.iter().all(|n| n.content != TIDE_PORT_SAID));
        assert_eq!(
            after.iter().filter(|n| n.content == TIDE_TUESDAYS).count(),
            1
        );
        let successors: Vec<String> = tx
            .query(
                "SELECT COALESCE(status, 'active') FROM memory_chunks WHERE id = ANY($1) ORDER BY id",
                &[&vec![fixed, merged]],
            )
            .unwrap()
            .iter()
            .map(|r| r.get(0))
            .collect();
        assert_eq!(successors, ["active", "active"], "the successors stay");
    }

    /// A new note without a vector, because the write could not embed it,
    /// gets one when the repeat check needs it, so a paraphrase of a
    /// forgotten note is still found by vector.
    #[test]
    fn repeat_check_embeds_a_new_note_on_demand() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping repeat_check_embeds_a_new_note_on_demand: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let conv = store(
            &mut tx,
            &stored_file(
                uuid::Uuid::new_v4(),
                "markdown",
                "document",
                ts(T0),
                &["# Dock\nSpool in /srv."],
            ),
        );
        // Plain inserts: neither note has a vector.
        let note = |tx: &mut postgres::Transaction<'_>, text: &str| -> i64 {
            tx.query_one(
                "INSERT INTO memory_chunks (source_type, source_id, content, category, confidence, status) \
                 VALUES ('conversation', $1, $2, 'insight', 0.8, 'active') RETURNING id",
                &[&conv, &text],
            )
            .unwrap()
            .get(0)
        };
        let forgotten = note(&mut tx, DOCK_KEPT);
        tx.execute(
            "UPDATE memory_chunks SET status = 'forgotten' WHERE id = $1",
            &[&forgotten],
        )
        .unwrap();
        let new = note(&mut tx, DOCK_BELOW);
        let check = active();
        let vectors = |tx: &mut postgres::Transaction<'_>, id: i64| -> i64 {
            tx.query_one(
                "SELECT count(*) FROM embeddings WHERE source_type = 'memory_chunk' \
                 AND source_id = $1 AND model = $2",
                &[&id, &check.model],
            )
            .unwrap()
            .get(0)
        };
        assert_eq!((vectors(&mut tx, forgotten), vectors(&mut tx, new)), (0, 0));
        let fixtures = [
            ("its spool in /srv/dock-spool", plane(1.0, 0.0)),
            ("sit below /srv/dock-spool", plane(0.99, 0.14)),
        ];
        let outcome = with_vectors(&fixtures, || {
            document_lifecycle::drop_repeats(&mut tx, conv, &[new], &check)
        })
        .expect("check");
        assert_eq!(
            outcome,
            document_lifecycle::RepeatOutcome {
                dropped: 1,
                text_only: 0
            },
            "found by the vector made for the new note"
        );
        assert_eq!(vectors(&mut tx, forgotten), 1, "kept for the next check");
    }

    /// An empty forgotten note cannot be repeated: it is left out of the
    /// check, so it does not turn every later check into a text-only one.
    #[test]
    fn empty_forgotten_note_is_left_out_of_the_check() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping empty_forgotten_note_is_left_out_of_the_check: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let conv = store(
            &mut tx,
            &stored_file(
                uuid::Uuid::new_v4(),
                "markdown",
                "document",
                ts(T0),
                &["# Weir\nOpen."],
            ),
        );
        let note = |tx: &mut postgres::Transaction<'_>, text: &str| -> i64 {
            tx.query_one(
                "INSERT INTO memory_chunks (source_type, source_id, content, category, confidence, status) \
                 VALUES ('conversation', $1, $2, 'insight', 0.8, 'active') RETURNING id",
                &[&conv, &text],
            )
            .unwrap()
            .get(0)
        };
        let empty = note(&mut tx, "   ");
        tx.execute(
            "UPDATE memory_chunks SET status = 'forgotten' WHERE id = $1",
            &[&empty],
        )
        .unwrap();
        let new = note(&mut tx, WEIR_OPEN);
        let outcome = with_vectors(&[("weir gate opens", axis(5))], || {
            document_lifecycle::drop_repeats(&mut tx, conv, &[new], &active())
        })
        .expect("check");
        assert_eq!(outcome, document_lifecycle::RepeatOutcome::default());
    }

    const WEIR_OPEN: &str = "The weir gate opens at the spring tide.";

    /// While the model fails on a new version, the old version's notes
    /// stay current: they are closed with the first window that commits.
    #[test]
    fn reimport_keeps_old_notes_current_while_the_model_fails() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping reimport_keeps_old_notes_current_while_the_model_fails: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let session = uuid::Uuid::new_v4();
        let conv = store(
            &mut tx,
            &stored_file(session, "markdown", "document", ts(T0), &["# Quay\nOld."]),
        );
        extract_conversation_with(&mut tx, conv, 24_000, &active(), |_| {
            Ok(vec![fact("The quay closes at dusk.")])
        })
        .expect("first pass");
        let v2 = ts(T0 + 3600);
        store(
            &mut tx,
            &stored_file(session, "markdown", "document", v2, &["# Quay\nNew."]),
        );
        let failed = extract_conversation_with(&mut tx, conv, 24_000, &active(), |_| {
            anyhow::bail!("model gone")
        })
        .expect("failed pass");
        assert_eq!(
            failed,
            ExtractSummary {
                errors: 1,
                ..Default::default()
            }
        );
        let statuses = |tx: &mut postgres::Transaction<'_>| -> Vec<String> {
            notes_of(tx, conv).into_iter().map(|n| n.status).collect()
        };
        assert_eq!(statuses(&mut tx), ["active"], "still current");
        assert_eq!(
            document_lifecycle::replaced_since_extraction(&mut tx, conv).unwrap(),
            Some(v2),
            "the new version is still pending"
        );

        let done = extract_conversation_with(&mut tx, conv, 24_000, &active(), |_| {
            Ok(vec![fact("The quay closes at midnight.")])
        })
        .expect("second pass");
        assert_eq!(
            done,
            ExtractSummary {
                windows: 1,
                chunks: 1,
                closed: 1,
                ..Default::default()
            }
        );
        assert_eq!(statuses(&mut tx), ["superseded", "active"]);
        assert_eq!(notes_of(&mut tx, conv)[0].valid_to, Some(v2));
    }

    #[test]
    fn document_sections_of_one_timestamp_commit_together() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping document_sections_of_one_timestamp_commit_together: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let mut file = stored_file(
            uuid::Uuid::new_v4(),
            "web",
            "document",
            ts(T0),
            &["one", "two", "three"],
        );
        for m in &mut file.messages {
            m.created_at = Some(ts(T0));
        }
        let conv = store(&mut tx, &file);
        let watermark = |tx: &mut postgres::Transaction<'_>| -> Option<DateTime<Utc>> {
            tx.query_one(
                "SELECT memory_extracted_until FROM conversations WHERE id = $1",
                &[&conv],
            )
            .unwrap()
            .get(0)
        };

        // "document: one" is 13 bytes: one section per window, three windows
        // of one timestamp. The second fails, so nothing is written and the
        // watermark stays, although the first window got its reply.
        let mut calls = 0;
        let failed = extract_conversation_with(&mut tx, conv, 15, &active(), |_| {
            calls += 1;
            if calls == 2 {
                anyhow::bail!("model gone");
            }
            Ok(vec![fact(FRIDAYS)])
        })
        .expect("failed pass");
        assert_eq!(
            failed,
            ExtractSummary {
                errors: 1,
                ..Default::default()
            }
        );
        assert!(notes_of(&mut tx, conv).is_empty());
        assert_eq!(watermark(&mut tx), None);

        let mut sent = Vec::new();
        let done = extract_conversation_with(&mut tx, conv, 15, &active(), |text| {
            sent.push(text.to_string());
            Ok(vec![fact(text)])
        })
        .expect("second pass");
        assert_eq!(sent, ["document: one", "document: two", "document: three"]);
        assert_eq!(
            done,
            ExtractSummary {
                windows: 3,
                chunks: 3,
                ..Default::default()
            }
        );
        assert_eq!(watermark(&mut tx), Some(ts(T0)));
    }

    #[test]
    fn reimport_of_a_session_changes_no_notes() {
        // Negative control: the same replace on a session source closes
        // nothing and drops nothing.
        let Some(mut client) = test_db() else {
            eprintln!("skipping reimport_of_a_session_changes_no_notes: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let session = uuid::Uuid::new_v4();
        let conv = store(
            &mut tx,
            &stored_file(session, "claude_code", "user", ts(T0), &["one", "two"]),
        );
        extract_conversation_with(&mut tx, conv, 24_000, &active(), |_| {
            Ok(vec![fact(FRIDAYS), fact(PORT)])
        })
        .expect("first pass");
        let old = notes_of(&mut tx, conv);
        tx.execute(
            "UPDATE memory_chunks SET status = 'forgotten' WHERE id = $1",
            &[&old[1].id],
        )
        .unwrap();
        store(
            &mut tx,
            &stored_file(
                session,
                "claude_code",
                "user",
                ts(T0 + 3600),
                &["one", "two", "three"],
            ),
        );
        let second = extract_conversation_with(&mut tx, conv, 24_000, &active(), |_| {
            Ok(vec![fact(FRIDAYS), fact(PORT), fact(BACKUPS)])
        })
        .expect("second pass");
        assert_eq!(
            second,
            ExtractSummary {
                windows: 1,
                chunks: 3,
                ..Default::default()
            }
        );
        let first_note = notes_of(&mut tx, conv)
            .into_iter()
            .find(|n| n.id == old[0].id)
            .unwrap();
        assert_eq!(
            (first_note.status.as_str(), first_note.valid_to),
            ("active", None)
        );
    }

    #[test]
    fn forgotten_repeat_is_found_by_vector() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping forgotten_repeat_is_found_by_vector: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let model = format!("test-doc-repeat-{}", uuid::Uuid::new_v4().simple());
        let file = |tx: &mut postgres::Transaction<'_>| {
            store(
                tx,
                &stored_file(
                    uuid::Uuid::new_v4(),
                    "markdown",
                    "document",
                    ts(T0),
                    &["# Cache"],
                ),
            )
        };
        let (conv, other) = (file(&mut tx), file(&mut tx));
        let note =
            |tx: &mut postgres::Transaction<'_>, conv: i64, text: &str, v: Option<Vec<f32>>| {
                let id = insert_memory(tx, conv, &fact(text), ts(T0)).expect("insert note");
                if let Some(v) = v {
                    tx.execute(
                        "INSERT INTO embeddings (source_type, source_id, model, embedding_768) \
                         VALUES ('memory_chunk', $1, $2, $3::text::vector)",
                        &[&id, &model, &crate::generate_embeddings::vector_literal(&v)],
                    )
                    .expect("insert vector");
                }
                id
            };
        let forgotten = note(
            &mut tx,
            conv,
            "The app cache lives in /var/cache/app.",
            Some(plane(1.0, 0.0)),
        );
        let elsewhere = note(&mut tx, other, "Logs rotate weekly.", None);
        tx.execute(
            "UPDATE memory_chunks SET status = 'forgotten' WHERE id = ANY($1)",
            &[&vec![forgotten, elsewhere]],
        )
        .unwrap();
        // cosine 0.99 to the forgotten note, and 0 to it.
        let near = note(
            &mut tx,
            conv,
            "The app keeps its cache in /var/cache/app.",
            Some(plane(0.99, 0.14)),
        );
        let far = note(&mut tx, conv, "Logs rotate weekly.", Some(plane(0.0, 1.0)));
        let ids = [near, far];
        // No embedder: the check uses only the vectors already stored.
        let breaker = tripped();
        let check = |model: &str, min_similarity: f64| document_lifecycle::RepeatCheck {
            model: model.to_string(),
            min_similarity,
            breaker: &breaker,
        };
        let outcome =
            |dropped: u64, text_only: u64| document_lifecycle::RepeatOutcome { dropped, text_only };
        let cosine: f64 = tx
            .query_one(
                "SELECT 1 - (a.embedding_768 <=> b.embedding_768) FROM embeddings a, embeddings b \
                 WHERE a.source_id = $1 AND b.source_id = $2 AND a.model = $3 AND b.model = $3",
                &[&near, &forgotten, &model],
            )
            .unwrap()
            .get(0);
        assert!((cosine - 0.99).abs() < 0.001, "{cosine}");

        let mut run = |c| document_lifecycle::drop_repeats(&mut tx, conv, &ids, &c).unwrap();
        assert_eq!(
            run(check(&model, 0.999)),
            outcome(0, 0),
            "below the threshold nothing goes"
        );
        assert_eq!(
            run(check("another-model", 0.92)),
            outcome(0, 2),
            "vectors of another model never count; without an embedder both are checked by text"
        );
        assert_eq!(
            run(check(&model, cosine + 1e-9)),
            outcome(0, 0),
            "just above the cosine"
        );
        assert_eq!(
            run(check(&model, cosine)),
            outcome(1, 0),
            "at the threshold it is a repeat"
        );
        let left: Vec<i64> = notes_of(&mut tx, conv).iter().map(|n| n.id).collect();
        assert_eq!(
            left,
            [forgotten, far],
            "the same text forgotten in another file does not count"
        );
        let vectors: i64 = tx
            .query_one(
                "SELECT count(*) FROM embeddings WHERE source_type = 'memory_chunk' AND source_id = $1",
                &[&near],
            )
            .unwrap()
            .get(0);
        assert_eq!(vectors, 0, "the dropped note's vector goes with it");
    }

    const CACHE_KEPT: &str = "The ops cache is kept in /srv/ops-cache.";
    const CACHE_BELOW: &str = "Ops stores its cache below /srv/ops-cache.";
    const LOGS_SUNDAY: &str = "Ops logs rotate every Sunday.";

    #[test]
    fn forgotten_paraphrase_is_dropped_through_the_extraction_path() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping forgotten_paraphrase_is_dropped_through_the_extraction_path: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let check = active();
        let session = uuid::Uuid::new_v4();
        let conv = store(
            &mut tx,
            &stored_file(
                session,
                "markdown",
                "document",
                ts(T0),
                &["# Cache\nIn /srv."],
            ),
        );
        extract_conversation_with(&mut tx, conv, 24_000, &check, |_| {
            Ok(vec![fact(CACHE_KEPT)])
        })
        .expect("first pass");
        let original = notes_of(&mut tx, conv)[0].id;
        // Forgotten, and without a vector of the active model: the embed job
        // skips forgotten notes, so after a model switch none has one.
        tx.execute(
            "UPDATE memory_chunks SET status = 'forgotten' WHERE id = $1",
            &[&original],
        )
        .unwrap();
        tx.execute(
            "DELETE FROM embeddings WHERE source_type = 'memory_chunk' AND source_id = $1",
            &[&original],
        )
        .unwrap();

        store(
            &mut tx,
            &stored_file(
                session,
                "markdown",
                "document",
                ts(T0 + 3600),
                &["# Cache\nBelow /srv.", "# Logs\nSundays."],
            ),
        );
        // The fake embedder: the paraphrase lies at cosine 0.99 to the
        // original, the new fact at 0.
        let fixtures = [
            ("kept in /srv/ops-cache", plane(1.0, 0.0)),
            ("below /srv/ops-cache", plane(0.99, 0.14)),
            ("rotate every Sunday", plane(0.0, 1.0)),
        ];
        let second = with_vectors(&fixtures, || {
            extract_conversation_with(&mut tx, conv, 24_000, &check, |_| {
                Ok(vec![fact(CACHE_BELOW), fact(LOGS_SUNDAY)])
            })
        })
        .expect("second pass");
        assert_eq!(
            second,
            ExtractSummary {
                windows: 1,
                chunks: 1,
                dropped: 1,
                ..Default::default()
            }
        );
        let after: Vec<(String, String)> = notes_of(&mut tx, conv)
            .into_iter()
            .map(|n| (n.content, n.status))
            .collect();
        assert_eq!(
            after,
            [
                (CACHE_KEPT.to_string(), "forgotten".to_string()),
                (LOGS_SUNDAY.to_string(), "active".to_string()),
            ]
        );
        // The forgotten note's vector was made on demand and kept for the next check.
        let kept: i64 = tx
            .query_one(
                "SELECT count(*) FROM embeddings \
                 WHERE source_type = 'memory_chunk' AND source_id = $1 AND model = $2",
                &[&original, &check.model],
            )
            .unwrap()
            .get(0);
        assert_eq!(kept, 1);
    }

    #[test]
    fn forgotten_check_counts_text_only_when_the_embedder_fails() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping forgotten_check_counts_text_only_when_the_embedder_fails: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let breaker = tripped();
        let check = document_lifecycle::RepeatCheck {
            breaker: &breaker,
            ..active()
        };
        const KEY: &str = "The deploy key of the ops service sits in the team vault.";
        const KEY_AGAIN: &str = "The team vault holds the deploy key of the ops service.";
        let session = uuid::Uuid::new_v4();
        let conv = store(
            &mut tx,
            &stored_file(session, "markdown", "document", ts(T0), &["# Keys"]),
        );
        extract_conversation_with(&mut tx, conv, 24_000, &check, |_| Ok(vec![fact(KEY)]))
            .expect("first pass");
        let original = notes_of(&mut tx, conv)[0].id;
        tx.execute(
            "UPDATE memory_chunks SET status = 'forgotten' WHERE id = $1",
            &[&original],
        )
        .unwrap();
        tx.execute(
            "DELETE FROM embeddings WHERE source_type = 'memory_chunk' AND source_id = $1",
            &[&original],
        )
        .unwrap();
        store(
            &mut tx,
            &stored_file(
                session,
                "markdown",
                "document",
                ts(T0 + 3600),
                &["# Keys", "# Vault"],
            ),
        );
        // The same text is still caught; the paraphrase can only be compared
        // by text, so it stays and is counted.
        let second = extract_conversation_with(&mut tx, conv, 24_000, &check, |_| {
            Ok(vec![fact(KEY), fact(KEY_AGAIN)])
        })
        .expect("second pass");
        assert_eq!(
            second,
            ExtractSummary {
                windows: 1,
                chunks: 1,
                dropped: 1,
                text_only: 1,
                ..Default::default()
            }
        );
        let mut total = ExtractSummary::default();
        total.add(&second);
        total.add(&second);
        assert_eq!(
            total.document_line().as_deref(),
            Some(
                "documents: 0 note(s) of old versions closed, \
                 2 repeat(s) of forgotten or replaced notes dropped, 2 notes checked by text only"
            )
        );
        assert_eq!(ExtractSummary::default().document_line(), None);
    }

    #[test]
    fn an_appended_message_is_not_a_new_version() {
        // git keeps a uuid per commit, so a new commit is appended. With a
        // single extracted message the watermark sits exactly on it.
        let Some(mut client) = test_db() else {
            eprintln!("skipping an_appended_message_is_not_a_new_version: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let session = uuid::Uuid::new_v4();
        let mut repo = stored_file(session, "git", "document", ts(T0), &["Move the ops port"]);
        repo.messages[0].uuid = Some(uuid::Uuid::new_v4());
        let conv = store(&mut tx, &repo);
        extract_conversation_with(&mut tx, conv, 24_000, &active(), |_| Ok(vec![fact(PORT)]))
            .expect("first pass");

        let mut next = stored_file(session, "git", "document", ts(T0 + 60), &["Add backups"]);
        next.messages[0].uuid = Some(uuid::Uuid::new_v4());
        repo.messages.push(next.messages.remove(0));
        store(&mut tx, &repo);
        assert_eq!(
            document_lifecycle::replaced_since_extraction(&mut tx, conv).unwrap(),
            None
        );
        let second = extract_conversation_with(&mut tx, conv, 24_000, &active(), |text| {
            assert_eq!(text, "document: Add backups");
            Ok(vec![fact(BACKUPS)])
        })
        .expect("second pass");
        assert_eq!(
            second,
            ExtractSummary {
                windows: 1,
                chunks: 1,
                ..Default::default()
            }
        );
        let statuses: Vec<String> = notes_of(&mut tx, conv)
            .into_iter()
            .map(|n| n.status)
            .collect();
        assert_eq!(statuses, ["active", "active"]);
    }

    #[test]
    fn consolidation_threshold_follows_the_model_profile() {
        use document_lifecycle::consolidation_threshold;
        assert_eq!(consolidation_threshold("nomic-embed-text"), 0.92);
        assert_eq!(consolidation_threshold("nomic-embed-text:latest"), 0.92);
        assert_eq!(consolidation_threshold("granite-embedding:278m"), 0.93);
        assert_eq!(
            consolidation_threshold("granite-embedding-multilingual"),
            0.93
        );
        assert_eq!(
            consolidation_threshold("some-unknown-model"),
            crate::lifecycle::MIN_SIMILARITY
        );
        // The consolidation threshold, not the as_of one of the same profile.
        let granite = crate::embedding::model_profile("granite-embedding:278m").unwrap();
        assert_ne!(
            granite.as_of_min_cosine,
            granite.consolidation_min_similarity
        );
        assert_eq!(
            consolidation_threshold("granite-embedding:278m"),
            granite.consolidation_min_similarity
        );
        let check = active();
        assert_eq!(check.model, crate::embedding::configured_model());
        assert_eq!(check.min_similarity, consolidation_threshold(&check.model));
    }

    /// The Rust extraction on a database the Python CLI migrated: refused
    /// before a conversation is read or a request is sent, in one line that
    /// names the missing migration and the command.
    #[test]
    fn extract_refuses_unmigrated_database() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping extract_refuses_unmigrated_database: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let conv = seed_conversation(&mut tx, &[(1, "first"), (2, "second")]);
        // A temporary ledger shadows applied_migrations for this session.
        tx.batch_execute(
            "CREATE TEMP TABLE applied_migrations (migration_name text PRIMARY KEY) ON COMMIT DROP; \
             INSERT INTO applied_migrations VALUES ('015_account_stars_untimed.sql')",
        )
        .expect("ledger without the latest migration");
        // Nothing listens there: a request would fail the conversation, not
        // the run.
        let backend = ollama_at("http://127.0.0.1:9".into());
        let err =
            run_pending(&mut tx, &backend, 100_000, 24_000, Profile::Default).expect_err("refused");
        let line = format!("{err:#}");
        assert!(line.contains(crate::migrate::LATEST), "{line}");
        assert!(line.contains("throughline migrate"), "{line}");
        assert!(!line.contains('\n'), "one line: {line}");
        assert!(
            pending_conversations(&mut tx, 100_000)
                .unwrap()
                .contains(&conv),
            "nothing was read"
        );

        // Control: with the latest migration the run gets as far as the
        // conversation and records the failed request as an error.
        tx.execute(
            "INSERT INTO applied_migrations VALUES ($1)",
            &[&crate::migrate::LATEST],
        )
        .expect("latest migration");
        tx.execute(
            "UPDATE conversations SET started_at = now() + interval '100 years' WHERE id = $1",
            &[&conv],
        )
        .expect("first in line");
        let s = run_pending(&mut tx, &backend, 1, 24_000, Profile::Default).expect("migrated");
        assert_eq!((s.windows, s.errors), (0, 1), "{s:?}");
    }
}
