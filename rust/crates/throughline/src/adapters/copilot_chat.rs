//! GitHub Copilot Chat (VS Code) adapter:
//! `<user dir>/workspaceStorage/<hash>/chatSessions/<id>.json` and
//! `<user dir>/globalStorage/emptyWindowChatSessions/<id>.jsonl`, where the
//! user dir is `~/Library/Application Support/Code/User` on macOS and
//! `~/.config/Code/User` on Linux.
//!
//! Two file shapes, told apart by the extension:
//! - `.json`: the session as one object (`version`, `sessionId`,
//!   `creationDate` in ms, `requests[]`).
//! - `.jsonl`: an operation log, replayed top to bottom. Each line is
//!   `{"kind": <int>, ...}` with a path `k` (keys and array indexes from the
//!   session root) where a kind needs one:
//!   - 0: `v` is the whole session and replaces the state;
//!   - 1 (set): the property at `k` becomes `v`;
//!   - 2 (push): the array at `k` (empty when missing) is first cut to
//!     length `i` when `i` is given, then `v[]` is appended;
//!   - 3 (delete): the property at `k` is removed.
//!
//!   An array index in `k` may name an element or the slot right after the
//!   last one, and `i` may not exceed the array's length. A larger or
//!   malformed index makes the line unresolvable, so a stray value never
//!   allocates (VS Code itself writes only indexes in range).
//!
//!   VS Code writes a fresh kind-0 line only for a new log and when it
//!   compacts one, so most requests arrive as push and set lines. Lines with
//!   another kind, lines before the first snapshot and lines whose path does
//!   not resolve are skipped; the conversation metadata counts
//!   `log_lines_replayed` and `log_lines_ignored`. The replay rules were read
//!   in `objectMutationLog.ts` (commit below) and written anew; no code was
//!   copied.
//!
//! Status: the JSONL line shape and the snapshot fields (`version`,
//! `creationDate`, `responderUsername`, `sessionId`, `requests`,
//! `pendingRequests`, `inputState`) were confirmed on one real file that held
//! no requests. The request and response fields follow community notes and
//! the VS Code source at commit 1306698e328e5e531f5e632301d30020aa994eff
//! (`src/vs/workbench/contrib/chat/common/model/chatModel.ts`,
//! `model/objectMutationLog.ts`, `chatService/chatService.ts`); they are
//! unverified on real data.
//!
//! Each request becomes two turns. `message` (a string, or an object with
//! `text`) is the user turn at `timestamp` (ms). `response[]` is the assistant
//! turn at `responseTimestamp` (ms), else at the request's time. Markdown parts
//! (`{"value"}` without a kind, `markdownContent`, `markdownVuln`) are kept,
//! an `inlineReference` becomes its name in backticks (as VS Code copies it),
//! `toolInvocationSerialized` becomes a tool call that holds the tool's
//! display message (`invocation_message`, not its arguments, which the file
//! does not keep), and `thinking` and every other kind are dropped. A request
//! with `hiddenFromTranscript` is skipped; with `requestHiddenFromTranscript`
//! or `isSystemInitiated` only its user turn is skipped, since the user did
//! not type it.

use super::{stable_uuid_str, Adapter};
use crate::types::{NormalisedConversation, NormalisedMessage};
use anyhow::Result;
use chrono::{DateTime, TimeZone, Utc};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use uuid::Uuid;

const NS: Uuid = Uuid::from_u128(0x0695fc52_73f4_4de2_ab98_207b7516e8bf);

/// Project of sessions with no folder (an empty VS Code window).
const NO_FOLDER: &str = "copilot_chat";

pub struct CopilotChatAdapter {
    home: PathBuf,
}

impl Default for CopilotChatAdapter {
    fn default() -> Self {
        Self::new(dirs::config_dir().unwrap_or_default().join("Code/User"))
    }
}

impl CopilotChatAdapter {
    pub fn new(home: PathBuf) -> Self {
        Self { home }
    }
}

fn push_session_files(dir: &Path, out: &mut Vec<PathBuf>) -> Result<()> {
    if !dir.is_dir() {
        return Ok(());
    }
    for entry in std::fs::read_dir(dir)? {
        let path = entry?.path();
        let ext = path.extension().and_then(|e| e.to_str());
        if path.is_file() && matches!(ext, Some("json" | "jsonl")) {
            out.push(path);
        }
    }
    Ok(())
}

/// Result of replaying an operation log.
struct Log {
    session: Option<Value>,
    replayed: u64,
    ignored: u64,
}

/// Replay an operation log: the last kind-0 snapshot plus the set, push and
/// delete lines after it.
fn read_log(raw: &str) -> Log {
    let mut log = Log {
        session: None,
        replayed: 0,
        ignored: 0,
    };
    for line in raw.lines().filter(|l| !l.trim().is_empty()) {
        let entry = serde_json::from_str::<Value>(line).unwrap_or(Value::Null);
        let kind = entry.get("kind").and_then(Value::as_i64);
        if kind == Some(0) {
            log.session = entry.get("v").cloned();
            continue;
        }
        let applied = match (kind, log.session.as_mut()) {
            (Some(k @ 1..=3), Some(state)) => apply_mutation(state, k, &entry),
            _ => None,
        };
        if applied.is_some() {
            log.replayed += 1;
        } else {
            log.ignored += 1;
        }
    }
    log
}

fn key_name(key: &Value) -> Option<String> {
    match key {
        Value::String(s) => Some(s.clone()),
        Value::Number(n) => Some(n.to_string()),
        _ => None,
    }
}

fn key_index(key: &Value) -> Option<usize> {
    match key {
        Value::Number(n) => n.as_u64().and_then(|n| usize::try_from(n).ok()),
        Value::String(s) => s.parse().ok(),
        _ => None,
    }
}

/// The child of an object or array at `key`; with `create`, a missing object
/// key is added as null and an array grows by one null when `key` is its
/// length. Any index past that does not resolve: VS Code writes only indexes
/// in range, and a stray huge one must not allocate.
fn child<'a>(node: &'a mut Value, key: &Value, create: bool) -> Option<&'a mut Value> {
    match node {
        Value::Object(map) => {
            let name = key_name(key)?;
            if create {
                Some(map.entry(name).or_insert(Value::Null))
            } else {
                map.get_mut(&name)
            }
        }
        Value::Array(items) => {
            let i = key_index(key)?;
            if create && i == items.len() {
                items.push(Value::Null);
            }
            items.get_mut(i)
        }
        _ => None,
    }
}

/// Apply one set (1), push (2) or delete (3) line. `None` when its path does
/// not resolve.
fn apply_mutation(state: &mut Value, kind: i64, entry: &Value) -> Option<()> {
    let path = entry.get("k").and_then(Value::as_array)?;
    let (last, parents) = path.split_last()?;
    let mut node = state;
    for key in parents {
        node = child(node, key, false)?;
    }
    match kind {
        1 => *child(node, last, true)? = entry.get("v").cloned().unwrap_or(Value::Null),
        2 => {
            // An `i` that is not an index rejects the line before any change.
            let cut = match entry.get("i") {
                None | Some(Value::Null) => None,
                Some(i) => Some(key_index(i)?),
            };
            let slot = child(node, last, true)?;
            if slot.is_null() {
                *slot = Value::Array(Vec::new());
            }
            let items = slot.as_array_mut()?;
            if let Some(cut) = cut {
                // Only ever shorten; a cut past the end does not resolve.
                if cut > items.len() {
                    return None;
                }
                items.truncate(cut);
            }
            if let Some(Value::Array(values)) = entry.get("v") {
                items.extend(values.iter().cloned());
            }
        }
        _ => match node {
            Value::Object(map) => {
                map.remove(&key_name(last)?);
            }
            Value::Array(items) => *items.get_mut(key_index(last)?)? = Value::Null,
            _ => return None,
        },
    }
    Some(())
}

fn ms_ts(v: Option<&Value>) -> Option<DateTime<Utc>> {
    let v = v?;
    let ms = v.as_i64().or_else(|| v.as_f64().map(|f| f as i64))?;
    if ms <= 0 {
        return None;
    }
    Utc.timestamp_millis_opt(ms).single()
}

/// A string or a markdown object (`{"value": ...}`).
fn text_of(v: Option<&Value>) -> &str {
    match v {
        Some(Value::String(s)) => s,
        Some(other) => other
            .get("value")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        None => "",
    }
}

/// The assistant text and tool calls of one response.
fn response_parts(response: Option<&Value>) -> (String, Vec<Value>) {
    let mut text = String::new();
    let mut calls = Vec::new();
    for part in response.and_then(Value::as_array).into_iter().flatten() {
        match part.get("kind").and_then(Value::as_str) {
            None => text.push_str(text_of(Some(part))),
            Some("markdownContent" | "markdownVuln") => text.push_str(text_of(part.get("content"))),
            Some("inlineReference") => text.push_str(&inline_reference(part)),
            Some("toolInvocationSerialized") => {
                let name = part.get("toolId").and_then(Value::as_str).unwrap_or("?");
                let message = text_of(part.get("invocationMessage")).trim();
                if !text.is_empty() && !text.ends_with('\n') {
                    text.push('\n');
                }
                text.push_str(format!("[Tool: {name}] {message}").trim_end());
                text.push('\n');
                calls.push(json!({
                    "tool_name": name,
                    "tool_call_id": part.get("toolCallId"),
                    "invocation_message": message,
                }));
            }
            _ => {}
        }
    }
    (text.trim().to_string(), calls)
}

/// An inline reference as VS Code copies it: a location or URI by its file
/// name, a symbol by its name, in backticks.
fn inline_reference(part: &Value) -> String {
    let Some(target) = part.get("inlineReference") else {
        return String::new();
    };
    let uri = target.get("uri").unwrap_or(target);
    let name = match (
        target.get("uri"),
        target.get("name").and_then(Value::as_str),
    ) {
        (None, Some(symbol)) => Some(symbol),
        _ => uri
            .get("path")
            .and_then(Value::as_str)
            .and_then(|p| p.rsplit('/').next()),
    };
    name.filter(|n| !n.is_empty())
        .map(|n| format!("`{n}`"))
        .unwrap_or_default()
}

fn flag(request: &Value, key: &str) -> bool {
    request.get(key).and_then(Value::as_bool).unwrap_or(false)
}

/// Path of a `file://` URI, percent-decoded.
fn file_uri_path(uri: &str) -> Option<String> {
    let rest = uri.strip_prefix("file://")?;
    let bytes = rest.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        let escaped = (bytes[i] == b'%')
            .then(|| rest.get(i + 1..i + 3))
            .flatten()
            .and_then(|h| u8::from_str_radix(h, 16).ok());
        match escaped {
            Some(b) => {
                out.push(b);
                i += 3;
            }
            None => {
                out.push(bytes[i]);
                i += 1;
            }
        }
    }
    String::from_utf8(out).ok().filter(|p| !p.is_empty())
}

/// The session's working directory, else the folder of its VS Code
/// workspace (`workspace.json` next to `chatSessions`).
fn project_path(path: &Path, session: &Value) -> String {
    let working_dir = session
        .get("workingDirectory")
        .and_then(Value::as_str)
        .and_then(file_uri_path);
    let workspace_folder = || {
        let sessions_dir = path.parent()?;
        if sessions_dir.file_name()? != "chatSessions" {
            return None;
        }
        let raw = std::fs::read_to_string(sessions_dir.parent()?.join("workspace.json")).ok()?;
        let meta: Value = serde_json::from_str(&raw).ok()?;
        meta.get("folder")
            .and_then(Value::as_str)
            .and_then(file_uri_path)
    };
    working_dir
        .or_else(workspace_folder)
        .unwrap_or_else(|| NO_FOLDER.to_string())
}

impl Adapter for CopilotChatAdapter {
    fn name(&self) -> &str {
        "copilot_chat"
    }

    fn home(&self) -> PathBuf {
        self.home.clone()
    }

    fn discover(&self) -> Result<Vec<PathBuf>> {
        let mut out = Vec::new();
        let workspaces = self.home.join("workspaceStorage");
        if workspaces.is_dir() {
            // Only `chatSessions`: the rest of each folder belongs to other
            // extensions.
            for ws in std::fs::read_dir(&workspaces)? {
                push_session_files(&ws?.path().join("chatSessions"), &mut out)?;
            }
        }
        push_session_files(
            &self.home.join("globalStorage/emptyWindowChatSessions"),
            &mut out,
        )?;
        out.sort();
        Ok(out)
    }

    fn parse(&self, path: &Path) -> Result<Option<NormalisedConversation>> {
        let raw = std::fs::read_to_string(path)?;
        let is_log = path.extension().is_some_and(|e| e == "jsonl");
        let log = if is_log {
            read_log(&raw)
        } else {
            Log {
                session: serde_json::from_str::<Value>(&raw).ok(),
                replayed: 0,
                ignored: 0,
            }
        };
        let (session, replayed, ignored) = (log.session, log.replayed, log.ignored);
        let Some(session) = session.filter(Value::is_object) else {
            return Ok(None);
        };
        let requests: &[Value] = session
            .get("requests")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or_default();

        let raw_session_id = session
            .get("sessionId")
            .and_then(Value::as_str)
            .map(str::to_string)
            .or_else(|| path.file_stem().map(|s| s.to_string_lossy().into_owned()))
            .unwrap_or_default();
        let session_id = stable_uuid_str(&NS, Some(&raw_session_id), "copilot_chat:session");
        let started_at = ms_ts(session.get("creationDate"))
            .or_else(|| requests.iter().find_map(|r| ms_ts(r.get("timestamp"))))
            .or_else(|| {
                std::fs::metadata(path)
                    .and_then(|m| m.modified())
                    .ok()
                    .map(DateTime::<Utc>::from)
            })
            .unwrap_or_else(|| DateTime::<Utc>::from(std::time::UNIX_EPOCH));

        let mut messages = Vec::new();
        let (mut tokens_in, mut tokens_out) = (0i64, 0i64);
        let mut last_ts = started_at;
        for (idx, request) in requests.iter().enumerate() {
            if flag(request, "hiddenFromTranscript") {
                continue;
            }
            let key = request
                .get("requestId")
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(|| format!("#{idx}"));
            let uuid = |turn: &str| {
                stable_uuid_str(
                    &NS,
                    Some(&format!("{key}:{turn}")),
                    &format!("copilot_chat:{raw_session_id}"),
                )
            };
            let asked_at = ms_ts(request.get("timestamp")).unwrap_or(last_ts);
            last_ts = asked_at;
            let question = match request.get("message") {
                Some(Value::String(s)) => s.as_str(),
                Some(m) => m.get("text").and_then(Value::as_str).unwrap_or_default(),
                None => "",
            };
            let typed_by_user = !flag(request, "requestHiddenFromTranscript")
                && !flag(request, "isSystemInitiated");
            if typed_by_user && !question.trim().is_empty() {
                messages.push(NormalisedMessage {
                    role: "user".into(),
                    content: question.trim().to_string(),
                    created_at: Some(asked_at),
                    uuid: Some(uuid("request")),
                    metadata: json!({}),
                    ..Default::default()
                });
            }
            let (answer, calls) = response_parts(request.get("response"));
            if answer.is_empty() && calls.is_empty() {
                continue;
            }
            let get_i64 = |k: &str| request.get(k).and_then(Value::as_i64).unwrap_or(0);
            tokens_in += get_i64("promptTokens");
            tokens_out += get_i64("completionTokens");
            let tool_name = calls
                .first()
                .and_then(|c| c["tool_name"].as_str())
                .map(str::to_string);
            messages.push(NormalisedMessage {
                role: "assistant".into(),
                content: answer,
                tool_name,
                tool_calls: (!calls.is_empty()).then_some(Value::Array(calls)),
                created_at: Some(ms_ts(request.get("responseTimestamp")).unwrap_or(asked_at)),
                model: request
                    .get("modelId")
                    .and_then(Value::as_str)
                    .map(str::to_string),
                uuid: Some(uuid("response")),
                metadata: json!({}),
                ..Default::default()
            });
        }
        if messages.is_empty() {
            return Ok(None);
        }

        let ended_at = messages
            .iter()
            .filter_map(|m| m.created_at)
            .fold(started_at, |acc, t| acc.max(t));
        let model = messages
            .iter()
            .find(|m| m.role == "assistant")
            .and_then(|m| m.model.clone());
        let summary = session
            .get("customTitle")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|t| !t.is_empty())
            .map(str::to_string);
        let pending = session
            .get("pendingRequests")
            .and_then(Value::as_array)
            .map_or(0, Vec::len);

        Ok(Some(NormalisedConversation {
            session_id,
            project_path: Some(project_path(path, &session)),
            model,
            entrypoint: None,
            git_branch: None,
            started_at,
            ended_at: Some(ended_at),
            messages,
            token_count_in: (tokens_in > 0).then_some(tokens_in),
            token_count_out: (tokens_out > 0).then_some(tokens_out),
            summary,
            metadata: json!({
                "source": "copilot_chat",
                "format": if is_log { "jsonl" } else { "json" },
                "version": session.get("version"),
                "log_lines_replayed": replayed,
                "log_lines_ignored": ignored,
                "pending_requests": pending,
            }),
            source_tool: Some("copilot_chat".into()),
        }))
    }
}

#[cfg(test)]
mod tests {
    //! Invented fixtures. The JSONL line shape (`{"kind": <int>, "v": ...}`
    //! with the whole session in the first line) was checked against one real
    //! file that held no requests. The mutation lines and the request and
    //! response fields follow the VS Code source at commit
    //! 1306698e328e5e531f5e632301d30020aa994eff and are unverified on real
    //! data.

    use super::*;
    use chrono::{DateTime, Utc};
    use serde_json::{json, Value};
    use uuid::Uuid;

    fn ms(s: &str) -> i64 {
        ts(s).timestamp_millis()
    }

    fn ts(s: &str) -> DateTime<Utc> {
        DateTime::parse_from_rfc3339(s).unwrap().with_timezone(&Utc)
    }

    fn write(path: &Path, body: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, body).unwrap();
    }

    fn lines(entries: &[Value]) -> String {
        entries.iter().map(|e| format!("{e}\n")).collect()
    }

    fn contents(conv: &NormalisedConversation) -> Vec<&str> {
        conv.messages.iter().map(|m| m.content.as_str()).collect()
    }

    #[test]
    fn copilot_json_session_requests_become_turns() {
        let tmp = tempfile::tempdir().unwrap();
        let ws = tmp.path().join("workspaceStorage/5f0e9d8c7b6a");
        write(
            &ws.join("workspace.json"),
            r#"{"folder": "file:///home/dev/demo%20app"}"#,
        );
        // Other extensions keep their state next to the chat sessions.
        write(&ws.join("state.vscdb"), "not a chat");
        let session = "1d2e3f40-7777-4a2b-9c3d-000000000007";
        let data = json!({
            "version": 3,
            "sessionId": session,
            "creationDate": ms("2026-01-03T09:00:00Z"),
            "requests": [
                {
                    "requestId": "request_1",
                    "timestamp": ms("2026-01-03T09:00:05Z"),
                    "message": {"text": "how do I parse json", "parts": []},
                    "response": [
                        {"value": "Use serde_json in "},
                        {"kind": "inlineReference",
                         "inlineReference": {"uri": {"scheme": "file", "path": "/home/dev/demo app/src/main.rs"},
                                             "range": {"startLineNumber": 3, "endLineNumber": 3}}},
                        {"value": "."},
                        {"kind": "thinking", "value": "hidden reasoning"},
                        {"kind": "toolInvocationSerialized", "toolId": "copilot_readFile",
                         "toolCallId": "call_1", "invocationMessage": "Reading Cargo.toml",
                         "isComplete": true},
                        {"kind": "markdownContent", "content": {"value": " Add it to Cargo.toml."}}
                    ],
                    "responseTimestamp": ms("2026-01-03T09:00:09Z"),
                    "modelId": "copilot/test-model",
                    "promptTokens": 50,
                    "completionTokens": 12
                },
                {
                    "requestId": "request_2",
                    "timestamp": ms("2026-01-03T09:01:00Z"),
                    "message": "an old plain-string message",
                    "response": [{"value": "ok"}]
                },
                {
                    "requestId": "request_3",
                    "timestamp": ms("2026-01-03T09:02:00Z"),
                    "hiddenFromTranscript": true,
                    "message": {"text": "hidden question"},
                    "response": [{"value": "hidden answer"}]
                },
                {
                    "requestId": "request_4",
                    "timestamp": ms("2026-01-03T09:03:00Z"),
                    "isSystemInitiated": true,
                    "message": {"text": "prompt sent by the editor"},
                    "response": [{"value": "answer to the editor"}]
                }
            ]
        });
        let path = ws.join(format!("chatSessions/{session}.json"));
        write(&path, &data.to_string());

        let adapter = CopilotChatAdapter::new(tmp.path().to_path_buf());
        assert_eq!(adapter.discover().unwrap(), vec![path.clone()]);
        let conv = adapter.parse(&path).unwrap().expect("parsed");

        assert_eq!(conv.session_id, Uuid::parse_str(session).unwrap());
        assert_eq!(conv.source_tool.as_deref(), Some("copilot_chat"));
        assert_eq!(conv.project_path.as_deref(), Some("/home/dev/demo app"));
        let roles: Vec<&str> = conv.messages.iter().map(|m| m.role.as_str()).collect();
        assert_eq!(
            roles,
            ["user", "assistant", "user", "assistant", "assistant"]
        );
        assert_eq!(
            contents(&conv),
            [
                "how do I parse json",
                "Use serde_json in `main.rs`.\n[Tool: copilot_readFile] Reading Cargo.toml\n Add it to Cargo.toml.",
                "an old plain-string message",
                "ok",
                "answer to the editor"
            ]
        );
        let calls = conv.messages[1].tool_calls.as_ref().expect("tool calls");
        assert_eq!(calls[0]["tool_name"], "copilot_readFile");
        assert_eq!(calls[0]["invocation_message"], "Reading Cargo.toml");
        assert!(calls[0].get("input").is_none(), "no arguments are stored");
        assert_eq!(
            conv.messages[1].tool_name.as_deref(),
            Some("copilot_readFile")
        );
        assert_eq!(
            conv.messages[1].model.as_deref(),
            Some("copilot/test-model")
        );
        assert_eq!(
            conv.messages[0].created_at,
            Some(ts("2026-01-03T09:00:05Z"))
        );
        assert_eq!(
            conv.messages[1].created_at,
            Some(ts("2026-01-03T09:00:09Z"))
        );
        // Without a response time the answer takes the request's time.
        assert_eq!(
            conv.messages[3].created_at,
            Some(ts("2026-01-03T09:01:00Z"))
        );
        assert_eq!(conv.started_at, ts("2026-01-03T09:00:00Z"));
        assert_eq!(conv.ended_at, Some(ts("2026-01-03T09:03:00Z")));
        assert_eq!(conv.token_count_in, Some(50));
        assert_eq!(conv.token_count_out, Some(12));
        assert!(conv.messages.iter().all(|m| m.uuid.is_some()));
    }

    #[test]
    fn copilot_jsonl_snapshot_line_is_read_and_patches_counted() {
        let tmp = tempfile::tempdir().unwrap();
        let session = "2e3f4051-8888-4a2b-9c3d-000000000008";
        let snapshot = json!({"kind": 0, "v": {
            "version": 3,
            "creationDate": ms("2026-01-04T08:00:00Z"),
            "responderUsername": "Copilot",
            "sessionId": session,
            "requests": [{
                "requestId": "request_a",
                "timestamp": ms("2026-01-04T08:00:03Z"),
                "message": {"text": "hello there", "parts": []},
                "response": [{"value": "Hi."}]
            }],
            "pendingRequests": [],
            "inputState": {"inputText": ""}
        }});
        let path = tmp.path().join(format!(
            "globalStorage/emptyWindowChatSessions/{session}.jsonl"
        ));
        write(
            &path,
            &lines(&[
                snapshot.clone(),
                json!({"kind": 1, "k": ["customTitle"], "v": "Greeting"}),
                json!({"kind": 7, "k": ["requests"], "v": []}),
            ]),
        );

        let adapter = CopilotChatAdapter::new(tmp.path().to_path_buf());
        assert_eq!(adapter.discover().unwrap(), vec![path.clone()]);
        let conv = adapter.parse(&path).unwrap().expect("parsed");

        assert_eq!(conv.session_id, Uuid::parse_str(session).unwrap());
        assert_eq!(contents(&conv), ["hello there", "Hi."]);
        assert_eq!(conv.summary.as_deref(), Some("Greeting"));
        assert_eq!(conv.metadata["log_lines_replayed"], json!(1));
        // An unknown kind is counted, not applied.
        assert_eq!(conv.metadata["log_lines_ignored"], json!(1));
        assert_eq!(conv.started_at, ts("2026-01-04T08:00:00Z"));
        // An empty window has no folder; the synthetic project stands in.
        assert_eq!(conv.project_path.as_deref(), Some("copilot_chat"));

        // A snapshot without requests yields no conversation.
        let empty = tmp
            .path()
            .join("globalStorage/emptyWindowChatSessions/empty.jsonl");
        let mut v: Value = snapshot.clone();
        v["v"]["requests"] = json!([]);
        write(&empty, &format!("{v}\n"));
        assert!(adapter.parse(&empty).unwrap().is_none());
    }

    #[test]
    fn copilot_jsonl_mutations_are_replayed_in_order() {
        let tmp = tempfile::tempdir().unwrap();
        let session = "3f405162-9999-4a2b-9c3d-000000000009";
        let request = |id: &str, text: &str, at: &str, answer: &[&str]| {
            let parts: Vec<Value> = answer.iter().map(|a| json!({"value": a})).collect();
            json!({"requestId": id, "timestamp": ms(at), "modelId": "copilot/test-model",
                   "message": {"text": text, "parts": []}, "response": parts})
        };
        let path = tmp.path().join(format!(
            "globalStorage/emptyWindowChatSessions/{session}.jsonl"
        ));
        write(
            &path,
            &lines(&[
                // Before any snapshot: nothing to apply to.
                json!({"kind": 2, "k": ["requests"], "v": [request("early", "lost", "2026-01-05T07:00:00Z", &[])]}),
                // The snapshot as VS Code starts a session: no requests yet.
                json!({"kind": 0, "v": {"version": 3, "sessionId": session,
                       "creationDate": ms("2026-01-05T08:00:00Z"), "requests": [],
                       "pendingRequests": [], "inputState": {}}}),
                json!({"kind": 2, "k": ["requests"],
                       "v": [request("r1", "first question", "2026-01-05T08:00:05Z", &[])]}),
                json!({"kind": 1, "k": ["requests", 0, "response"], "v": [{"value": "first answer draft"}]}),
                json!({"kind": 2, "k": ["requests", 0, "response"], "v": [{"value": " and more"}]}),
                json!({"kind": 3, "k": ["requests", 0, "modelId"]}),
                json!({"kind": 2, "k": ["requests"],
                       "v": [request("r2", "second question", "2026-01-05T08:01:00Z", &["second answer"])]}),
                // `i` cuts the array to one entry before the push.
                json!({"kind": 2, "k": ["requests"], "i": 1,
                       "v": [request("r3", "third question", "2026-01-05T08:02:00Z", &["third answer"])]}),
                json!({"kind": 1, "k": ["requests", 1, "responseTimestamp"],
                       "v": ms("2026-01-05T08:02:30Z")}),
                // A path that does not resolve is skipped.
                json!({"kind": 1, "k": ["requests", 5, "message"], "v": "nowhere"}),
            ]),
        );

        let conv = CopilotChatAdapter::new(tmp.path().to_path_buf())
            .parse(&path)
            .unwrap()
            .expect("parsed");
        assert_eq!(
            contents(&conv),
            [
                "first question",
                "first answer draft and more",
                "third question",
                "third answer"
            ]
        );
        assert_eq!(conv.messages[1].model, None, "deleted by the kind-3 line");
        assert_eq!(
            conv.messages[3].model.as_deref(),
            Some("copilot/test-model")
        );
        assert_eq!(
            conv.messages[3].created_at,
            Some(ts("2026-01-05T08:02:30Z"))
        );
        assert_eq!(conv.metadata["log_lines_replayed"], json!(7));
        assert_eq!(conv.metadata["log_lines_ignored"], json!(2));
    }

    #[test]
    fn copilot_replay_rejects_out_of_range_indexes() {
        let request = |id: &str, text: &str| {
            json!({"requestId": id, "timestamp": ms("2026-01-06T08:00:05Z"),
                   "message": {"text": text}, "response": [{"value": "answer"}]})
        };
        let snapshot = json!({"kind": 0, "v": {"version": 3, "sessionId": "s",
                              "creationDate": ms("2026-01-06T08:00:00Z"),
                              "requests": [request("r1", "kept")]}});
        // Each line names an index the array does not have, or no index at all.
        // None may grow the array, panic, or change it.
        let bad = [
            json!({"kind": 1, "k": ["requests", u64::MAX], "v": 1}),
            json!({"kind": 1, "k": ["requests", "18446744073709551615"], "v": 1}),
            json!({"kind": 1, "k": ["requests", 20_000_000], "v": 1}),
            json!({"kind": 1, "k": ["requests", -1], "v": 1}),
            json!({"kind": 1, "k": ["requests", 1.5], "v": 1}),
            json!({"kind": 2, "k": ["requests", 20_000_000], "v": [1]}),
            json!({"kind": 2, "k": ["requests"], "i": u64::MAX, "v": []}),
            json!({"kind": 2, "k": ["requests"], "i": 20_000_000, "v": []}),
            json!({"kind": 2, "k": ["requests"], "i": -1, "v": []}),
            json!({"kind": 2, "k": ["requests"], "i": 0.5, "v": []}),
            json!({"kind": 3, "k": ["requests", u64::MAX]}),
        ];
        let mut entries = vec![snapshot];
        entries.extend(bad.iter().cloned());
        // In range: index == len appends one element; `i` == len cuts nothing.
        entries.push(json!({"kind": 1, "k": ["requests", 1], "v": request("r2", "appended")}));
        entries.push(json!({"kind": 2, "k": ["requests"], "i": 2, "v": []}));

        let log = read_log(&lines(&entries));
        let session = log.session.expect("snapshot");
        let requests = session["requests"].as_array().expect("requests");
        assert_eq!(requests.len(), 2, "no element beyond len + 1");
        assert_eq!(requests[0]["message"]["text"], "kept");
        assert_eq!(requests[1]["message"]["text"], "appended");
        assert_eq!(log.ignored, bad.len() as u64);
        assert_eq!(log.replayed, 2);
    }
}
