//! Measure recall against simpler alternatives on a fixed question set.
//!
//! Every method ranks the same candidates as `recall` in mode `current`
//! (except `phrase`, today's whole-phrase search, which is context only).
//! `current` uses the default rank, full text, so it equals `fulltext` by
//! construction; `weighted` is the weighted ranking.

use crate::recall::{self, CandidateFilter, Rank, RecallMode, RecallQuery};
use crate::search;
use anyhow::{bail, Context, Result};
use postgres::GenericClient;
use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::path::Path;

/// Ranks beyond this are reported as "not found" for every method alike.
const RANK_DEPTH: usize = 50;

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
    pub scores: Vec<MethodScore>,
    pub cases: Vec<CaseRanks>,
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
struct Rng(u64);

impl Rng {
    fn new(seed: u64) -> Self {
        Rng(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1)
    }
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
}

fn shuffle(ids: &mut [i64], rng: &mut Rng) {
    for i in (1..ids.len()).rev() {
        let j = (rng.next() % (i as u64 + 1)) as usize;
        ids.swap(i, j);
    }
}

pub const METHODS: [&str; 6] = [
    "current", "weighted", "cosine", "fulltext", "phrase", "random",
];

pub fn run_eval<C: GenericClient>(
    client: &mut C,
    cases: &[EvalCase],
    k: usize,
    seed: u64,
) -> Result<EvalReport> {
    let mut rng = Rng::new(seed);
    let mut per_method: Vec<Vec<Option<usize>>> = vec![Vec::new(); METHODS.len()];
    let mut out_cases = Vec::new();
    for case in cases {
        if case.expected.is_empty() {
            bail!("test set case without expected ids: {}", case.q);
        }
        let emb = recall::query_embedding(&case.q)?;
        if emb.is_none() {
            bail!("no query embedding (is Ollama reachable?): {}", case.q);
        }
        let mut q = RecallQuery {
            text: &case.q,
            project: case.project.as_deref(),
            mode: RecallMode::Current,
            valid_at: None,
            known_at: None,
            limit: RANK_DEPTH,
            rank: Rank::Fulltext,
        };
        let mut ranked = |q: &RecallQuery| -> Result<Vec<i64>> {
            Ok(recall::recall(client, q, emb.as_ref())?
                .hits
                .iter()
                .map(|h| h.id)
                .collect())
        };
        let current = ranked(&q)?;
        q.rank = Rank::Weighted;
        let weighted = ranked(&q)?;
        q.rank = Rank::Fulltext;
        let cands = recall::candidates(client, CandidateFilter::Current, &q, emb.as_ref())?;

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
        let fulltext = {
            let ranks = recall::fulltext_ranks(client, &recall::query_words(&case.q), &ids)?;
            let mut scored: Vec<(i64, f64)> = ids
                .iter()
                .map(|id| (*id, ranks.get(id).copied().unwrap_or(0.0)))
                .collect();
            // ts_rank is 0 without a match; those rank after all matches.
            scored.sort_by(|a, b| b.1.total_cmp(&a.1).then(a.0.cmp(&b.0)));
            scored.into_iter().map(|s| s.0).collect::<Vec<i64>>()
        };

        let phrase: Vec<i64> = search::memory_hits(client, &case.q, RANK_DEPTH as i32)?
            .iter()
            .filter_map(|h| h.message_id)
            .collect();

        let mut random = ids.clone();
        random.sort_unstable();
        shuffle(&mut random, &mut rng);

        let lists = [current, weighted, cosine, fulltext, phrase, random];
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
        scores,
        cases: out_cases,
    })
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
        assert_eq!(
            METHODS,
            ["current", "weighted", "cosine", "fulltext", "phrase", "random"]
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
