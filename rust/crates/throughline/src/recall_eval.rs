//! Measure recall against simpler alternatives on a fixed question set.
//!
//! Every method ranks the same candidates as `recall` in mode `current`
//! (except `phrase`, today's whole-phrase search, which is context only).
//! `fulltext` and `weighted` go through `recall` itself; `fulltext` was
//! labelled `current` until 2026-10-31, when it stopped being the default.
//! `cosine` sorts the candidates by their cosine, as the default rank does
//! for notes with a vector.
//!
//! [`run_locomo`] measures retrieval on the LoCoMo benchmark at the session
//! level, through `recall` itself with every rank.

use crate::access::Actor;
use crate::adapters::locomo;
use crate::recall::{self, Candidate, CandidateFilter, Rank, RecallMode, RecallQuery, Weights};
use crate::search;
use anyhow::{bail, Context, Result};
use chrono::{DateTime, Utc};
use postgres::GenericClient;
use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::Path;

/// Ranks beyond this are reported as "not found" for every method alike.
const RANK_DEPTH: usize = 50;

/// `recall-eval` is a CLI command: it sees every note, like local mode.
static LOCAL: Actor = Actor::local();

#[derive(Debug, Clone, Deserialize)]
pub struct EvalCase {
    pub q: String,
    pub project: Option<String>,
    pub expected: Vec<i64>,
}

#[derive(Debug, Clone, Serialize)]
pub struct MethodScore {
    pub method: String,
    pub hit_at_k: f64,
    pub mrr: f64,
    pub n: usize,
}

#[derive(Debug, Serialize)]
pub struct CaseRanks {
    pub q: String,
    pub expected: Vec<i64>,
    /// 1-based rank of the best expected note per method; null when not found.
    pub ranks: std::collections::BTreeMap<String, Option<usize>>,
}

#[derive(Debug, Serialize)]
pub struct EvalReport {
    pub k: usize,
    pub seed: u64,
    /// The weights `weighted` ranked with.
    pub weights: Weights,
    pub scores: Vec<MethodScore>,
    pub cases: Vec<CaseRanks>,
}

/// The six parts of one candidate's weighted score, so the ranking can be
/// redone offline with other weights.
#[derive(Debug, Clone, Serialize)]
pub struct CandidateParts {
    pub id: i64,
    /// Missing only when the question has no vector; `score` then scales the
    /// other five by 1 / (1 - w.cosine). A note without a vector has 0.
    pub cosine: Option<f64>,
    pub lexical: f64,
    pub recency: f64,
    pub reliability: f64,
    pub strength: f64,
    pub provenance: f64,
    /// Whether the note has a vector of the question's model.
    pub has_vector: bool,
}

/// Every candidate of one question (0-based, in the order of the test set).
#[derive(Debug, Serialize)]
pub struct QuestionParts {
    pub question_index: usize,
    pub candidates: Vec<CandidateParts>,
}

/// The parts of `cands` as `recall::score` computes them at `now`.
pub(crate) fn candidate_parts(
    cands: &[Candidate],
    words: &[String],
    has_embedding: bool,
    now: DateTime<Utc>,
) -> Vec<CandidateParts> {
    cands
        .iter()
        .map(|c| {
            // The parts do not depend on the weights.
            let (_, why) = recall::score(c, words, has_embedding, now, &recall::WEIGHTS);
            CandidateParts {
                id: c.id,
                cosine: why.cosine,
                lexical: why.lexical,
                recency: why.recency,
                reliability: why.reliability,
                strength: why.strength,
                provenance: why.provenance,
                has_vector: c.cosine.is_some(),
            }
        })
        .collect()
}

/// `--weights c,l,r,rel,s,p`: six numbers in the order of `Weights`, none
/// negative, summing to 1.
pub fn parse_weights(s: &str) -> Result<Weights, String> {
    let v = s
        .split(',')
        .map(|x| {
            x.trim()
                .parse::<f64>()
                .map_err(|e| format!("weight {:?}: {e}", x.trim()))
        })
        .collect::<Result<Vec<f64>, String>>()?;
    let &[cosine, lexical, recency, reliability, strength, provenance] = v.as_slice() else {
        return Err(format!(
            "6 weights needed (cosine,lexical,recency,reliability,strength,provenance), got {}",
            v.len()
        ));
    };
    if v.iter().any(|x| !x.is_finite() || *x < 0.0) {
        return Err("weights must be finite and not negative".to_string());
    }
    let sum: f64 = v.iter().sum();
    if (sum - 1.0).abs() > 1e-6 {
        return Err(format!("weights sum to {sum}, not 1"));
    }
    Ok(Weights {
        cosine,
        lexical,
        recency,
        reliability,
        strength,
        provenance,
    })
}

pub fn load_cases(path: &Path) -> Result<Vec<EvalCase>> {
    let text = std::fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
    serde_json::from_str(&text).context("parse test set")
}

/// 1-based rank of the first expected id in `ranked`, within `RANK_DEPTH`.
fn rank_of(ranked: &[i64], expected: &[i64]) -> Option<usize> {
    ranked
        .iter()
        .take(RANK_DEPTH)
        .position(|id| expected.contains(id))
        .map(|p| p + 1)
}

/// Hit rate within the top `k` and mean reciprocal rank over all cases.
pub fn hit_at_k_and_mrr(ranks: &[Option<usize>], k: usize) -> (f64, f64) {
    if ranks.is_empty() {
        return (0.0, 0.0);
    }
    let n = ranks.len() as f64;
    let hits = ranks.iter().filter(|r| r.is_some_and(|r| r <= k)).count() as f64;
    let rr: f64 = ranks.iter().flatten().map(|&r| 1.0 / r as f64).sum();
    (hits / n, rr / n)
}

/// Small deterministic generator (xorshift64*), enough for a fixed shuffle.
pub(crate) struct Rng(u64);

impl Rng {
    pub(crate) fn new(seed: u64) -> Self {
        Rng(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1)
    }
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
}

pub(crate) fn shuffle(ids: &mut [i64], rng: &mut Rng) {
    for i in (1..ids.len()).rev() {
        let j = (rng.next() % (i as u64 + 1)) as usize;
        ids.swap(i, j);
    }
}

pub const METHODS: [&str; 5] = ["fulltext", "weighted", "cosine", "phrase", "random"];

/// Ranks every case by each method. `weighted` uses `weights`; every
/// question is scored at one `now`. With `parts`, the score parts of all
/// candidates are collected per question at that same `now`.
pub fn run_eval<C: GenericClient>(
    client: &mut C,
    cases: &[EvalCase],
    k: usize,
    seed: u64,
    weights: &Weights,
    mut parts: Option<&mut Vec<QuestionParts>>,
) -> Result<EvalReport> {
    let now = Utc::now();
    let mut rng = Rng::new(seed);
    let mut per_method: Vec<Vec<Option<usize>>> = vec![Vec::new(); METHODS.len()];
    let mut out_cases = Vec::new();
    for (question_index, case) in cases.iter().enumerate() {
        if case.expected.is_empty() {
            bail!("test set case without expected ids: {}", case.q);
        }
        let emb = recall::query_embedding(&case.q)?;
        if emb.is_none() {
            bail!("no query embedding (is Ollama reachable?): {}", case.q);
        }
        let mut q = RecallQuery {
            // An operator's evaluation over the whole corpus, like every CLI command.
            actor: &LOCAL,
            text: &case.q,
            project: case.project.as_deref(),
            mode: RecallMode::Current,
            valid_at: None,
            known_at: None,
            limit: RANK_DEPTH,
            rank: Rank::Fulltext,
            sources: &[],
        };
        let mut ranked = |q: &RecallQuery| -> Result<Vec<i64>> {
            Ok(recall::recall_with(client, q, emb.as_ref(), weights, now)?
                .hits
                .iter()
                .map(|h| h.id)
                .collect())
        };
        let fulltext = ranked(&q)?;
        q.rank = Rank::Weighted;
        let weighted = ranked(&q)?;
        q.rank = Rank::Fulltext;
        let cands = recall::candidates(client, CandidateFilter::Current, &q, emb.as_ref())?;
        if let Some(parts) = parts.as_mut() {
            let words = recall::query_words(&case.q);
            parts.push(QuestionParts {
                question_index,
                candidates: candidate_parts(&cands, &words, emb.is_some(), now),
            });
        }

        // Notes without an embedding rank after all that have one.
        let mut by_cosine: Vec<(i64, Option<f64>)> =
            cands.iter().map(|c| (c.id, c.cosine)).collect();
        by_cosine.sort_by(|a, b| match (a.1, b.1) {
            (Some(x), Some(y)) => y.total_cmp(&x).then(a.0.cmp(&b.0)),
            (Some(_), None) => Ordering::Less,
            (None, Some(_)) => Ordering::Greater,
            (None, None) => a.0.cmp(&b.0),
        });
        let cosine: Vec<i64> = by_cosine.into_iter().map(|c| c.0).collect();

        let ids: Vec<i64> = cands.iter().map(|c| c.id).collect();

        let phrase: Vec<i64> = search::memory_hits(client, &LOCAL, &case.q, RANK_DEPTH as i32)?
            .iter()
            .filter_map(|h| h.message_id)
            .collect();

        let mut random = ids.clone();
        random.sort_unstable();
        shuffle(&mut random, &mut rng);

        let lists = [fulltext, weighted, cosine, phrase, random];
        let mut ranks = std::collections::BTreeMap::new();
        for (i, list) in lists.iter().enumerate() {
            let r = rank_of(list, &case.expected);
            per_method[i].push(r);
            ranks.insert(METHODS[i].to_string(), r);
        }
        out_cases.push(CaseRanks {
            q: case.q.clone(),
            expected: case.expected.clone(),
            ranks,
        });
    }
    let scores = METHODS
        .iter()
        .zip(&per_method)
        .map(|(m, ranks)| {
            let (hit, mrr) = hit_at_k_and_mrr(ranks, k);
            MethodScore {
                method: m.to_string(),
                hit_at_k: hit,
                mrr,
                n: ranks.len(),
            }
        })
        .collect();
    Ok(EvalReport {
        k,
        seed,
        weights: *weights,
        scores,
        cases: out_cases,
    })
}

/// One question of a LoCoMo QA file, `{"items": [...]}`.
#[derive(Debug, Clone, Deserialize)]
pub struct QaItem {
    /// The question's id in the file, like `<sample>#<n>`.
    #[serde(default)]
    pub id: Option<String>,
    /// The dialogue, which is the project of its sessions' notes.
    pub sample_id: String,
    pub category: i64,
    pub question: String,
    /// Turn ids (`D<session>:<turn>`), several per string allowed.
    #[serde(default)]
    pub evidence: Vec<serde_json::Value>,
    /// The ids as the data file has them, when the QA file keeps a cleaned
    /// copy in `evidence`; read in its place, so unreadable ids are counted.
    #[serde(default)]
    pub evidence_raw: Option<Vec<serde_json::Value>>,
    /// The gold answer (a string or a number; none in category 5).
    #[serde(default)]
    pub answer: Option<serde_json::Value>,
}

impl QaItem {
    fn evidence_ids(&self) -> &[serde_json::Value] {
        self.evidence_raw.as_deref().unwrap_or(&self.evidence)
    }
}

#[derive(Deserialize)]
struct QaFile {
    items: Vec<QaItem>,
}

/// Categories 1 to 4 make the headline; 5 (adversarial) and any other
/// category are reported on their own.
pub(crate) const HEADLINE: std::ops::RangeInclusive<i64> = 1..=4;
const HEADLINE_LABEL: &str = "1-4";

/// One rank and category: `hit[i]` and `coverage[i]` belong to `ks[i]`.
#[derive(Debug, Clone, Serialize)]
pub struct LocomoRow {
    pub rank: String,
    /// A category number, or `1-4` for the headline.
    pub category: String,
    pub n: usize,
    /// Share of questions with a note from an evidence session in the top k.
    pub hit: Vec<f64>,
    /// Mean share of a question's evidence sessions with a note in the top k.
    pub coverage: Vec<f64>,
}

#[derive(Debug, Default, Serialize)]
pub struct LocomoReport {
    pub ks: Vec<usize>,
    /// The default rank first.
    pub ranks: Vec<String>,
    /// Questions in the file.
    pub questions: usize,
    /// Questions scored: their dialogue has notes and an evidence id names a turn.
    pub measured: usize,
    /// Dialogues measured: they have notes or were extracted.
    pub samples_measured: usize,
    /// Measured dialogues extracted to zero notes: their questions count as misses.
    pub samples_without_notes: usize,
    /// Dialogues neither extracted nor with notes in the database: not measured.
    pub samples_skipped: usize,
    /// Questions of dialogues not measured.
    pub skipped_questions: usize,
    /// Questions of measured dialogues whose evidence names no turn.
    pub without_evidence: usize,
    /// Evidence ids (in measured dialogues) that name no turn of the dialogue.
    pub unreadable_ids: usize,
    /// Questions per rank whose result said `degraded`.
    pub degraded: BTreeMap<String, usize>,
    pub rows: Vec<LocomoRow>,
}

/// Hits and covered shares of one rank and category, summed over questions.
#[derive(Debug, Clone)]
struct Tally {
    n: usize,
    hits: Vec<usize>,
    covered: Vec<f64>,
}

impl Tally {
    fn new(ks: usize) -> Self {
        Tally {
            n: 0,
            hits: vec![0; ks],
            covered: vec![0.0; ks],
        }
    }

    /// One question: `convs` are the returned notes' conversations in rank order.
    fn count(&mut self, convs: &[Option<i64>], sessions: &BTreeSet<i64>, ks: &[usize]) {
        self.n += 1;
        for (j, &k) in ks.iter().enumerate() {
            let covered: BTreeSet<i64> = convs
                .iter()
                .take(k)
                .flatten()
                .filter(|c| sessions.contains(c))
                .copied()
                .collect();
            if !covered.is_empty() {
                self.hits[j] += 1;
            }
            self.covered[j] += covered.len() as f64 / sessions.len() as f64;
        }
    }

    fn add(&mut self, other: &Tally) {
        self.n += other.n;
        for (a, b) in self.hits.iter_mut().zip(&other.hits) {
            *a += b;
        }
        for (a, b) in self.covered.iter_mut().zip(&other.covered) {
            *a += b;
        }
    }

    fn row(&self, rank: &str, category: String) -> LocomoRow {
        let n = self.n.max(1) as f64;
        LocomoRow {
            rank: rank.to_string(),
            category,
            n: self.n,
            hit: self.hits.iter().map(|&h| h as f64 / n).collect(),
            coverage: self.covered.iter().map(|&c| c / n).collect(),
        }
    }
}

pub fn load_qa(path: &Path) -> Result<Vec<QaItem>> {
    let text = std::fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
    Ok(serde_json::from_str::<QaFile>(&text)
        .context("parse QA file")?
        .items)
}

/// The default rank first, then the others.
fn locomo_ranks() -> Vec<Rank> {
    let mut ranks = vec![recall::DEFAULT_RANK];
    for r in [Rank::Cosine, Rank::Fulltext, Rank::Weighted] {
        if !ranks.contains(&r) {
            ranks.push(r);
        }
    }
    ranks
}

/// A measured dialogue: the session (conversation) of every turn, and
/// whether it has notes.
struct Dialogue {
    turns: HashMap<String, i64>,
    has_notes: bool,
}

/// Dialogue `sample` as measured, or `None` when it has no notes and none of
/// its sessions was extracted, so its questions cannot be measured. A
/// dialogue extracted to zero notes is measured: its questions are misses.
/// A turn id in two sessions of the dialogue is an error.
fn dialogue_turns<C: GenericClient>(client: &mut C, sample: &str) -> Result<Option<Dialogue>> {
    let row = client.query_one(
        "SELECT EXISTS (SELECT 1 FROM memory_chunks WHERE project_name = $1), \
                EXISTS (SELECT 1 FROM conversations WHERE source_tool = 'locomo' \
                        AND project_path = $1 AND memory_extracted_until IS NOT NULL)",
        &[&sample],
    )?;
    let (has_notes, extracted): (bool, bool) = (row.get(0), row.get(1));
    if !has_notes && !extracted {
        return Ok(None);
    }
    let rows = client.query(
        "SELECT m.metadata->>'dia_id', m.conversation_id FROM messages m \
         JOIN conversations c ON c.id = m.conversation_id \
         WHERE c.source_tool = 'locomo' AND c.project_path = $1 AND m.metadata ? 'dia_id'",
        &[&sample],
    )?;
    let mut turns = HashMap::new();
    for r in &rows {
        let (id, session): (String, i64) = (r.get(0), r.get(1));
        if let Some(other) = turns.insert(id.clone(), session) {
            if other != session {
                bail!("turn {id} of dialogue {sample} is in two sessions ({other} and {session})");
            }
        }
    }
    Ok(Some(Dialogue { turns, has_notes }))
}

/// The session each note of `ids` was extracted from: its own source, not
/// that of its chain's first version. A note from no conversation has none.
fn own_sessions<C: GenericClient>(client: &mut C, ids: &[i64]) -> Result<HashMap<i64, i64>> {
    let rows = client.query(
        "SELECT id, source_id FROM memory_chunks \
         WHERE id = ANY($1) AND source_type = 'conversation' AND source_id IS NOT NULL",
        &[&ids],
    )?;
    Ok(rows.iter().map(|r| (r.get(0), r.get(1))).collect())
}

/// LoCoMo retrieval at the session level. Each question goes through
/// `recall` in mode `current`, project = its dialogue, once per rank; no use
/// is counted. A hit at k: a note among the top k was extracted from a
/// session that holds an evidence turn of the question (the note's own
/// session, not that of the first version it replaced). `embed` gives the
/// question vector.
pub fn run_locomo<C: GenericClient>(
    client: &mut C,
    items: &[QaItem],
    ks: &[usize],
    mut embed: impl FnMut(&str) -> Result<Option<recall::QueryEmbedding>>,
) -> Result<LocomoReport> {
    let mut ks = ks.to_vec();
    ks.sort_unstable();
    ks.dedup();
    if ks.is_empty() || ks.iter().any(|&k| k == 0 || k > RANK_DEPTH) {
        bail!("--k takes values from 1 to {RANK_DEPTH}");
    }
    let depth = ks[ks.len() - 1];
    let ranks = locomo_ranks();
    let mut report = LocomoReport {
        ks: ks.clone(),
        ranks: ranks.iter().map(|r| r.as_str().to_string()).collect(),
        questions: items.len(),
        ..Default::default()
    };
    let mut dialogues: HashMap<&str, Option<Dialogue>> = HashMap::new();
    let mut tallies: BTreeMap<i64, Vec<Tally>> = BTreeMap::new();
    for (index, item) in items.iter().enumerate() {
        let sample = item.sample_id.as_str();
        if !dialogues.contains_key(sample) {
            let turns = dialogue_turns(client, sample)?;
            dialogues.insert(sample, turns);
        }
        let Some(dialogue) = &dialogues[sample] else {
            report.skipped_questions += 1;
            continue;
        };
        let turns = &dialogue.turns;
        let mut sessions = BTreeSet::new();
        for id in item.evidence_ids() {
            let tokens: Vec<&str> = id
                .as_str()
                .map(|s| locomo::evidence_tokens(s).collect())
                .unwrap_or_default();
            if tokens.is_empty() {
                report.unreadable_ids += 1;
            }
            for token in tokens {
                match turns.get(token) {
                    Some(&session) => {
                        sessions.insert(session);
                    }
                    None => report.unreadable_ids += 1,
                }
            }
        }
        if sessions.is_empty() {
            report.without_evidence += 1;
            continue;
        }
        let emb = embed(&item.question)?;
        if emb.is_none() {
            bail!("no query embedding for question {index} (is Ollama reachable?)");
        }
        report.measured += 1;
        let tally = tallies
            .entry(item.category)
            .or_insert_with(|| vec![Tally::new(ks.len()); ranks.len()]);
        for (i, &rank) in ranks.iter().enumerate() {
            let q = RecallQuery {
                // An operator's evaluation, like every CLI command.
                actor: &LOCAL,
                text: &item.question,
                project: Some(sample),
                mode: RecallMode::Current,
                valid_at: None,
                known_at: None,
                limit: depth,
                rank,
                sources: &[],
            };
            let result = recall::recall(client, &q, emb.as_ref())?;
            if result.degraded.is_some() {
                *report
                    .degraded
                    .entry(rank.as_str().to_string())
                    .or_default() += 1;
            }
            let ids: Vec<i64> = result.hits.iter().map(|h| h.id).collect();
            let own = own_sessions(client, &ids)?;
            let convs: Vec<Option<i64>> = ids.iter().map(|id| own.get(id).copied()).collect();
            tally[i].count(&convs, &sessions, &ks);
        }
    }
    report.samples_measured = dialogues.values().filter(|d| d.is_some()).count();
    report.samples_without_notes = dialogues
        .values()
        .flatten()
        .filter(|d| !d.has_notes)
        .count();
    report.samples_skipped = dialogues.len() - report.samples_measured;
    for (i, rank) in report.ranks.iter().enumerate() {
        let mut headline = Tally::new(ks.len());
        for (category, t) in tallies.range(HEADLINE) {
            report.rows.push(t[i].row(rank, category.to_string()));
            headline.add(&t[i]);
        }
        if headline.n > 0 {
            report
                .rows
                .push(headline.row(rank, HEADLINE_LABEL.to_string()));
        }
        for (category, t) in tallies.iter().filter(|(c, _)| !HEADLINE.contains(c)) {
            report.rows.push(t[i].row(rank, category.to_string()));
        }
    }
    Ok(report)
}

pub fn render_locomo_text(r: &LocomoReport) -> String {
    let mut s = format!("{:<10} {:<8} {:>5}", "rank", "category", "n");
    for k in &r.ks {
        s.push_str(&format!(
            " {:>7} {:>7}",
            format!("hit@{k}"),
            format!("cov@{k}")
        ));
    }
    s.push('\n');
    for row in &r.rows {
        s.push_str(&format!(
            "{:<10} {:<8} {:>5}",
            row.rank, row.category, row.n
        ));
        for (hit, cov) in row.hit.iter().zip(&row.coverage) {
            s.push_str(&format!(" {:>6.1}% {:>6.1}%", hit * 100.0, cov * 100.0));
        }
        s.push('\n');
    }
    s.push_str(&format!(
        "headline: categories {HEADLINE_LABEL}; any other category on its own\n\
         questions: {}, measured: {}, dialogues measured: {} ({} without notes, \
         their questions count as misses), dialogues not extracted: {} \
         ({} questions), without a readable evidence id: {}\n\
         unreadable evidence ids: {}\n",
        r.questions,
        r.measured,
        r.samples_measured,
        r.samples_without_notes,
        r.samples_skipped,
        r.skipped_questions,
        r.without_evidence,
        r.unreadable_ids
    ));
    let degraded: Vec<String> = r
        .ranks
        .iter()
        .map(|rank| format!("{rank} {}", r.degraded.get(rank).copied().unwrap_or(0)))
        .collect();
    s.push_str(&format!("degraded results: {}\n", degraded.join(", ")));
    s
}

pub fn render_text(r: &EvalReport) -> String {
    let mut s = format!("{:<10} {:>8} {:>8} {:>4}\n", "method", "hit@k", "MRR", "n");
    for m in &r.scores {
        s.push_str(&format!(
            "{:<10} {:>7.1}% {:>8.3} {:>4}\n",
            m.method,
            m.hit_at_k * 100.0,
            m.mrr,
            m.n
        ));
    }
    s.push_str(&format!("k = {}, seed = {}\n", r.k, r.seed));
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hit_at_k_and_mrr() {
        let ranks = [Some(1), Some(3), None, Some(6)];
        let (hit, mrr) = super::hit_at_k_and_mrr(&ranks, 5);
        assert!((hit - 0.5).abs() < 1e-12);
        assert!((mrr - (1.0 + 1.0 / 3.0 + 0.0 + 1.0 / 6.0) / 4.0).abs() < 1e-12);
        assert_eq!(super::hit_at_k_and_mrr(&[], 5), (0.0, 0.0));
    }

    #[test]
    fn methods_keep_the_weighted_ranking() {
        // `current` (full text through `recall`) is reported as `fulltext`
        // since 2026-10-31; result files before that keep the old label.
        assert_eq!(
            METHODS,
            ["fulltext", "weighted", "cosine", "phrase", "random"]
        );
    }

    fn test_db() -> Option<postgres::Client> {
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
        cfg.connect(postgres::NoTls).ok()
    }

    /// A synthetic benchmark dialogue: session 1 (turns D1:1, D1:2) holds
    /// one note, session 2 (turn D2:1) none. The note and every question
    /// share the word `word`, and only the note has a vector of `model`.
    struct Bench {
        sample: String,
        word: String,
        model: String,
        note: i64,
    }

    fn bench_fixture<C: GenericClient>(client: &mut C) -> Bench {
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let sample = format!("demo-{tag}");
        let word = format!("q{tag}");
        let model = format!("test-embed-{tag}");
        let mut session = |n: i64, turns: &[&str]| -> i64 {
            let conv: i64 = client
                .query_one(
                    "INSERT INTO conversations (session_id, project_path, started_at, summary, source_tool) \
                     VALUES ($1, $2, now(), $3, 'locomo') RETURNING id",
                    &[&uuid::Uuid::new_v4(), &sample, &format!("demo session {n}")],
                )
                .unwrap()
                .get(0);
            for t in turns {
                client
                    .execute(
                        "INSERT INTO messages (conversation_id, role, content, created_at, metadata) \
                         VALUES ($1, 'user', 'Odile: a walk by the lake.', now(), \
                                 jsonb_build_object('dia_id', $2::text))",
                        &[&conv, t],
                    )
                    .unwrap();
            }
            conv
        };
        let first = session(1, &["D1:1", "D1:2"]);
        session(2, &["D2:1"]);
        let ct = crate::extract_memory::category_column_type(client).unwrap();
        let note: i64 = client
            .query_one(
                &format!(
                    "INSERT INTO memory_chunks (source_type, source_id, content, category, \
                     project_name, confidence, status, access_count, last_accessed) \
                     VALUES ('conversation', $1, $2, 'insight'::text::{ct}, $3, 0.9, 'active', \
                             3, '2031-03-04T15:15:00Z') RETURNING id"
                ),
                &[&first, &format!("Odile walks by the lake {word}."), &sample],
            )
            .unwrap()
            .get(0);
        let mut v = vec![0.0f32; 768];
        v[0] = 1.0;
        client
            .execute(
                "INSERT INTO embeddings (source_type, source_id, model, embedding_768) \
                 VALUES ('memory_chunk', $1, $2, $3::text::vector)",
                &[
                    &note,
                    &model,
                    &crate::generate_embeddings::vector_literal(&v),
                ],
            )
            .unwrap();
        Bench {
            sample,
            word,
            model,
            note,
        }
    }

    impl Bench {
        fn embedding(&self) -> recall::QueryEmbedding {
            let mut v = vec![0.0f32; 768];
            v[0] = 1.0;
            recall::QueryEmbedding {
                model: self.model.clone(),
                column: "embedding_768",
                vector: v,
                min_cosine: 0.5,
            }
        }

        fn item(&self, category: i64, evidence: serde_json::Value) -> QaItem {
            QaItem {
                id: None,
                sample_id: self.sample.clone(),
                category,
                question: format!("Where is {}?", self.word),
                evidence: serde_json::from_value(evidence).unwrap(),
                evidence_raw: None,
                answer: None,
            }
        }
    }

    fn row<'a>(r: &'a LocomoReport, rank: &str, category: &str) -> &'a LocomoRow {
        r.rows
            .iter()
            .find(|x| x.rank == rank && x.category == category)
            .unwrap_or_else(|| panic!("no row {rank}/{category} in {:?}", r.rows))
    }

    #[test]
    fn locomo_mode_hits_on_evidence_session() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping locomo_mode_hits_on_evidence_session: no PostgreSQL reachable");
            return;
        };
        // Everything rolls back with the transaction.
        let mut tx = client.transaction().unwrap();
        let b = bench_fixture(&mut tx);
        let mut unknown = b.item(1, serde_json::json!(["D1:1"]));
        unknown.sample_id = format!("{}-absent", b.sample);
        let items = vec![
            // Evidence in the session that holds the note: a hit.
            b.item(1, serde_json::json!(["D1:2"])),
            // Evidence only in session 2: a miss; "D9:9" names no turn and
            // "D" is no id, two unreadable ids.
            b.item(2, serde_json::json!(["D2:1; D9:9", "D"])),
            // Two evidence sessions, the note covers one of them.
            b.item(1, serde_json::json!(["D1:1", "D2:1"])),
            // Adversarial: reported on its own, not in the headline.
            b.item(5, serde_json::json!(["D1:1"])),
            // No readable evidence id.
            b.item(3, serde_json::json!([])),
            // A dialogue the database does not hold.
            unknown,
        ];
        let report = run_locomo(&mut tx, &items, &[1, 5], |_| Ok(Some(b.embedding()))).unwrap();

        assert_eq!(report.ks, vec![1, 5]);
        assert_eq!(report.ranks, vec!["cosine", "fulltext", "weighted"]);
        assert_eq!(report.ranks[0], recall::DEFAULT_RANK.as_str());
        assert_eq!(report.questions, 6);
        assert_eq!(report.measured, 4);
        assert_eq!((report.samples_measured, report.samples_skipped), (1, 1));
        assert_eq!(report.skipped_questions, 1);
        assert_eq!(report.without_evidence, 1);
        assert_eq!(report.unreadable_ids, 2);
        for rank in &report.ranks {
            let one = row(&report, rank, "1");
            assert_eq!(one.n, 2, "{rank}");
            assert_eq!(one.hit, vec![1.0, 1.0], "{rank}");
            assert_eq!(one.coverage, vec![0.75, 0.75], "{rank}");
            let two = row(&report, rank, "2");
            assert_eq!((two.n, two.hit.clone()), (1, vec![0.0, 0.0]), "{rank}");
            assert_eq!(two.coverage, vec![0.0, 0.0], "{rank}");
            let headline = row(&report, rank, "1-4");
            assert_eq!(headline.n, 3, "{rank}");
            assert!((headline.hit[0] - 2.0 / 3.0).abs() < 1e-12, "{rank}");
            assert!((headline.coverage[1] - 0.5).abs() < 1e-12, "{rank}");
            let five = row(&report, rank, "5");
            assert_eq!((five.n, five.hit.clone()), (1, vec![1.0, 1.0]), "{rank}");
            assert!(
                !report
                    .rows
                    .iter()
                    .any(|x| x.rank == *rank && x.category == "3"),
                "a category without a measured question has no row"
            );
            assert_eq!(report.degraded.get(rank.as_str()).copied().unwrap_or(0), 0);
        }
        let text = render_locomo_text(&report);
        assert!(text.contains("1-4"), "{text}");
        assert!(text.contains("unreadable evidence ids: 2"), "{text}");
    }

    /// A new session of dialogue `sample` with turns `turns`, extracted when
    /// `extracted`.
    fn add_session<C: GenericClient>(
        client: &mut C,
        sample: &str,
        turns: &[&str],
        extracted: bool,
    ) -> i64 {
        let conv: i64 = client
            .query_one(
                "INSERT INTO conversations (session_id, project_path, started_at, summary, \
                 source_tool, memory_extracted_until) \
                 VALUES ($1, $2, now(), 'demo session', 'locomo', \
                         CASE WHEN $3 THEN now() END) RETURNING id",
                &[&uuid::Uuid::new_v4(), &sample, &extracted],
            )
            .unwrap()
            .get(0);
        for t in turns {
            client
                .execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at, metadata) \
                     VALUES ($1, 'user', 'Odile: a walk by the lake.', now(), \
                             jsonb_build_object('dia_id', $2::text))",
                    &[&conv, t],
                )
                .unwrap();
        }
        conv
    }

    /// The session that holds turn `dia_id` of dialogue `sample`.
    fn session_of<C: GenericClient>(client: &mut C, sample: &str, dia_id: &str) -> i64 {
        client
            .query_one(
                "SELECT m.conversation_id FROM messages m \
                 JOIN conversations c ON c.id = m.conversation_id \
                 WHERE c.project_path = $1 AND m.metadata->>'dia_id' = $2",
                &[&sample, &dia_id],
            )
            .unwrap()
            .get(0)
    }

    #[test]
    fn locomo_hit_counts_the_notes_own_session() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping locomo_hit_counts_the_notes_own_session: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let b = bench_fixture(&mut tx);
        let ct = crate::extract_memory::category_column_type(&mut tx).unwrap();
        // The fixture's note (session 1) replaced a first version from
        // session 3: the chain's root, whose session recall reports.
        let third = add_session(&mut tx, &b.sample, &["D3:1"], false);
        tx.execute(
            &format!(
                "INSERT INTO memory_chunks (source_type, source_id, content, category, \
                 project_name, confidence, status, superseded_by) \
                 VALUES ('conversation', $1, $2, 'insight'::text::{ct}, $3, 0.9, 'superseded', $4)"
            ),
            &[
                &third,
                &format!("Odile walked by the lake {}.", b.word),
                &b.sample,
                &b.note,
            ],
        )
        .unwrap();
        // A second current note, from session 2, less near the question.
        let second = session_of(&mut tx, &b.sample, "D2:1");
        let n2: i64 = tx
            .query_one(
                &format!(
                    "INSERT INTO memory_chunks (source_type, source_id, content, category, \
                     project_name, confidence, status) \
                     VALUES ('conversation', $1, $2, 'insight'::text::{ct}, $3, 0.9, 'active') \
                     RETURNING id"
                ),
                &[
                    &second,
                    &format!("Odile rests by the lake {}.", b.word),
                    &b.sample,
                ],
            )
            .unwrap()
            .get(0);
        let mut v = vec![0.0f32; 768];
        v[0] = 0.6;
        v[1] = 0.8;
        tx.execute(
            "INSERT INTO embeddings (source_type, source_id, model, embedding_768) \
             VALUES ('memory_chunk', $1, $2, $3::text::vector)",
            &[
                &n2,
                &b.model,
                &crate::generate_embeddings::vector_literal(&v),
            ],
        )
        .unwrap();
        let items = vec![
            // The note's own session: a hit.
            b.item(1, serde_json::json!(["D1:1"])),
            // Only the replaced version's session: a miss.
            b.item(2, serde_json::json!(["D3:1"])),
            // The second note's session: past k = 1 for the vector ranks.
            b.item(4, serde_json::json!(["D2:1"])),
        ];
        let report = run_locomo(&mut tx, &items, &[1, 5], |_| Ok(Some(b.embedding()))).unwrap();
        for rank in &report.ranks {
            assert_eq!(row(&report, rank, "1").hit[1], 1.0, "{rank}");
            assert_eq!(row(&report, rank, "2").hit, vec![0.0, 0.0], "{rank}");
            assert_eq!(row(&report, rank, "4").hit[1], 1.0, "{rank}");
        }
        // Cosine and weighted put the fixture's note first: k cuts the list.
        for rank in ["cosine", "weighted"] {
            assert_eq!(row(&report, rank, "1").hit, vec![1.0, 1.0], "{rank}");
            assert_eq!(row(&report, rank, "4").hit, vec![0.0, 1.0], "{rank}");
        }
    }

    #[test]
    fn locomo_turn_in_two_sessions_is_an_error() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping locomo_turn_in_two_sessions_is_an_error: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let b = bench_fixture(&mut tx);
        add_session(&mut tx, &b.sample, &["D1:1"], false);
        let items = vec![b.item(1, serde_json::json!(["D2:1"]))];
        let err = run_locomo(&mut tx, &items, &[5], |_| Ok(Some(b.embedding())))
            .expect_err("a turn in two sessions");
        assert!(err.to_string().contains("D1:1"), "{err}");
        assert!(err.to_string().contains("two sessions"), "{err}");
    }

    #[test]
    fn locomo_dialogue_extracted_to_no_notes_counts_misses() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping locomo_dialogue_extracted_to_no_notes_counts_misses: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().unwrap();
        let b = bench_fixture(&mut tx);
        // Extracted, and the extraction kept nothing.
        let empty = format!("{}-empty", b.sample);
        add_session(&mut tx, &empty, &["D1:1"], true);
        // Imported, never extracted.
        let pending = format!("{}-pending", b.sample);
        add_session(&mut tx, &pending, &["D1:1"], false);
        let on = |sample: &str| {
            let mut item = b.item(1, serde_json::json!(["D1:1"]));
            item.sample_id = sample.to_string();
            item
        };
        let items = vec![on(&b.sample), on(&empty), on(&pending)];
        let report = run_locomo(&mut tx, &items, &[1, 5], |_| Ok(Some(b.embedding()))).unwrap();
        assert_eq!(report.measured, 2);
        assert_eq!(report.samples_measured, 2);
        assert_eq!(report.samples_without_notes, 1);
        assert_eq!(report.samples_skipped, 1);
        assert_eq!(report.skipped_questions, 1);
        for rank in &report.ranks {
            let one = row(&report, rank, "1");
            assert_eq!((one.n, one.hit.clone()), (2, vec![0.5, 0.5]), "{rank}");
        }
        let text = render_locomo_text(&report);
        assert!(
            text.contains("dialogues measured: 2 (1 without notes"),
            "{text}"
        );
        assert!(text.contains("dialogues not extracted: 1"), "{text}");
    }

    #[test]
    fn qa_file_reads_raw_evidence_when_present() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("qa.json");
        std::fs::write(
            &path,
            r#"{"items": [
                {"sample_id": "demo-1", "category": 2, "question": "When?",
                 "evidence": ["D1:1"], "evidence_raw": ["D1:1", "D"]},
                {"sample_id": "demo-1", "category": 1, "question": "Who?", "evidence": ["D1:2"]},
                {"sample_id": "demo-1", "category": 5, "question": "Why?"}]}"#,
        )
        .unwrap();
        let items = load_qa(&path).unwrap();
        let ids = |i: usize| -> Vec<&str> {
            items[i]
                .evidence_ids()
                .iter()
                .map(|v| v.as_str().unwrap())
                .collect()
        };
        assert_eq!(ids(0), ["D1:1", "D"]);
        assert_eq!(ids(1), ["D1:2"]);
        assert!(ids(2).is_empty());
        assert_eq!(items[0].category, 2);
    }

    #[test]
    fn locomo_mode_counts_no_use() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping locomo_mode_counts_no_use: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let b = bench_fixture(&mut tx);
        let used = |tx: &mut postgres::Transaction| -> (i64, Option<DateTime<Utc>>) {
            let r = tx
                .query_one(
                    "SELECT COALESCE(access_count, 0)::bigint, last_accessed \
                     FROM memory_chunks WHERE id = $1",
                    &[&b.note],
                )
                .unwrap();
            (r.get(0), r.get(1))
        };
        let before = used(&mut tx);
        let items = vec![b.item(4, serde_json::json!(["D1:1"]))];
        let report = run_locomo(&mut tx, &items, &[5, 10], |_| Ok(Some(b.embedding()))).unwrap();
        // The note was returned by every rank, so it was read three times.
        for rank in &report.ranks {
            assert_eq!(row(&report, rank, "4").hit, vec![1.0, 1.0], "{rank}");
        }
        assert_eq!(report.ranks.len(), 3);
        assert_eq!(
            used(&mut tx),
            before,
            "access_count and last_accessed unchanged"
        );
    }

    #[test]
    fn shuffle_is_deterministic_per_seed() {
        let base: Vec<i64> = (1..=20).collect();
        let mut a = base.clone();
        let mut b = base.clone();
        let mut c = base.clone();
        shuffle(&mut a, &mut Rng::new(7));
        shuffle(&mut b, &mut Rng::new(7));
        shuffle(&mut c, &mut Rng::new(8));
        assert_eq!(a, b);
        assert_ne!(a, c);
    }
}
