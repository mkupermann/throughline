//! Answer-quality evaluation (R-eval).
//!
//! Retrieval evals measure what comes back. This measures whether the answer
//! stays inside those excerpts, cites them correctly, and does not invent.

use crate::ask::{self, AskAnswer};
use crate::llm::GenerationBackend;
use anyhow::Result;
use postgres::GenericClient;
use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct EvalCase {
    pub id: String,
    pub question: String,
    /// Optional must-cite conversation ids (when known).
    #[serde(default)]
    pub must_cite_conversation_ids: Vec<i64>,
    /// Phrases that must appear in the answer if the case is grounded.
    #[serde(default)]
    pub expected_phrases: Vec<String>,
    /// Phrases that must NOT appear (hallucination tripwires).
    #[serde(default)]
    pub forbidden_phrases: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct EvalCaseResult {
    pub id: String,
    pub question: String,
    pub answered: bool,
    pub cited: bool,
    pub citation_count: usize,
    pub hallucination_flags: Vec<String>,
    pub missing_phrases: Vec<String>,
    pub source_count: usize,
    pub pass: bool,
    pub notes: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct EvalReport {
    pub cases: Vec<EvalCaseResult>,
    pub passed: usize,
    pub failed: usize,
    pub hallucination_rate: f32,
    pub citation_rate: f32,
}

pub fn load_cases(path: &Path) -> Result<Vec<EvalCase>> {
    let text = std::fs::read_to_string(path)?;
    let mut out = Vec::new();
    for (i, line) in text.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let case: EvalCase = serde_json::from_str(line)
            .map_err(|e| anyhow::anyhow!("eval case line {}: {e}", i + 1))?;
        out.push(case);
    }
    Ok(out)
}

fn count_citations(answer: &str) -> usize {
    let mut n = 0;
    let bytes = answer.as_bytes();
    for (i, _) in bytes.iter().enumerate() {
        if bytes[i] == b'[' {
            if let Some(end) = answer[i..].find(']') {
                let inner = &answer[i + 1..i + end];
                if !inner.is_empty() && inner.chars().all(|c| c.is_ascii_digit()) {
                    n += 1;
                }
            }
        }
    }
    n
}

pub fn evaluate_answer(case: &EvalCase, answer: &AskAnswer) -> EvalCaseResult {
    let text = answer.answer.to_lowercase();
    let mut hallucination_flags = Vec::new();
    for bad in &case.forbidden_phrases {
        if text.contains(&bad.to_lowercase()) {
            hallucination_flags.push(bad.clone());
        }
    }
    let mut missing_phrases = Vec::new();
    for good in &case.expected_phrases {
        if !text.contains(&good.to_lowercase()) {
            missing_phrases.push(good.clone());
        }
    }
    let citation_count = count_citations(&answer.answer);
    let answered = !answer.answer.trim().is_empty();
    let pass = answered
        && hallucination_flags.is_empty()
        && missing_phrases.is_empty()
        && (answer.cited || citation_count > 0 || case.expected_phrases.is_empty());

    let mut notes = String::new();
    if !answered {
        notes.push_str("empty answer; ");
    }
    if !answer.cited && citation_count == 0 && !case.expected_phrases.is_empty() {
        notes.push_str("no citations; ");
    }
    if !hallucination_flags.is_empty() {
        notes.push_str("forbidden phrase hit; ");
    }
    if !missing_phrases.is_empty() {
        notes.push_str("missing expected phrase; ");
    }

    EvalCaseResult {
        id: case.id.clone(),
        question: case.question.clone(),
        answered,
        cited: answer.cited || citation_count > 0,
        citation_count,
        hallucination_flags,
        missing_phrases,
        source_count: answer.sources.len(),
        pass,
        notes,
    }
}

/// Run the answer-quality suite against the corpus + a generation backend.
pub fn run_eval<C: GenericClient>(
    client: &mut C,
    backend: &GenerationBackend,
    cases: &[EvalCase],
) -> Result<EvalReport> {
    let mut results = Vec::new();
    for case in cases {
        let answer = ask::ask(client, &case.question, backend, 12)?;
        results.push(evaluate_answer(case, &answer));
    }
    let passed = results.iter().filter(|r| r.pass).count();
    let failed = results.len() - passed;
    let with_flags = results
        .iter()
        .filter(|r| !r.hallucination_flags.is_empty())
        .count();
    let with_cites = results.iter().filter(|r| r.cited).count();
    let n = results.len().max(1) as f32;
    Ok(EvalReport {
        cases: results,
        passed,
        failed,
        hallucination_rate: with_flags as f32 / n,
        citation_rate: with_cites as f32 / n,
    })
}

pub fn render_text(report: &EvalReport) -> String {
    let mut out = String::new();
    out.push_str(&format!(
        "answer-eval: passed={}/{} hallucination_rate={:.0}% citation_rate={:.0}%\n\n",
        report.passed,
        report.passed + report.failed,
        report.hallucination_rate * 100.0,
        report.citation_rate * 100.0
    ));
    for c in &report.cases {
        let mark = if c.pass { "PASS" } else { "FAIL" };
        out.push_str(&format!(
            "{mark}  [{}] {} (cites={} sources={} {})\n",
            c.id, c.question, c.citation_count, c.source_count, c.notes
        ));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ask::{AskAnswer, SourceRef};

    fn ans(text: &str, cited: bool, sources: usize) -> AskAnswer {
        AskAnswer {
            question: "q".into(),
            answer: text.into(),
            model: "test".into(),
            local: true,
            cited,
            sources: (0..sources)
                .map(|i| SourceRef {
                    conversation_id: i as i64,
                    session_id: uuid::Uuid::nil(),
                    project_name: None,
                    snippet: format!("s{i}"),
                })
                .collect(),
        }
    }

    #[test]
    fn passes_grounded_cited_answer() {
        let case = EvalCase {
            id: "c1".into(),
            question: "what db?".into(),
            must_cite_conversation_ids: vec![],
            expected_phrases: vec!["postgres".into()],
            forbidden_phrases: vec!["mysql".into()],
        };
        let r = evaluate_answer(&case, &ans("We use postgres [1].", true, 1));
        assert!(r.pass);
        assert!(r.cited);
    }

    #[test]
    fn flags_hallucination() {
        let case = EvalCase {
            id: "c2".into(),
            question: "q".into(),
            must_cite_conversation_ids: vec![],
            expected_phrases: vec![],
            forbidden_phrases: vec!["kubernetes".into()],
        };
        let r = evaluate_answer(&case, &ans("We deploy to kubernetes [1].", true, 1));
        assert!(!r.pass);
        assert_eq!(r.hallucination_flags, vec!["kubernetes".to_string()]);
    }

    #[test]
    fn counts_numeric_citations() {
        assert_eq!(count_citations("a [1] b [23] c [x]"), 2);
        assert_eq!(count_citations("none"), 0);
    }
}
