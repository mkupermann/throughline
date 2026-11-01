//! Gemini CLI adapter: `~/.gemini/tmp/<project>/chats/session-*.jsonl` and
//! the older `session-*.json`.
//!
//! Built against the session recorder of google-gemini/gemini-cli at commit
//! 6814e00d6b148c3a1d57244d5d7040f7c541009d
//! (`packages/core/src/services/chatRecordingService.ts`,
//! `chatRecordingTypes.ts`, `packages/core/src/config/projectRegistry.ts`).
//! The rules below were read there and written anew in Rust; no code was
//! copied. No real session file was available, so the adapter is unverified
//! on real data.
//!
//! A session file is a log of JSON lines, replayed top to bottom:
//! - a metadata line (`sessionId`, `projectHash`, `startTime`, `lastUpdated`,
//!   `kind`, `summary`) merges into the session metadata;
//! - a message line (a string `id`) is appended, or replaces the message with
//!   the same id where it stands (the recorder writes a message again when it
//!   adds tokens or tool calls);
//! - `{"$set": {...}}` merges into the metadata; a `messages` array in it is a
//!   legacy checkpoint and replaces every message;
//! - `{"$rewindTo": "<id>"}` drops that message and all after it, and drops
//!   every message when the id is unknown;
//! - `{"$patch": {...}}` applies, in this order: `id` with `content` and
//!   `toolCalls[].result`, the same for each entry of `updates`, then
//!   `removeIds`, then `orderIds`, which moves the listed messages, in that
//!   order, behind the unlisted ones.
//!
//! When the replay leaves no `sessionId` and `projectHash`, the whole file is
//! read as one legacy JSON object with a `messages` array. Roles: `user` →
//! user, `gemini` → assistant; `info`, `warning` and `error` lines are
//! notices of the CLI and are skipped, and thoughts are dropped. A user turn
//! takes `displayContent` (what was typed) when the recorder kept one, since
//! `content` then holds the expanded `@file` text. Tool output comes back as
//! user records of `functionResponse` parts; they hold no text and are
//! skipped. Subagent sessions (`kind: "subagent"`, written to
//! `chats/<parent session id>/`) are skipped as with Claude Code.
//!
//! Project folders under `tmp/` were once named by the SHA-256 hex of the
//! project root and are now named by a slug with a `.project_root` marker.
//! The CLI migrates by copying the old folder and keeps it (`storage.ts`,
//! `storageMigration.ts`), so a session can exist twice. Both are discovered;
//! the copy in the hash folder is skipped (the runner counts it as skipped)
//! when a slug folder holds a session file of the same name with the same
//! `sessionId`. A session left only in a hash folder takes its project root
//! from the slug folder whose marker hashes to that name.

use super::{stable_uuid_str, Adapter};
use crate::types::{NormalisedConversation, NormalisedMessage};
use anyhow::Result;
use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use uuid::Uuid;

const NS: Uuid = Uuid::from_u128(0x3079e2ce_737a_4a08_9e35_44fade21940a);

pub struct GeminiCliAdapter {
    home: PathBuf,
}

impl Default for GeminiCliAdapter {
    fn default() -> Self {
        Self::new(dirs::home_dir().unwrap_or_default().join(".gemini/tmp"))
    }
}

impl GeminiCliAdapter {
    pub fn new(home: PathBuf) -> Self {
        Self { home }
    }
}

fn is_session_file(path: &Path) -> bool {
    let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
        return false;
    };
    name.starts_with("session-") && (name.ends_with(".jsonl") || name.ends_with(".json"))
}

/// The state of one session file after its lines were replayed.
#[derive(Default)]
struct Replay {
    meta: Map<String, Value>,
    /// `sessionId` of the first metadata line, which later `$set` lines
    /// could change.
    first_session_id: Option<String>,
    messages: Vec<(String, Value)>,
}

impl Replay {
    fn position(&self, id: &str) -> Option<usize> {
        self.messages.iter().position(|(m, _)| m == id)
    }

    fn upsert(&mut self, message: &Value) {
        let Some(id) = message.get("id").and_then(Value::as_str) else {
            return;
        };
        match self.position(id) {
            Some(i) => self.messages[i].1 = message.clone(),
            None => self.messages.push((id.to_string(), message.clone())),
        }
    }

    fn upsert_all(&mut self, list: Option<&Value>) {
        for m in list.and_then(Value::as_array).into_iter().flatten() {
            if is_message(m) {
                self.upsert(m);
            }
        }
    }

    fn line(&mut self, record: &Value) {
        let Some(obj) = record.as_object() else {
            return;
        };
        if let Some(id) = obj.get("$rewindTo").and_then(Value::as_str) {
            match self.position(id) {
                Some(i) => self.messages.truncate(i),
                None => self.messages.clear(),
            }
        } else if let Some(patch) = obj.get("$patch").and_then(Value::as_object) {
            self.patch(patch);
        } else if is_message(record) {
            self.upsert(record);
        } else if let Some(set) = obj.get("$set").and_then(Value::as_object) {
            if set.get("messages").is_some_and(Value::is_array) {
                self.messages.clear();
                self.upsert_all(set.get("messages"));
            }
            self.meta.extend(set.clone());
        } else if str_field(obj, "sessionId").is_some() && str_field(obj, "projectHash").is_some() {
            if self.first_session_id.is_none() {
                self.first_session_id = str_field(obj, "sessionId").map(str::to_string);
            }
            self.meta.extend(obj.clone());
            self.upsert_all(obj.get("messages"));
        }
    }

    fn patch(&mut self, patch: &Map<String, Value>) {
        if str_field(patch, "id").is_some() {
            self.patch_one(patch);
        }
        for update in patch
            .get("updates")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            if let Some(update) = update.as_object().filter(|u| str_field(u, "id").is_some()) {
                self.patch_one(update);
            }
        }
        for id in patch
            .get("removeIds")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            if let Some(id) = id.as_str() {
                self.messages.retain(|(m, _)| m != id);
            }
        }
        if let Some(order) = patch.get("orderIds").and_then(Value::as_array) {
            let mut moved = Vec::new();
            for id in order.iter().filter_map(Value::as_str) {
                if let Some(i) = self.position(id) {
                    moved.push(self.messages.remove(i));
                }
            }
            self.messages.extend(moved);
        }
    }

    fn patch_one(&mut self, patch: &Map<String, Value>) {
        let Some(i) = str_field(patch, "id").and_then(|id| self.position(id)) else {
            return;
        };
        let Some(message) = self.messages[i].1.as_object_mut() else {
            return;
        };
        if let Some(content) = patch.get("content") {
            message.insert("content".into(), content.clone());
        }
        if message.get("type").and_then(Value::as_str) != Some("gemini") {
            return;
        }
        let Some(calls) = message.get_mut("toolCalls").and_then(Value::as_array_mut) else {
            return;
        };
        for tc_patch in patch
            .get("toolCalls")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let (Some(id), Some(result)) = (
                tc_patch.get("id").and_then(Value::as_str),
                tc_patch.get("result"),
            ) else {
                continue;
            };
            if let Some(call) = calls
                .iter_mut()
                .find(|c| c.get("id").and_then(Value::as_str) == Some(id))
                .and_then(Value::as_object_mut)
            {
                call.insert("result".into(), result.clone());
            }
        }
    }
}

fn str_field<'a>(obj: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    obj.get(key).and_then(Value::as_str)
}

/// A message line has a string `id` and is no patch.
fn is_message(v: &Value) -> bool {
    v.get("id").is_some_and(Value::is_string) && v.get("$patch").is_none()
}

/// Replay a session file. `None` when it is neither a readable log nor a
/// legacy JSON object.
fn replay(raw: &str) -> Option<Replay> {
    let mut state = Replay::default();
    for line in raw.lines() {
        if line.trim().is_empty() {
            continue;
        }
        if let Ok(record) = serde_json::from_str::<Value>(line) {
            state.line(&record);
        }
    }
    let complete = str_field(&state.meta, "sessionId").is_some()
        && str_field(&state.meta, "projectHash").is_some();
    if complete {
        return Some(state);
    }
    // Legacy: one JSON object for the whole session.
    let legacy = serde_json::from_str::<Value>(raw).ok()?;
    let obj = legacy.as_object().filter(|o| o.contains_key("sessionId"))?;
    let messages = obj
        .get("messages")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|m| m.is_object())
        .map(|m| {
            let id = m.get("id").and_then(Value::as_str).unwrap_or_default();
            (id.to_string(), m.clone())
        })
        .collect();
    Some(Replay {
        meta: obj.clone(),
        first_session_id: str_field(obj, "sessionId").map(str::to_string),
        messages,
    })
}

/// Text of a genai part list (a string, one part or an array of both).
/// Parts marked `thought` are dropped.
fn part_text(content: &Value) -> String {
    match content {
        Value::String(s) => s.clone(),
        Value::Array(parts) => parts.iter().map(part_text).collect(),
        Value::Object(part) if part.get("thought").and_then(Value::as_bool) != Some(true) => part
            .get("text")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        _ => String::new(),
    }
}

fn parse_ts(v: Option<&Value>) -> Option<DateTime<Utc>> {
    let s = v?.as_str()?;
    DateTime::parse_from_rfc3339(s)
        .ok()
        .map(|d| d.with_timezone(&Utc))
}

fn token(message: &Value, key: &str) -> i64 {
    message
        .get("tokens")
        .and_then(|t| t.get(key))
        .and_then(Value::as_i64)
        .unwrap_or(0)
}

/// Project root from the `.project_root` marker the CLI keeps in each
/// project directory, else the directory name (a slug or the older hash).
fn project_path(session_file: &Path) -> Option<String> {
    let dir = session_file.parent()?.parent()?;
    if let Some(root) = marker(dir) {
        return Some(root);
    }
    let name = dir.file_name()?.to_str()?;
    if is_hash_dir(name) {
        if let Some(root) = root_of_hash(dir.parent()?, name) {
            return Some(root);
        }
    }
    Some(name.to_string())
}

fn marker(project_dir: &Path) -> Option<String> {
    let root = std::fs::read_to_string(project_dir.join(".project_root")).ok()?;
    let root = root.trim();
    (!root.is_empty()).then(|| root.to_string())
}

/// A project folder of the older layout: the SHA-256 hex of the root.
fn is_hash_dir(name: &str) -> bool {
    name.len() == 64 && name.bytes().all(|b| b.is_ascii_hexdigit())
}

/// The root in the slug folder's marker whose SHA-256 is `hash`.
fn root_of_hash(tmp: &Path, hash: &str) -> Option<String> {
    std::fs::read_dir(tmp)
        .ok()?
        .filter_map(|e| e.ok())
        .filter_map(|e| marker(&e.path()))
        .find(|root| format!("{:x}", Sha256::digest(root.as_bytes())).eq_ignore_ascii_case(hash))
}

fn session_id_of(state: &Replay) -> Option<String> {
    state
        .first_session_id
        .clone()
        .or_else(|| str_field(&state.meta, "sessionId").map(str::to_string))
}

/// Whether a session file in a hash folder was also copied into a slug
/// folder: a file of the same name (or its `.jsonl` successor) with the same
/// session id.
fn has_slug_copy(path: &Path, session_id: &str) -> bool {
    let (Some(name), Some(dir)) = (
        path.file_name().and_then(|n| n.to_str()),
        path.parent().and_then(Path::parent),
    ) else {
        return false;
    };
    let in_hash_dir = dir
        .file_name()
        .and_then(|n| n.to_str())
        .is_some_and(is_hash_dir);
    let Some(entries) = dir
        .parent()
        .filter(|_| in_hash_dir)
        .and_then(|tmp| std::fs::read_dir(tmp).ok())
    else {
        return false;
    };
    let names = [name.to_string(), format!("{name}l")];
    entries
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| !is_hash_dir(n))
        })
        .flat_map(|p| names.iter().map(move |n| p.join("chats").join(n)))
        .filter(|candidate| candidate.is_file())
        .any(|candidate| {
            std::fs::read_to_string(&candidate)
                .ok()
                .and_then(|raw| replay(&raw))
                .and_then(|state| session_id_of(&state))
                .as_deref()
                == Some(session_id)
        })
}

impl Adapter for GeminiCliAdapter {
    fn name(&self) -> &str {
        "gemini_cli"
    }

    fn home(&self) -> PathBuf {
        self.home.clone()
    }

    fn discover(&self) -> Result<Vec<PathBuf>> {
        if !self.home.is_dir() {
            return Ok(Vec::new());
        }
        let mut out = Vec::new();
        for project in std::fs::read_dir(&self.home)? {
            let chats = project?.path().join("chats");
            if !chats.is_dir() {
                continue;
            }
            // Direct children only: subagent logs sit one level deeper.
            for entry in std::fs::read_dir(&chats)? {
                let path = entry?.path();
                if path.is_file() && is_session_file(&path) && self.excluded_reason(&path).is_none()
                {
                    out.push(path);
                }
            }
        }
        out.sort();
        Ok(out)
    }

    fn excluded_reason(&self, path: &Path) -> Option<&'static str> {
        let parent = path.parent().and_then(|p| p.file_name());
        if parent.is_some_and(|p| p != "chats") {
            return Some("subagent transcript");
        }
        // A resumed `.json` session continues in `<name>.jsonl`.
        if path.extension().is_some_and(|e| e == "json") {
            let mut successor = path.as_os_str().to_owned();
            successor.push("l");
            if Path::new(&successor).exists() {
                return Some("continued in .jsonl");
            }
        }
        None
    }

    fn parse(&self, path: &Path) -> Result<Option<NormalisedConversation>> {
        let raw = std::fs::read_to_string(path)?;
        let Some(state) = replay(&raw) else {
            return Ok(None);
        };
        if str_field(&state.meta, "kind") == Some("subagent") {
            return Ok(None);
        }
        let Some(raw_session_id) = session_id_of(&state) else {
            return Ok(None);
        };
        if has_slug_copy(path, &raw_session_id) {
            return Ok(None);
        }
        let session_id = stable_uuid_str(&NS, Some(&raw_session_id), "gemini_cli:session");

        let started_at = parse_ts(state.meta.get("startTime"))
            .or_else(|| {
                state
                    .messages
                    .iter()
                    .find_map(|(_, m)| parse_ts(m.get("timestamp")))
            })
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
        for (idx, (id, m)) in state.messages.iter().enumerate() {
            let role = match m.get("type").and_then(Value::as_str) {
                Some("user") => "user",
                Some("gemini") => "assistant",
                _ => continue,
            };
            let mut lines = Vec::new();
            let typed = m
                .get("displayContent")
                .filter(|_| role == "user")
                .map(part_text)
                .filter(|t| !t.trim().is_empty());
            let text = typed.unwrap_or_else(|| part_text(m.get("content").unwrap_or(&Value::Null)));
            if !text.trim().is_empty() {
                lines.push(text.trim().to_string());
            }
            let mut calls = Vec::new();
            for call in m
                .get("toolCalls")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                let name = call.get("name").and_then(Value::as_str).unwrap_or("?");
                let description = call
                    .get("description")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .trim();
                lines.push(if description.is_empty() {
                    format!("[Tool: {name}]")
                } else {
                    format!("[Tool: {name}] {description}")
                });
                calls.push(json!({
                    "tool_name": name,
                    "input": call.get("args").cloned().unwrap_or_else(|| json!({})),
                }));
            }
            // A turn that only carried thoughts leaves nothing to keep.
            if lines.is_empty() {
                continue;
            }
            let created_at = parse_ts(m.get("timestamp")).unwrap_or(last_ts);
            last_ts = created_at;
            let (t_in, t_out, total) = (token(m, "input"), token(m, "output"), token(m, "total"));
            tokens_in += t_in;
            tokens_out += t_out;
            // Message ids are stable across rewrites; only legacy messages
            // without one fall back to their position.
            let uuid = if id.is_empty() {
                stable_uuid_str(&NS, None, &format!("gemini_cli:{raw_session_id}:{idx}"))
            } else {
                stable_uuid_str(&NS, Some(id), &format!("gemini_cli:{raw_session_id}:msg"))
            };
            let tool_name = calls
                .first()
                .and_then(|c| c["tool_name"].as_str())
                .map(str::to_string);
            let tool_calls = (!calls.is_empty()).then_some(Value::Array(calls));
            messages.push(NormalisedMessage {
                role: role.into(),
                content: lines.join("\n"),
                tool_name,
                tool_calls,
                created_at: Some(created_at),
                model: m.get("model").and_then(Value::as_str).map(str::to_string),
                token_count: (total > 0).then_some(total),
                uuid: Some(uuid),
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
            .chain(parse_ts(state.meta.get("lastUpdated")))
            .fold(started_at, |acc, t| acc.max(t));
        let model = messages
            .iter()
            .find(|m| m.role == "assistant")
            .and_then(|m| m.model.clone());

        Ok(Some(NormalisedConversation {
            session_id,
            project_path: project_path(path),
            model,
            entrypoint: None,
            git_branch: None,
            started_at,
            ended_at: Some(ended_at),
            messages,
            token_count_in: (tokens_in > 0).then_some(tokens_in),
            token_count_out: (tokens_out > 0).then_some(tokens_out),
            summary: str_field(&state.meta, "summary").map(str::to_string),
            metadata: json!({
                "source": "gemini_cli",
                "project_hash": str_field(&state.meta, "projectHash"),
            }),
            source_tool: Some("gemini_cli".into()),
        }))
    }
}

#[cfg(test)]
mod tests {
    //! Invented fixtures. Their shape follows the session recorder of
    //! google-gemini/gemini-cli at commit 6814e00d6b148c3a1d57244d5d7040f7c541009d
    //! (`packages/core/src/services/chatRecordingService.ts`); no real session
    //! file was available.

    use super::*;
    use chrono::{DateTime, Utc};
    use serde_json::{json, Value};
    use uuid::Uuid;

    fn ts(s: &str) -> DateTime<Utc> {
        DateTime::parse_from_rfc3339(s).unwrap().with_timezone(&Utc)
    }

    fn write_lines(path: &Path, lines: &[Value]) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let body: String = lines.iter().map(|l| format!("{l}\n")).collect();
        std::fs::write(path, body).unwrap();
    }

    fn meta(session: &str, kind: &str) -> Value {
        json!({
            "sessionId": session,
            "projectHash": "7d1e0c3b9a",
            "startTime": "2026-01-02T10:00:00.000Z",
            "lastUpdated": "2026-01-02T10:00:00.000Z",
            "kind": kind
        })
    }

    #[test]
    fn gemini_session_roles_times_and_tool_calls() {
        let tmp = tempfile::tempdir().unwrap();
        let proj = tmp.path().join("demo-app");
        std::fs::create_dir_all(&proj).unwrap();
        std::fs::write(proj.join(".project_root"), "/home/dev/demo-app\n").unwrap();
        let session = "4b0f3c2e-9d61-4c1a-8e57-2a6d0b9f7c11";
        let path = proj.join("chats/session-2026-01-02T10-00-4b0f3c2e.jsonl");
        write_lines(
            &path,
            &[
                meta(session, "main"),
                json!({"id": "a1f0c6de-0d3e-4c55-9a43-5b8f2e7d6c01", "timestamp": "2026-01-02T10:00:05.000Z",
                       "type": "user",
                       // The model saw the expanded @file text; the user typed less.
                       "content": [{"text": "list the files @a.rs"},
                                   {"text": "\n--- a.rs ---\nfn main() {}"}],
                       "displayContent": [{"text": "list the files @a.rs"}]}),
                json!({"id": "a1f0c6de-0d3e-4c55-9a43-5b8f2e7d6c02", "timestamp": "2026-01-02T10:00:09.000Z",
                       "type": "gemini", "content": "", "model": "gemini-test",
                       "thoughts": [{"subject": "Plan", "description": "look at the folder first",
                                     "timestamp": "2026-01-02T10:00:08.000Z"}],
                       "toolCalls": [{"id": "call-1", "name": "run_shell_command",
                                      "args": {"command": "ls"}, "status": "success",
                                      "timestamp": "2026-01-02T10:00:09.000Z",
                                      "description": "ls",
                                      "result": [{"functionResponse": {"name": "run_shell_command",
                                                  "response": {"output": "a.rs\nb.rs"}}}]}],
                       "tokens": {"input": 100, "output": 20, "cached": 0, "total": 120}}),
                json!({"$set": {"lastUpdated": "2026-01-02T10:00:09.000Z"}}),
                // Tool output comes back as a user record of functionResponse parts.
                json!({"id": "a1f0c6de-0d3e-4c55-9a43-5b8f2e7d6c05", "timestamp": "2026-01-02T10:00:10.000Z",
                       "type": "user",
                       "content": [{"functionResponse": {"id": "call-1", "name": "run_shell_command",
                                                         "response": {"output": "a.rs\nb.rs"}}}]}),
                // An offset other than Z is converted to UTC.
                json!({"id": "a1f0c6de-0d3e-4c55-9a43-5b8f2e7d6c03", "timestamp": "2026-01-02T12:00:12.000+02:00",
                       "type": "gemini", "model": "gemini-test",
                       "content": [{"text": "hidden reasoning", "thought": true},
                                   {"text": "Two files: a.rs and b.rs."}],
                       "tokens": {"input": 130, "output": 10, "cached": 0, "total": 140}}),
                json!({"id": "a1f0c6de-0d3e-4c55-9a43-5b8f2e7d6c04", "timestamp": "2026-01-02T10:00:13.000Z",
                       "type": "info", "content": "Session saved"}),
                json!({"$set": {"lastUpdated": "2026-01-02T10:00:20.000Z", "summary": "Listing files"}}),
            ],
        );

        let adapter = GeminiCliAdapter::new(tmp.path().to_path_buf());
        assert_eq!(adapter.discover().unwrap(), vec![path.clone()]);
        let conv = adapter.parse(&path).unwrap().expect("parsed");

        assert_eq!(conv.session_id, Uuid::parse_str(session).unwrap());
        assert_eq!(conv.source_tool.as_deref(), Some("gemini_cli"));
        assert_eq!(conv.project_path.as_deref(), Some("/home/dev/demo-app"));
        assert_eq!(conv.summary.as_deref(), Some("Listing files"));
        assert_eq!(conv.model.as_deref(), Some("gemini-test"));
        let roles: Vec<&str> = conv.messages.iter().map(|m| m.role.as_str()).collect();
        assert_eq!(roles, ["user", "assistant", "assistant"]);
        assert_eq!(conv.messages[0].content, "list the files @a.rs");
        assert_eq!(conv.messages[1].content, "[Tool: run_shell_command] ls");
        assert_eq!(conv.messages[2].content, "Two files: a.rs and b.rs.");
        for m in &conv.messages {
            assert!(!m.content.contains("hidden reasoning"), "thought part kept");
            assert!(!m.content.contains("look at the folder"), "thoughts kept");
            assert!(!m.content.contains("fn main"), "expanded file text kept");
        }
        let calls = conv.messages[1].tool_calls.as_ref().expect("tool calls");
        assert_eq!(calls[0]["tool_name"], "run_shell_command");
        assert_eq!(calls[0]["input"]["command"], "ls");
        assert_eq!(
            conv.messages[1].tool_name.as_deref(),
            Some("run_shell_command")
        );
        assert_eq!(
            conv.messages[0].uuid,
            Some(Uuid::parse_str("a1f0c6de-0d3e-4c55-9a43-5b8f2e7d6c01").unwrap())
        );
        assert_eq!(
            conv.messages[0].created_at,
            Some(ts("2026-01-02T10:00:05Z"))
        );
        assert_eq!(
            conv.messages[2].created_at,
            Some(ts("2026-01-02T10:00:12Z"))
        );
        assert_eq!(conv.started_at, ts("2026-01-02T10:00:00Z"));
        assert_eq!(conv.ended_at, Some(ts("2026-01-02T10:00:20Z")));
        assert_eq!(conv.token_count_in, Some(230));
        assert_eq!(conv.token_count_out, Some(30));
    }

    #[test]
    fn gemini_patch_lines_apply_in_order() {
        let tmp = tempfile::tempdir().unwrap();
        let proj = tmp.path().join("9f1c2b7e4d");
        let msg = |id: &str, kind: &str, text: &str, second: u32| {
            json!({"id": id, "timestamp": format!("2026-01-02T10:00:{second:02}.000Z"),
                   "type": kind, "content": text})
        };
        let path = proj.join("chats/session-2026-01-02T10-00-5c2d1e0f.jsonl");
        write_lines(
            &path,
            &[
                meta("5c2d1e0f-1111-4a2b-9c3d-000000000001", "main"),
                msg("m0", "user", "dropped by the checkpoint", 1),
                // Legacy checkpoint: the message list is replaced.
                json!({"$set": {"messages": [
                    msg("m1", "user", "first question", 2),
                    msg("m2", "gemini", "first answer draft", 3)
                ]}}),
                msg("m3", "user", "second question", 4),
                msg("m4", "gemini", "second answer", 5),
                json!({"$patch": {"id": "m2", "content": "first answer, corrected"}}),
                // The same id again replaces the message where it stands.
                msg("m1", "user", "first question, edited", 2),
                // Drops m3 and everything after it.
                json!({"$rewindTo": "m3"}),
                msg("m5", "user", "another question", 6),
                msg("m6", "gemini", "another answer", 7),
                json!({"$patch": {"updates": [{"id": "m6", "content": "another answer, patched"}],
                                  "removeIds": ["m5"]}}),
                // Within one patch `updates` come after the single `id`; this
                // one also patches a user message.
                json!({"$patch": {"id": "m1", "content": "from the single patch",
                                  "updates": [{"id": "m1", "content": "first question, patched"}]}}),
                // Listed messages move, in this order, behind the others.
                json!({"$patch": {"orderIds": ["m6", "m1"]}}),
            ],
        );

        let conv = GeminiCliAdapter::new(tmp.path().to_path_buf())
            .parse(&path)
            .unwrap()
            .expect("parsed");
        let contents: Vec<&str> = conv.messages.iter().map(|m| m.content.as_str()).collect();
        assert_eq!(
            contents,
            [
                "first answer, corrected",
                "another answer, patched",
                "first question, patched"
            ]
        );
        // No `.project_root` marker: the project directory name stands in.
        assert_eq!(conv.project_path.as_deref(), Some("9f1c2b7e4d"));

        // A rewind to an unknown id drops every message.
        let cleared = proj.join("chats/session-2026-01-02T11-00-6d3e2f10.jsonl");
        write_lines(
            &cleared,
            &[
                meta("6d3e2f10-2222-4a2b-9c3d-000000000002", "main"),
                msg("x1", "user", "gone", 1),
                json!({"$rewindTo": "not-there"}),
            ],
        );
        assert!(GeminiCliAdapter::new(tmp.path().to_path_buf())
            .parse(&cleared)
            .unwrap()
            .is_none());
    }

    #[test]
    fn gemini_skips_subagent_sessions() {
        let tmp = tempfile::tempdir().unwrap();
        let chats = tmp.path().join("demo-app/chats");
        let parent = "7e4f3a21-3333-4a2b-9c3d-000000000003";
        let main = chats.join("session-2026-01-02T10-00-7e4f3a21.jsonl");
        write_lines(
            &main,
            &[
                meta(parent, "main"),
                json!({"id": "u1", "timestamp": "2026-01-02T10:00:01.000Z", "type": "user",
                       "content": "delegate this"}),
            ],
        );
        // The recorder nests subagent logs under the parent session id.
        let nested = chats
            .join(parent)
            .join("8a5b4c32-4444-4a2b-9c3d-000000000004.jsonl");
        let sub_lines = [
            meta("8a5b4c32-4444-4a2b-9c3d-000000000004", "subagent"),
            json!({"id": "s1", "timestamp": "2026-01-02T10:00:02.000Z", "type": "user",
                   "content": "subagent task"}),
        ];
        write_lines(&nested, &sub_lines);
        // A subagent log at the top level is still skipped by its `kind`.
        let flat = chats.join("session-2026-01-02T10-01-8a5b4c32.jsonl");
        write_lines(&flat, &sub_lines);

        let adapter = GeminiCliAdapter::new(tmp.path().to_path_buf());
        assert_eq!(
            adapter.discover().unwrap(),
            vec![main.clone(), flat.clone()]
        );
        assert_eq!(
            adapter.excluded_reason(&nested),
            Some("subagent transcript")
        );
        assert!(adapter.parse(&flat).unwrap().is_none());
        assert!(adapter.parse(&nested).unwrap().is_none());
        assert_eq!(
            adapter.parse(&main).unwrap().expect("main").messages.len(),
            1
        );
    }

    #[test]
    fn gemini_reads_legacy_json_sessions() {
        let tmp = tempfile::tempdir().unwrap();
        let chats = tmp.path().join("demo-app/chats");
        std::fs::create_dir_all(&chats).unwrap();
        let legacy = chats.join("session-2026-01-01T09-00-9b6c5d43.json");
        let record = json!({
            "sessionId": "9b6c5d43-5555-4a2b-9c3d-000000000005",
            "projectHash": "7d1e0c3b9a",
            "startTime": "2026-01-01T09:00:00.000Z",
            "lastUpdated": "2026-01-01T09:00:30.000Z",
            "messages": [
                {"id": "l1", "timestamp": "2026-01-01T09:00:10.000Z", "type": "user",
                 "content": "old format question"},
                {"id": "l2", "timestamp": "2026-01-01T09:00:20.000Z", "type": "gemini",
                 "content": "old format answer"}
            ]
        });
        std::fs::write(&legacy, serde_json::to_string_pretty(&record).unwrap()).unwrap();
        // A legacy file that was resumed lives on as `<name>.jsonl`; only that one counts.
        let migrated = chats.join("session-2026-01-01T08-00-0c7d6e54.json");
        std::fs::write(&migrated, "{}").unwrap();
        let successor = chats.join("session-2026-01-01T08-00-0c7d6e54.jsonl");
        write_lines(
            &successor,
            &[meta("0c7d6e54-6666-4a2b-9c3d-000000000006", "main")],
        );

        let adapter = GeminiCliAdapter::new(tmp.path().to_path_buf());
        assert_eq!(
            adapter.discover().unwrap(),
            vec![successor.clone(), legacy.clone()]
        );
        let conv = adapter.parse(&legacy).unwrap().expect("legacy parsed");
        let contents: Vec<&str> = conv.messages.iter().map(|m| m.content.as_str()).collect();
        assert_eq!(contents, ["old format question", "old format answer"]);
        assert_eq!(conv.started_at, ts("2026-01-01T09:00:00Z"));
    }

    #[test]
    fn gemini_migrated_duplicate_session_is_read_once_from_the_slug_folder() {
        use sha2::{Digest, Sha256};
        let tmp = tempfile::tempdir().unwrap();
        let root = "/home/dev/demo-app";
        // The CLI copies `tmp/<sha256 of the root>/` to `tmp/<slug>/` and keeps
        // the old folder.
        let hash = format!("{:x}", Sha256::digest(root.as_bytes()));
        let slug = tmp.path().join("demo-app");
        std::fs::create_dir_all(&slug).unwrap();
        std::fs::write(slug.join(".project_root"), format!("{root}\n")).unwrap();
        let name = "session-2026-01-02T10-00-a1b2c3d4.jsonl";
        let session = "a1b2c3d4-aaaa-4a2b-9c3d-00000000000a";
        let copied = [
            meta(session, "main"),
            json!({"id": "c1", "timestamp": "2026-01-02T10:00:01.000Z", "type": "user",
                   "content": "asked before the migration"}),
        ];
        let old_copy = tmp.path().join(&hash).join("chats").join(name);
        write_lines(&old_copy, &copied);
        let new_copy = slug.join("chats").join(name);
        write_lines(
            &new_copy,
            &[
                copied[0].clone(),
                copied[1].clone(),
                json!({"id": "c2", "timestamp": "2026-01-02T10:05:00.000Z", "type": "user",
                       "content": "asked after the migration"}),
            ],
        );
        // A session left only in the old folder (its copy expired).
        let orphan = tmp
            .path()
            .join(&hash)
            .join("chats/session-2026-01-01T09-00-b2c3d4e5.jsonl");
        write_lines(
            &orphan,
            &[
                meta("b2c3d4e5-bbbb-4a2b-9c3d-00000000000b", "main"),
                json!({"id": "o1", "timestamp": "2026-01-01T09:00:01.000Z", "type": "user",
                       "content": "only in the old folder"}),
            ],
        );

        let adapter = GeminiCliAdapter::new(tmp.path().to_path_buf());
        let found = adapter.discover().unwrap();
        assert!(found.contains(&old_copy) && found.contains(&new_copy));
        // The old copy is skipped; the runner counts it as skipped.
        assert!(adapter.parse(&old_copy).unwrap().is_none());
        let conv = adapter.parse(&new_copy).unwrap().expect("slug copy");
        assert_eq!(conv.messages.len(), 2);
        assert_eq!(conv.project_path.as_deref(), Some(root));
        // The orphan keeps the real root, not the hash, as its project.
        let orphan_conv = adapter.parse(&orphan).unwrap().expect("orphan");
        assert_eq!(orphan_conv.project_path.as_deref(), Some(root));
    }
}
