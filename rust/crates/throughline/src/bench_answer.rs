//! Benchmark answers for LoCoMo (block 8, B6). Each question of categories
//! 1 to 4 whose dialogue has notes is answered and written as one JSON line,
//! for a judge that runs outside the repo.
//!
//! The memory path builds the context pack for project = the dialogue and
//! task = the question, with the default rank and the production budget, and
//! asks the answer model with the production answer prompt. The raw path
//! (`--raw`) answers through `ask` within the same project: semantic search
//! over the raw messages, the baseline without notes. Neither path counts
//! use, and only the database `throughline_bench` is accepted.

use crate::access::Actor;
use crate::adapters::locomo;
use crate::config::DbConfig;
use crate::context_pack::{self, ContextRequest, Section};
use crate::llm::{self, GenerationBackend};
use crate::recall::{self, QueryEmbedding};
use crate::recall_eval::{self, QaItem};
use anyhow::{bail, Context, Result};
use postgres::GenericClient;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::Instant;

/// A benchmark run is an operator's measurement: it sees every note.
static LOCAL: Actor = Actor::local();

/// Excerpts per question on the raw path, as the CLI and the API ask.
const RAW_LIMIT: i32 = 12;

pub struct Options {
    pub qa: PathBuf,
    pub out: PathBuf,
    /// Questions in a sample stratified by category; `None` answers all.
    pub sample: Option<usize>,
    pub seed: u64,
    pub raw: bool,
}

/// One answered question, one line of the output file.
#[derive(Debug, Serialize, Deserialize)]
pub struct Row {
    pub question_id: String,
    pub sample_id: String,
    pub category: i64,
    pub question: String,
    pub gold: String,
    /// `None` when the model call failed; `error` says why.
    pub answer: Option<String>,
    /// The notes the context pack cites (memory path).
    pub context_note_ids: Vec<i64>,
    /// The sessions of the excerpts (raw path).
    #[serde(default)]
    pub context_session_ids: Vec<i64>,
    /// Lines in the pack's "Relevant to the task" section (memory path).
    #[serde(default)]
    pub relevant_lines: usize,
    /// `memory` or `raw`.
    pub path: String,
    pub model: String,
    #[serde(default)]
    pub degraded: Option<String>,
    #[serde(default)]
    pub error: Option<String>,
    /// Embedding, retrieval and answer, wall clock.
    pub seconds: f64,
}

#[derive(Debug, Default)]
pub struct Summary {
    pub selected: usize,
    pub already_done: usize,
    pub answered: usize,
    pub errors: usize,
}

fn path_name(raw: bool) -> &'static str {
    if raw {
        "raw"
    } else {
        "memory"
    }
}

fn empty_row(item: &QaItem, path: &str, model: &str) -> Row {
    let gold = match &item.answer {
        Some(serde_json::Value::String(s)) => s.clone(),
        None | Some(serde_json::Value::Null) => String::new(),
        Some(v) => v.to_string(),
    };
    Row {
        question_id: item.id.clone().unwrap_or_default(),
        sample_id: item.sample_id.clone(),
        category: item.category,
        question: item.question.clone(),
        gold,
        answer: None,
        context_note_ids: Vec::new(),
        context_session_ids: Vec::new(),
        relevant_lines: 0,
        path: path.to_string(),
        model: model.to_string(),
        degraded: None,
        error: None,
        seconds: 0.0,
    }
}

/// The memory path for one question: the context pack for project = its
/// dialogue and task = the question, ranked with `emb` by the default rank
/// within the production budget, then `generate(system, prompt)` with the
/// production answer prompt. A failed model call is kept in `error`.
pub(crate) fn answer_memory<C: GenericClient>(
    client: &mut C,
    item: &QaItem,
    emb: Option<&QueryEmbedding>,
    model: &str,
    generate: impl FnOnce(&str, &str) -> Result<String>,
) -> Result<Row> {
    let pack = context_pack::build_context(
        client,
        &LOCAL,
        &ContextRequest {
            project: &item.sample_id,
            task: Some(&item.question),
            budget: context_pack::DEFAULT_BUDGET,
        },
        emb,
    )?;
    let mut row = empty_row(item, path_name(false), model);
    row.context_note_ids = context_pack::cited_note_ids(&pack);
    row.relevant_lines = pack
        .items
        .iter()
        .filter(|i| i.section == Section::Relevant)
        .count();
    row.degraded = pack.degraded.clone();
    let prompt = crate::ask::answer_prompt(&item.question, &pack.text);
    match generate(crate::ask::ANSWER_SYSTEM, &prompt) {
        Ok(answer) => row.answer = Some(answer.trim().to_string()),
        Err(e) => row.error = Some(format!("{e:#}")),
    }
    Ok(row)
}

/// The raw path for one question: `ask` within the dialogue's project.
fn answer_raw<C: GenericClient>(
    client: &mut C,
    item: &QaItem,
    backend: &GenerationBackend,
) -> Result<Row> {
    let answer = crate::ask::ask(
        client,
        &LOCAL,
        &item.question,
        backend,
        RAW_LIMIT,
        Some(&item.sample_id),
    )?;
    Ok(raw_row(item, &backend.label, answer))
}

/// The row of a raw answer. Without its semantic half (no question vector,
/// or the vector query failed) `ask` is word search only, which is not the
/// baseline; without excerpts or with a failed model call there is no
/// answer. Each is written as `error`, like a failed memory answer, and a
/// resume asks again.
fn raw_row(item: &QaItem, model: &str, answer: crate::ask::AskAnswer) -> Row {
    let mut row = empty_row(item, path_name(true), model);
    for s in &answer.sources {
        if !row.context_session_ids.contains(&s.conversation_id) {
            row.context_session_ids.push(s.conversation_id);
        }
    }
    if let Some(why) = answer.semantic_skipped {
        row.error = Some(format!("semantic search skipped: {why}"));
    } else if answer.sources.is_empty() {
        row.error = Some("no excerpts found".into());
    } else if let Some(e) = answer.generation_error {
        // `ask` falls back to a listing of the excerpts when the model fails.
        row.error = Some(format!("generation failed: {e}"));
    } else {
        row.answer = Some(answer.answer);
    }
    row
}

fn answer_one<C: GenericClient>(
    client: &mut C,
    item: &QaItem,
    backend: &GenerationBackend,
    raw: bool,
) -> Result<Row> {
    let start = Instant::now();
    let mut row = if raw {
        answer_raw(client, item, backend)?
    } else {
        let emb = recall::query_embedding(&item.question)?;
        if emb.is_none() {
            bail!(
                "no query embedding for {} (is Ollama reachable?)",
                item.id.as_deref().unwrap_or("")
            );
        }
        answer_memory(
            client,
            item,
            emb.as_ref(),
            &backend.label,
            |system, prompt| llm::complete(backend, Some(system), prompt),
        )?
    };
    row.seconds = start.elapsed().as_secs_f64();
    Ok(row)
}

/// `per` questions of each category 1-4 (all of a smaller one), drawn with
/// `seed`, in file order.
fn stratified(items: Vec<QaItem>, per: usize, seed: u64) -> Vec<QaItem> {
    let mut rng = recall_eval::Rng::new(seed);
    let mut chosen: HashSet<i64> = HashSet::new();
    for category in recall_eval::HEADLINE {
        let mut idx: Vec<i64> = (0..items.len() as i64)
            .filter(|&i| items[i as usize].category == category)
            .collect();
        recall_eval::shuffle(&mut idx, &mut rng);
        chosen.extend(idx.into_iter().take(per));
    }
    items
        .into_iter()
        .enumerate()
        .filter(|(i, _)| chosen.contains(&(*i as i64)))
        .map(|(_, item)| item)
        .collect()
}

/// Categories 1-4 of the dialogues that have notes, in file order.
fn select<C: GenericClient>(client: &mut C, items: Vec<QaItem>) -> Result<Vec<QaItem>> {
    let mut has_notes: HashMap<String, bool> = HashMap::new();
    let mut kept = Vec::new();
    for item in items {
        if !recall_eval::HEADLINE.contains(&item.category) {
            continue;
        }
        let has = match has_notes.get(&item.sample_id) {
            Some(&has) => has,
            None => {
                let has: bool = client
                    .query_one(
                        "SELECT EXISTS (SELECT 1 FROM memory_chunks WHERE project_name = $1)",
                        &[&item.sample_id],
                    )?
                    .get(0);
                has_notes.insert(item.sample_id.clone(), has);
                has
            }
        };
        if has {
            kept.push(item);
        }
    }
    Ok(kept)
}

/// Question ids already answered in `out` on this path; a missing file is empty.
fn finished(out: &Path, path: &str) -> Result<HashSet<String>> {
    let text = match std::fs::read_to_string(out) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(HashSet::new()),
        Err(e) => return Err(e).with_context(|| format!("read {}", out.display())),
    };
    let mut done = HashSet::new();
    for line in text.lines().filter(|l| !l.trim().is_empty()) {
        let row: Row = serde_json::from_str(line).context("parse an answer line")?;
        if row.path != path {
            bail!(
                "{} holds {} answers; use another --out for the {path} path",
                out.display(),
                row.path
            );
        }
        if row.error.is_none() {
            done.insert(row.question_id);
        }
    }
    Ok(done)
}

/// Answer the selected questions into `opts.out`, resuming a file that
/// already holds some; a failed model call is retried once, then written
/// with its error.
pub fn run(db: &DbConfig, opts: &Options) -> Result<Summary> {
    // Benchmark data never reaches a real store: refuse before anything else.
    locomo::check_database(&db.dbname)?;
    let per_category = recall_eval::HEADLINE.count();
    if opts.sample.is_some_and(|n| n < per_category) {
        bail!("--sample takes at least {per_category} questions, one per category 1-4");
    }
    let mut items = recall_eval::load_qa(&opts.qa)?;
    for (i, item) in items.iter_mut().enumerate() {
        if item.id.is_none() {
            item.id = Some(format!("{}#{i}", item.sample_id));
        }
    }
    let path = path_name(opts.raw);
    let done = finished(&opts.out, path)?;
    let mut client = db.connect().context("connect to PostgreSQL")?;
    let mut items = select(&mut client, items)?;
    if let Some(n) = opts.sample {
        items = stratified(items, n / per_category, opts.seed);
    }
    let backend = llm::resolve_backend().context("resolve generation backend")?;
    llm::ensure_local_or_allowed(&backend)?;
    // `ask` drops to word search without a question vector; stop instead.
    if recall::query_embedding("embedding check")?.is_none() {
        bail!("no query embedding (is Ollama reachable?)");
    }
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&opts.out)
        .with_context(|| format!("open {}", opts.out.display()))?;
    let mut summary = Summary {
        selected: items.len(),
        ..Default::default()
    };
    for (i, item) in items.iter().enumerate() {
        let id = item.id.as_deref().unwrap_or("");
        if done.contains(id) {
            summary.already_done += 1;
            continue;
        }
        let mut row = answer_one(&mut client, item, &backend, opts.raw)?;
        if row.error.is_some() {
            row = answer_one(&mut client, item, &backend, opts.raw)?;
        }
        match &row.error {
            Some(e) => {
                summary.errors += 1;
                eprintln!("[{}/{}] {id}: {e}", i + 1, items.len());
            }
            None => {
                summary.answered += 1;
                eprintln!(
                    "[{}/{}] {id} category {} {:.1}s",
                    i + 1,
                    items.len(),
                    row.category,
                    row.seconds
                );
            }
        }
        writeln!(file, "{}", serde_json::to_string(&row)?)?;
        file.flush()?;
    }
    Ok(summary)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sample_takes_a_share_of_each_category() {
        let items: Vec<QaItem> = (0..30)
            .map(|i| QaItem {
                id: Some(format!("demo#{i}")),
                sample_id: "demo".into(),
                // Category 3 is small: 2 questions; 1, 2 and 4 have 6 to 10.
                category: [1, 2, 4][i % 3],
                question: String::new(),
                evidence: vec![],
                evidence_raw: None,
                answer: None,
            })
            .chain((30..32).map(|i| QaItem {
                id: Some(format!("demo#{i}")),
                sample_id: "demo".into(),
                category: 3,
                question: String::new(),
                evidence: vec![],
                evidence_raw: None,
                answer: None,
            }))
            .collect();
        let ids = |v: &[QaItem]| v.iter().map(|i| i.id.clone().unwrap()).collect::<Vec<_>>();
        let picked = stratified(items.clone(), 4, 7);
        let count = |c: i64| picked.iter().filter(|i| i.category == c).count();
        assert_eq!([count(1), count(2), count(3), count(4)], [4, 4, 2, 4]);
        // Same seed, same sample, in file order; another seed differs.
        assert_eq!(ids(&picked), ids(&stratified(items.clone(), 4, 7)));
        let mut sorted = ids(&picked);
        sorted.sort_by_key(|id| id[5..].parse::<usize>().unwrap());
        assert_eq!(ids(&picked), sorted);
        assert_ne!(ids(&picked), ids(&stratified(items, 4, 8)));
    }

    #[test]
    fn sample_below_one_per_category_is_refused() {
        let mut db = crate::config::DbConfig::from_env();
        db.dbname = locomo::BENCH_DATABASE.into();
        let dir = tempfile::tempdir().unwrap();
        let out = dir.path().join("answers.jsonl");
        let opts = Options {
            qa: dir.path().join("missing-qa.json"),
            out: out.clone(),
            sample: Some(3),
            seed: 7,
            raw: false,
        };
        let err = run(&db, &opts).unwrap_err().to_string();
        assert!(err.contains("--sample takes at least 4"), "{err}");
        assert!(!out.exists());
    }

    fn raw_item(sample: &str, question: &str) -> QaItem {
        QaItem {
            id: Some(format!("{sample}#0")),
            sample_id: sample.to_string(),
            category: 4,
            question: question.to_string(),
            answer: Some(serde_json::json!("By the lake")),
            evidence: vec![],
            evidence_raw: None,
        }
    }

    fn ask_answer(sources: usize) -> crate::ask::AskAnswer {
        crate::ask::AskAnswer {
            question: "q".into(),
            answer: "By the lake [1].".into(),
            model: "fake".into(),
            local: true,
            cited: true,
            sources: (0..sources)
                .map(|i| crate::ask::SourceRef {
                    conversation_id: 40 + i as i64,
                    session_id: uuid::Uuid::nil(),
                    project_name: Some("demo".into()),
                    snippet: format!("s{i}"),
                })
                .collect(),
            semantic_skipped: None,
            generation_error: None,
        }
    }

    #[test]
    fn raw_answer_needs_its_semantic_half() {
        let item = raw_item("demo", "Where does Odile walk?");
        // The baseline as measured: an answer with its sessions.
        let row = raw_row(&item, "fake", ask_answer(2));
        assert_eq!(row.answer.as_deref(), Some("By the lake [1]."));
        assert_eq!(row.error, None);
        assert_eq!(row.context_session_ids, vec![40, 41]);
        // Word search only, no excerpts, or a failed model call: no answer.
        let mut skipped = ask_answer(2);
        skipped.semantic_skipped = Some("no embedding backend: refused".into());
        let mut failed = ask_answer(2);
        failed.generation_error = Some("timeout".into());
        for (answer, why) in [
            (skipped, "semantic search skipped: no embedding backend"),
            (ask_answer(0), "no excerpts found"),
            (failed, "generation failed: timeout"),
        ] {
            let row = raw_row(&item, "fake", answer);
            assert_eq!(row.answer, None, "{why}");
            assert!(
                row.error.as_deref().unwrap_or("").starts_with(why),
                "{row:?}"
            );
        }
        // With Ollama down, `ask` itself says the semantic half was skipped,
        // although word search still finds the turn; no use is counted.
        let Some(mut client) = crate::config::DbConfig::from_env().connect().ok() else {
            eprintln!("skipping the database half of raw_answer_needs_its_semantic_half");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let sample = format!("demo-{tag}");
        let conv: i64 = tx
            .query_one(
                "INSERT INTO conversations (session_id, project_path, started_at, summary, source_tool) \
                 VALUES ($1, $2, now(), 'demo session', 'locomo') RETURNING id",
                &[&uuid::Uuid::new_v4(), &sample],
            )
            .unwrap()
            .get(0);
        tx.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) \
             VALUES ($1, 'user', $2, now())",
            &[&conv, &format!("Odile: a walk by the lake {tag}.")],
        )
        .unwrap();
        let note = crate::access::team_test::note(
            &mut tx,
            None,
            "shared",
            &format!("Odile walks by the lake {tag}."),
            "insight",
            Some(&sample),
        );
        let used =
            |tx: &mut postgres::Transaction| -> (i64, Option<chrono::DateTime<chrono::Utc>>) {
                let r = tx
                    .query_one(
                        "SELECT COALESCE(access_count, 0)::bigint, last_accessed \
                     FROM memory_chunks WHERE id = $1",
                        &[&note],
                    )
                    .unwrap();
                (r.get(0), r.get(1))
            };
        let before = used(&mut tx);
        let backend = GenerationBackend {
            kind: llm::BackendKind::Ollama,
            label: "fake".into(),
            base_url: "http://127.0.0.1:9".into(),
            model: "fake".into(),
            api_key: None,
        };
        let answer = crate::recall::test_ollama::unreachable(|| {
            crate::ask::ask_with(
                &mut tx,
                &LOCAL,
                &tag,
                &backend,
                RAW_LIMIT,
                Some(&sample),
                |_| Ok("By the lake [1].".into()),
            )
        })
        .expect("ask");
        assert!(!answer.sources.is_empty(), "word search finds the turn");
        assert!(answer.semantic_skipped.is_some());
        let row = raw_row(&raw_item(&sample, &tag), "fake", answer);
        assert_eq!(row.answer, None);
        assert!(
            row.error
                .as_deref()
                .unwrap_or("")
                .starts_with("semantic search skipped"),
            "{row:?}"
        );
        assert_eq!(used(&mut tx), before, "the raw path counts no use");
    }

    #[test]
    fn bench_answer_builds_context_for_the_sample_project() {
        let Some(mut client) = crate::config::DbConfig::from_env().connect().ok() else {
            eprintln!(
                "skipping bench_answer_builds_context_for_the_sample_project: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let model = format!("test-embed-{tag}");
        let sample = format!("demo-{tag}");
        let other = format!("other-{tag}");
        let ct = crate::extract_memory::category_column_type(&mut tx).unwrap();
        let mut v = vec![0.0f32; 768];
        v[0] = 1.0;
        let vector = crate::generate_embeddings::vector_literal(&v);
        let mut notes: Vec<(String, i64)> = Vec::new();
        // Two synthetic dialogues with the same kind of notes, all equally near the question.
        for project in [&sample, &other] {
            let conv: i64 = tx
                .query_one(
                    "INSERT INTO conversations (session_id, project_path, started_at, summary, source_tool) \
                     VALUES ($1, $2, now(), 'demo session', 'locomo') RETURNING id",
                    &[&uuid::Uuid::new_v4(), project],
                )
                .unwrap()
                .get(0);
            tx.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) \
                 VALUES ($1, 'user', 'Odile: a walk by the lake.', now())",
                &[&conv],
            )
            .unwrap();
            for (category, text) in [
                ("insight", format!("Odile walks by the lake in {project}.")),
                ("preference", format!("Odile prefers tea in {project}.")),
            ] {
                let id: i64 = tx
                    .query_one(
                        &format!(
                            "INSERT INTO memory_chunks (source_type, source_id, content, category, \
                             project_name, confidence, status, source_at, valid_from) \
                             VALUES ('conversation', $1, $2, $3::text::{ct}, $4, 0.9, 'active', \
                                     now(), now()) RETURNING id"
                        ),
                        &[&conv, &text, &category, project],
                    )
                    .unwrap()
                    .get(0);
                tx.execute(
                    "INSERT INTO embeddings (source_type, source_id, model, embedding_768) \
                     VALUES ('memory_chunk', $1, $2, $3::text::vector)",
                    &[&id, &model, &vector],
                )
                .unwrap();
                notes.push((project.clone(), id));
            }
        }
        let all: Vec<i64> = notes.iter().map(|(_, id)| *id).collect();
        let used = |tx: &mut postgres::Transaction| -> i64 {
            tx.query_one(
                "SELECT COALESCE(sum(access_count), 0)::bigint FROM memory_chunks WHERE id = ANY($1)",
                &[&all],
            )
            .unwrap()
            .get(0)
        };
        let before = used(&mut tx);
        let item = QaItem {
            id: Some(format!("{sample}#0")),
            sample_id: sample.clone(),
            category: 4,
            question: "Where does Odile walk?".into(),
            answer: Some(serde_json::json!("By the lake")),
            evidence: vec![],
            evidence_raw: None,
        };
        let emb = QueryEmbedding {
            model,
            column: "embedding_768",
            vector: v,
            min_cosine: 0.5,
        };
        let mut seen = (String::new(), String::new());
        let row = answer_memory(&mut tx, &item, Some(&emb), "fake", |system, prompt| {
            seen = (system.to_string(), prompt.to_string());
            Ok("By the lake.".into())
        })
        .expect("answer");
        assert_eq!(row.question_id, format!("{sample}#0"));
        assert_eq!(row.sample_id, sample);
        assert_eq!(row.gold, "By the lake");
        assert_eq!(row.answer.as_deref(), Some("By the lake."));
        // The pack cites the sample's notes, and only those.
        assert!(!row.context_note_ids.is_empty(), "{row:?}");
        let projects: Vec<Option<String>> = tx
            .query(
                "SELECT DISTINCT project_name FROM memory_chunks WHERE id = ANY($1)",
                &[&row.context_note_ids],
            )
            .unwrap()
            .iter()
            .map(|r| r.get(0))
            .collect();
        assert_eq!(projects, vec![Some(sample.clone())]);
        for (project, id) in &notes {
            assert_eq!(
                row.context_note_ids.contains(id),
                project == &sample,
                "{row:?}"
            );
        }
        // The model gets the production answer prompt around that pack.
        assert_eq!(seen.0, crate::ask::ANSWER_SYSTEM);
        assert!(
            seen.1.contains("Question: Where does Odile walk?"),
            "{}",
            seen.1
        );
        assert!(seen.1.contains(&format!("lake in {sample}")), "{}", seen.1);
        assert!(!seen.1.contains(&other), "{}", seen.1);
        // A benchmark answer counts no use.
        assert_eq!(used(&mut tx), before);
    }
}
