//! LoCoMo adapter, for benchmark runs only.
//!
//! Reads the public LoCoMo file: a JSON array of long dialogues between two
//! speakers, each split into dated sessions, plus question-answer items whose
//! evidence names dialogue turns (`D<session>:<turn>`). Every session becomes
//! one conversation, and the dialogue's `sample_id` is its project.
//!
//! The data is licensed CC BY-NC and must never reach a real memory store, so
//! the adapter is not a built-in (`ingest --source all` never sees it), has no
//! default path (only `THROUGHLINE_LOCOMO_PATH`), and `ingest --source locomo`
//! and `recall-eval --mode locomo` refuse every database except
//! `throughline_bench`.

use super::{stable_uuid_str, Adapter};
use crate::types::{NormalisedConversation, NormalisedMessage};
use anyhow::{anyhow, bail, Context, Result};
use chrono::{Duration, NaiveDateTime};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use uuid::Uuid;

/// Adapter name, as `ingest --source` takes it.
pub const NAME: &str = "locomo";
/// The only place the data file's path comes from.
pub const PATH_ENV: &str = "THROUGHLINE_LOCOMO_PATH";
/// The only database `ingest --source locomo` writes to.
pub const BENCH_DATABASE: &str = "throughline_bench";

const NS: Uuid = Uuid::from_bytes([
    0x6c, 0x6f, 0x63, 0x6f, 0x6d, 0x6f, 0x4b, 0x21, 0x9e, 0x3a, 0x51, 0x0d, 0x7b, 0x42, 0xc8, 0x15,
]);

/// Session times look like `1:56 pm on 8 May, 2023`; read as UTC.
const DATE_FORMAT: &str = "%I:%M %p on %d %B, %Y";

/// Refuse any database but [`BENCH_DATABASE`], before a connection is opened.
pub fn check_database(dbname: &str) -> Result<()> {
    if dbname == BENCH_DATABASE {
        return Ok(());
    }
    bail!("LoCoMo benchmark data is used only with PGDATABASE={BENCH_DATABASE} (got {dbname:?})")
}

/// The ids in one evidence string, split on spaces, commas and semicolons.
pub(crate) fn evidence_tokens(raw: &str) -> impl Iterator<Item = &str> {
    raw.split(|c: char| c.is_whitespace() || c == ';' || c == ',')
        .filter(|t| !t.is_empty())
}

/// Evidence ids of the question-answer items, checked against the turns.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct EvidenceCheck {
    /// Well-formed ids that name a turn of their dialogue.
    pub read: usize,
    /// Tokens that are not `D<session>:<turn>`.
    pub malformed: usize,
    /// Well-formed ids that name no turn of their dialogue.
    pub unknown: usize,
}

/// Map a LoCoMo file to one conversation per session.
///
/// Speaker A is `user`, speaker B `assistant`; each turn keeps its speaker's
/// name in front and its `dia_id` in the metadata. Turn n of a session is
/// stamped session time plus n seconds, so turns stay ordered and distinct.
/// Evidence strings are split on spaces, commas and semicolons, and every
/// token is checked against the dialogue's turns.
pub fn parse_dialogues(raw: &str) -> Result<(Vec<NormalisedConversation>, EvidenceCheck)> {
    let samples: Vec<Value> =
        serde_json::from_str(raw).context("LoCoMo file is not a JSON array")?;
    let mut convs = Vec::new();
    let mut evidence = EvidenceCheck::default();
    for (i, sample) in samples.iter().enumerate() {
        let sample_id = sample["sample_id"]
            .as_str()
            .filter(|s| !s.is_empty())
            .ok_or_else(|| anyhow!("dialogue {i} has no sample_id"))?;
        let dialogue = &sample["conversation"];
        let speaker = |key: &str| {
            dialogue[key]
                .as_str()
                .ok_or_else(|| anyhow!("dialogue {i} has no {key}"))
        };
        let (speaker_a, speaker_b) = (speaker("speaker_a")?, speaker("speaker_b")?);

        let mut sessions: Vec<(u32, &Vec<Value>)> = dialogue
            .as_object()
            .into_iter()
            .flatten()
            .filter_map(|(key, value)| {
                let n = key.strip_prefix("session_")?.parse().ok()?;
                Some((n, value.as_array()?))
            })
            .collect();
        sessions.sort_by_key(|(n, _)| *n);

        let mut turn_ids: HashSet<&str> = HashSet::new();
        for (n, turns) in sessions {
            if turns.is_empty() {
                continue;
            }
            let date = dialogue[format!("session_{n}_date_time")]
                .as_str()
                .ok_or_else(|| anyhow!("dialogue {i} session {n} has no date"))?;
            let started_at = NaiveDateTime::parse_from_str(date, DATE_FORMAT)
                .with_context(|| format!("dialogue {i} session {n}: unreadable date"))?
                .and_utc();
            let mut messages = Vec::with_capacity(turns.len());
            for (t, turn) in turns.iter().enumerate() {
                let field = |key: &str| {
                    turn[key]
                        .as_str()
                        .ok_or_else(|| anyhow!("dialogue {i} session {n} turn {t} has no {key}"))
                };
                let (name, dia_id, text) = (field("speaker")?, field("dia_id")?, field("text")?);
                let role = if name == speaker_a {
                    "user"
                } else if name == speaker_b {
                    "assistant"
                } else {
                    bail!("dialogue {i} session {n} turn {t}: speaker is neither A nor B");
                };
                let mut content = format!("{name}: {text}");
                if let Some(caption) = turn["blip_caption"].as_str() {
                    content.push_str(&format!(" [shares a photo: {caption}]"));
                }
                turn_ids.insert(dia_id);
                messages.push(NormalisedMessage {
                    role: role.into(),
                    content,
                    created_at: Some(started_at + Duration::seconds(t as i64)),
                    uuid: Some(stable_uuid_str(
                        &NS,
                        Some(&format!("{sample_id}:{dia_id}")),
                        NAME,
                    )),
                    metadata: json!({ "dia_id": dia_id }),
                    ..Default::default()
                });
            }
            let ended_at = messages.last().and_then(|m| m.created_at);
            convs.push(NormalisedConversation {
                session_id: stable_uuid_str(
                    &NS,
                    Some(&format!("{sample_id}:session_{n}")),
                    "locomo:session",
                ),
                project_path: Some(sample_id.to_string()),
                model: None,
                entrypoint: Some(NAME.into()),
                git_branch: None,
                started_at,
                ended_at,
                messages,
                token_count_in: None,
                token_count_out: None,
                summary: Some(format!("{sample_id} session {n}")),
                metadata: json!({ "source": NAME, "sample_id": sample_id, "session": n }),
                source_tool: Some(NAME.into()),
            });
        }

        let items = sample["qa"].as_array().map(Vec::as_slice).unwrap_or(&[]);
        for raw_id in items
            .iter()
            .flat_map(|q| q["evidence"].as_array().map(Vec::as_slice).unwrap_or(&[]))
        {
            let raw_id = raw_id.as_str().unwrap_or("");
            for token in evidence_tokens(raw_id) {
                if !is_turn_id(token) {
                    evidence.malformed += 1;
                } else if turn_ids.contains(token) {
                    evidence.read += 1;
                } else {
                    evidence.unknown += 1;
                }
            }
            if raw_id.trim().is_empty() {
                evidence.malformed += 1;
            }
        }
    }
    Ok((convs, evidence))
}

/// `D<session>:<turn>`, both numbers.
fn is_turn_id(token: &str) -> bool {
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    token
        .strip_prefix('D')
        .and_then(|rest| rest.split_once(':'))
        .is_some_and(|(session, turn)| digits(session) && digits(turn))
}

pub struct LocomoAdapter {
    path: Option<PathBuf>,
}

impl LocomoAdapter {
    /// The adapter for the file named by [`PATH_ENV`], if set.
    pub fn from_env() -> Self {
        Self::with_path(
            std::env::var_os(PATH_ENV)
                .filter(|p| !p.is_empty())
                .map(PathBuf::from),
        )
    }

    pub fn with_path(path: Option<PathBuf>) -> Self {
        Self { path }
    }
}

impl Adapter for LocomoAdapter {
    fn name(&self) -> &str {
        NAME
    }

    fn home(&self) -> PathBuf {
        self.path.clone().unwrap_or_default()
    }

    /// Always asked for by name, so a missing path is reported by `discover`.
    fn is_present(&self) -> bool {
        true
    }

    fn discover(&self) -> Result<Vec<PathBuf>> {
        let path = self
            .path
            .as_ref()
            .ok_or_else(|| anyhow!("set {PATH_ENV} to the LoCoMo JSON file"))?;
        if !path.is_file() {
            bail!("{PATH_ENV} names no file: {}", path.display());
        }
        Ok(vec![path.clone()])
    }

    fn parse(&self, path: &Path) -> Result<Option<NormalisedConversation>> {
        Ok(self.parse_many(path)?.into_iter().next())
    }

    fn parse_many(&self, path: &Path) -> Result<Vec<NormalisedConversation>> {
        let raw = std::fs::read_to_string(path)?;
        let (convs, evidence) = parse_dialogues(&raw)?;
        eprintln!(
            "[{NAME}] evidence ids: {} read, {} malformed, {} naming no turn",
            evidence.read, evidence.malformed, evidence.unknown
        );
        Ok(convs)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{DateTime, TimeZone, Utc};

    /// Synthetic: invented speakers, sentences and dates. Session 3 has a date
    /// but no turns, which the real file also does once. The second item's
    /// evidence holds two ids in one string, an id that names no turn and two
    /// malformed tokens.
    const FIXTURE: &str = r#"[
      {
        "sample_id": "demo-1",
        "conversation": {
          "speaker_a": "Odile",
          "speaker_b": "Fenn",
          "session_1_date_time": "3:15 pm on 4 March, 2031",
          "session_1": [
            {"speaker": "Odile", "dia_id": "D1:1", "text": "My kayak finally arrived."},
            {"speaker": "Fenn", "dia_id": "D1:2", "text": "Where will you paddle first?"},
            {"speaker": "Odile", "dia_id": "D1:3", "text": "Look at the lake.",
             "img_url": ["http://example.invalid/kayak.jpg"],
             "blip_caption": "a red kayak on a lake", "query": "kayak"}
          ],
          "session_2_date_time": "12:05 am on 11 March, 2031",
          "session_2": [
            {"speaker": "Fenn", "dia_id": "D2:1", "text": "I started a pottery class."},
            {"speaker": "Odile", "dia_id": "D2:2", "text": "What did you make?"}
          ],
          "session_3_date_time": "1:00 pm on 12 March, 2031"
        },
        "qa": [
          {"question": "What arrived?", "answer": "A kayak", "evidence": ["D1:1"], "category": 4},
          {"question": "What did they talk about?", "answer": "Boats and pots",
           "evidence": ["D1:2 D2:1", "D9:9", "D", "D:1:2"], "category": 1},
          {"question": "Which car?", "adversarial_answer": "A van", "evidence": [], "category": 5}
        ],
        "observation": {},
        "session_summary": {},
        "event_summary": {}
      }
    ]"#;

    fn utc(y: i32, mo: u32, d: u32, h: u32, mi: u32, s: u32) -> DateTime<Utc> {
        Utc.with_ymd_and_hms(y, mo, d, h, mi, s).unwrap()
    }

    #[test]
    fn locomo_adapter_maps_sessions_to_conversations() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("dialogues.json");
        std::fs::write(&path, FIXTURE).unwrap();
        let adapter = LocomoAdapter::with_path(Some(path.clone()));
        assert_eq!(adapter.name(), NAME);
        assert_eq!(adapter.discover().unwrap(), vec![path.clone()]);

        let convs = adapter.parse_many(&path).expect("parse");
        assert_eq!(convs.len(), 2, "one conversation per session with turns");

        let (s1, s2) = (&convs[0], &convs[1]);
        for c in &convs {
            assert_eq!(c.project_path.as_deref(), Some("demo-1"));
            assert_eq!(c.source_tool.as_deref(), Some(NAME));
        }
        assert_eq!(s1.started_at, utc(2031, 3, 4, 15, 15, 0));
        assert_eq!(
            s2.started_at,
            utc(2031, 3, 11, 0, 5, 0),
            "12 am is midnight"
        );

        let roles: Vec<&str> = s1.messages.iter().map(|m| m.role.as_str()).collect();
        assert_eq!(roles, ["user", "assistant", "user"]);
        let roles: Vec<&str> = s2.messages.iter().map(|m| m.role.as_str()).collect();
        assert_eq!(roles, ["assistant", "user"]);

        assert_eq!(s1.messages[0].content, "Odile: My kayak finally arrived.");
        assert_eq!(s1.messages[1].content, "Fenn: Where will you paddle first?");
        assert_eq!(
            s1.messages[2].content,
            "Odile: Look at the lake. [shares a photo: a red kayak on a lake]"
        );

        // Session date plus n seconds: distinct, increasing, in turn order.
        let times: Vec<DateTime<Utc>> = s1.messages.iter().map(|m| m.created_at.unwrap()).collect();
        assert_eq!(
            times,
            [
                utc(2031, 3, 4, 15, 15, 0),
                utc(2031, 3, 4, 15, 15, 1),
                utc(2031, 3, 4, 15, 15, 2)
            ]
        );
        assert_eq!(s1.ended_at, Some(utc(2031, 3, 4, 15, 15, 2)));

        assert_eq!(s1.messages[2].metadata["dia_id"], "D1:3");
        assert_eq!(s2.messages[0].metadata["dia_id"], "D2:1");

        // Ids are stable across parses and distinct within the file.
        let again = adapter.parse_many(&path).unwrap();
        assert_eq!(again[0].session_id, s1.session_id);
        assert_ne!(s1.session_id, s2.session_id);
        let ids: Vec<Uuid> = convs
            .iter()
            .flat_map(|c| c.messages.iter().map(|m| m.uuid.unwrap()))
            .collect();
        let again_ids: Vec<Uuid> = again
            .iter()
            .flat_map(|c| c.messages.iter().map(|m| m.uuid.unwrap()))
            .collect();
        assert_eq!(ids, again_ids);
        let mut unique = ids.clone();
        unique.sort();
        unique.dedup();
        assert_eq!(unique.len(), 5);
        assert_eq!(
            ids[0],
            stable_uuid_str(&NS, Some("demo-1:D1:1"), "locomo"),
            "uuid = stable_uuid(sample_id:dia_id)"
        );

        let (_, evidence) = parse_dialogues(FIXTURE).unwrap();
        assert_eq!(
            evidence,
            EvidenceCheck {
                read: 3,
                malformed: 2,
                unknown: 1
            }
        );
    }

    #[test]
    fn locomo_adapter_is_not_in_built_ins() {
        assert!(
            super::super::registry::built_ins()
                .iter()
                .all(|a| a.name() != NAME),
            "ingest --source all must never read benchmark data"
        );
        let named = super::super::registry::by_name("locomo").expect("named lookup");
        assert_eq!(named.name(), NAME);
        // No default path: without the variable there is nothing to read.
        let err = LocomoAdapter::with_path(None).discover().unwrap_err();
        assert!(err.to_string().contains(PATH_ENV), "{err}");
    }

    #[test]
    fn bench_database_guard_accepts_only_throughline_bench() {
        assert!(check_database(BENCH_DATABASE).is_ok());
        for other in ["throughline", "throughline_copy", "throughline_bench2", ""] {
            let err = check_database(other).unwrap_err().to_string();
            assert!(err.contains(BENCH_DATABASE), "{other:?}: {err}");
        }
    }
}
