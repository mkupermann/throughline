//! Context pack: header, section headings and cited lines packed into a hard token budget.

use crate::access::{push_visibility, Actor};
use crate::recall::{
    candidates, fulltext_ranks, query_words, recall, Candidate, CandidateFilter, QueryEmbedding,
    Rank, RecallHit, RecallMode, RecallQuery, MAX_QUERY_CHARS,
};
use chrono::{DateTime, Utc};
use postgres::GenericClient;
use serde::Serialize;
use std::collections::{HashMap, HashSet};

pub const MIN_BUDGET: usize = 64;
pub const MAX_BUDGET: usize = 32_768;
pub const DEFAULT_BUDGET: usize = 2_000;

/// Rough token estimate: the larger of the word count and `ceil(chars / 4)`, at least 1.
pub fn estimate_tokens(s: &str) -> usize {
    let words = s.split_whitespace().count();
    let chars = s.chars().count();
    words.max(chars.div_ceil(4)).max(1)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Section {
    Header,
    Decisions,
    Relevant,
    Sessions,
    OpenQuestions,
    Review,
    Sources,
}

#[derive(Clone, Debug, Serialize)]
pub struct Citation {
    pub kind: &'static str,
    pub id: i64,
    pub date: Option<DateTime<Utc>>,
    pub title: Option<String>,
}

#[derive(Clone)]
pub struct Line {
    pub section: Section,
    pub text: String,
    pub cite: Option<Citation>,
}

#[derive(Debug, Serialize)]
pub struct PackedItem {
    pub section: Section,
    pub text: String,
    pub tokens: usize,
    pub cite: Option<Citation>,
}

#[derive(Debug, Serialize)]
pub struct ContextPack {
    pub text: String,
    pub items: Vec<PackedItem>,
    pub citations: Vec<Citation>,
    pub tokens_used: usize,
    pub budget: usize,
    pub within_budget: bool,
    pub skipped: usize,
    pub estimate: &'static str,
}

/// The notes a pack cites (kind `memory`); these are the ones a use counts for.
pub fn cited_note_ids(pack: &ContextPack) -> Vec<i64> {
    pack.citations
        .iter()
        .filter(|c| c.kind == "memory")
        .map(|c| c.id)
        .collect()
}

#[derive(Debug, PartialEq)]
pub enum PackError {
    BudgetOutOfRange(usize),
    HeaderTooLarge,
}

fn heading(section: Section) -> Option<&'static str> {
    match section {
        Section::Header => None,
        Section::Decisions => Some("## Decisions"),
        Section::Relevant => Some("## Relevant to the task"),
        Section::Sessions => Some("## Recent sessions"),
        Section::OpenQuestions => Some("## Open questions"),
        Section::Review => Some("## Needs review"),
        Section::Sources => Some("## Sources"),
    }
}

/// Packs the header and then the lines, in order, into at most `budget` tokens.
/// The header is flattened like any line; a header that is blank after flattening is emitted as a
/// single space (one line, estimate 1), so that the accounting below holds for every `Ok` result.
/// Every emitted text line costs `estimate_tokens(line) + 1` (the newline).
/// Lines that do not fit are skipped; lines that are blank after flattening are dropped silently.
pub fn pack(header: &str, lines: &[Line], budget: usize) -> Result<ContextPack, PackError> {
    if !(MIN_BUDGET..=MAX_BUDGET).contains(&budget) {
        return Err(PackError::BudgetOutOfRange(budget));
    }
    let mut header = header.replace(['\n', '\r'], " ");
    if header.trim().is_empty() {
        header = " ".to_string();
    }
    let header_cost = estimate_tokens(&header) + 1;
    if header_cost > budget {
        return Err(PackError::HeaderTooLarge);
    }
    let mut used = header_cost;
    let mut out: Vec<String> = vec![header];
    let mut items = Vec::new();
    let mut citations: Vec<Citation> = Vec::new();
    let mut skipped = 0;
    let mut current: Option<Section> = None;
    for line in lines {
        let text = line.text.replace(['\n', '\r'], " ");
        if text.trim().is_empty() {
            continue;
        }
        let tokens = estimate_tokens(&text);
        let head = if current == Some(line.section) {
            None
        } else {
            heading(line.section)
        };
        let cost = tokens + 1 + head.map_or(0, |h| estimate_tokens(h) + 1);
        if used + cost > budget {
            skipped += 1;
            continue;
        }
        used += cost;
        if let Some(h) = head {
            out.push(h.to_string());
        }
        current = Some(line.section);
        out.push(text.clone());
        if let Some(c) = &line.cite {
            if !citations.iter().any(|x| x.kind == c.kind && x.id == c.id) {
                citations.push(c.clone());
            }
        }
        items.push(PackedItem {
            section: line.section,
            text,
            tokens,
            cite: line.cite.clone(),
        });
    }
    Ok(ContextPack {
        text: out.join("\n"),
        items,
        citations,
        tokens_used: used,
        budget,
        within_budget: used <= budget,
        skipped,
        estimate: "max(words, ceil(chars/4))",
    })
}

impl std::fmt::Display for PackError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PackError::BudgetOutOfRange(n) => {
                write!(f, "budget {n} is outside {MIN_BUDGET}..={MAX_BUDGET}")
            }
            PackError::HeaderTooLarge => write!(
                f,
                "the header does not fit the budget; use a shorter project name or a larger budget"
            ),
        }
    }
}

impl std::error::Error for PackError {}

/// The input problem behind a `build_context` error, if there is one: a header that cannot fit
/// the budget (a very long project name) is the caller's doing, not a database failure.
pub fn input_error(e: &anyhow::Error) -> Option<&PackError> {
    e.downcast_ref::<PackError>()
}

pub struct ContextRequest<'a> {
    pub project: &'a str,
    pub task: Option<&'a str>,
    pub budget: usize,
}

const DECISION_CATEGORIES: [&str; 3] = ["decision", "preference", "project_context"];
const RELEVANT_LIMIT: usize = 20;
const SESSION_LIMIT: i64 = 8;
const SESSION_TITLE_CHARS: usize = 120;
const QUESTION_LIMIT: usize = 5;
const REVIEW_LIMIT: i64 = 5;

/// A session title for one line: whitespace collapsed, at most `SESSION_TITLE_CHARS` characters,
/// "…" appended when it was cut.
fn session_label(raw: &str) -> String {
    let flat = raw.split_whitespace().collect::<Vec<_>>().join(" ");
    if flat.chars().count() <= SESSION_TITLE_CHARS {
        return flat;
    }
    let mut cut: String = flat.chars().take(SESSION_TITLE_CHARS).collect();
    cut.push('…');
    cut
}

fn ymd(d: DateTime<Utc>) -> String {
    d.format("%Y-%m-%d").to_string()
}

/// The conversation a note comes from, resolved through its replacement chain.
#[derive(Clone)]
struct Origin {
    id: i64,
    title: Option<String>,
    date: Option<DateTime<Utc>>,
}

impl Origin {
    fn label(&self) -> String {
        self.title
            .clone()
            .unwrap_or_else(|| format!("s#{}", self.id))
    }
}

struct NoteView<'a> {
    id: i64,
    content: &'a str,
    category: &'a str,
    valid_from: DateTime<Utc>,
    source_at: Option<DateTime<Utc>>,
    conversation_id: Option<i64>,
    session_title: &'a Option<String>,
}

/// `- [m#id · date · category] content (source: session "Title", date)`; the part in the last
/// parentheses is left out for a note that does not come from a conversation.
fn note_line(section: Section, n: &NoteView, origins: &mut HashMap<i64, Origin>) -> Line {
    let date = n.source_at.unwrap_or(n.valid_from);
    let mut text = format!(
        "- [m#{} · {} · {}] {}",
        n.id,
        ymd(date),
        n.category,
        n.content
    );
    if let Some(conv) = n.conversation_id {
        let o = Origin {
            id: conv,
            title: n.session_title.as_deref().map(session_label),
            date: n.source_at,
        };
        text.push_str(&format!(
            " (source: session \"{}\", {})",
            o.label(),
            ymd(date)
        ));
        origins.insert(n.id, o);
    }
    Line {
        section,
        text,
        cite: Some(Citation {
            kind: "memory",
            id: n.id,
            date: Some(date),
            title: n.session_title.as_deref().map(session_label),
        }),
    }
}

fn hit_line(section: Section, h: &RecallHit, origins: &mut HashMap<i64, Origin>) -> Line {
    note_line(
        section,
        &NoteView {
            id: h.id,
            content: &h.content,
            category: &h.category,
            valid_from: h.valid_from,
            source_at: h.source_at,
            conversation_id: h.conversation_id,
            session_title: &h.session_title,
        },
        origins,
    )
}

fn candidate_line(section: Section, c: &Candidate, origins: &mut HashMap<i64, Origin>) -> Line {
    note_line(
        section,
        &NoteView {
            id: c.id,
            content: &c.content,
            category: &c.category,
            valid_from: c.valid_from,
            source_at: c.source_at,
            conversation_id: c.conversation_id,
            session_title: &c.session_title,
        },
        origins,
    )
}

/// Gathers the sections for `req.project` and packs them into `req.budget` tokens.
/// A `PackError` comes back as an error with the text of the variant.
pub fn build_context<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    req: &ContextRequest,
    emb: Option<&QueryEmbedding>,
) -> anyhow::Result<ContextPack> {
    let header = format!(
        "Context for {} · {} · budget {} tokens (estimated)",
        req.project,
        ymd(Utc::now()),
        req.budget
    );
    // The header must fit before any query runs.
    pack(&header, &[], req.budget).map_err(anyhow::Error::new)?;
    let mut lines: Vec<Line> = Vec::new();
    let mut origins: HashMap<i64, Origin> = HashMap::new();
    let mut shown: HashSet<i64> = HashSet::new();

    // One query for the current notes of the project; Decisions and OpenQuestions share it.
    // Stale notes belong to the review section only, so they are not shown twice.
    let query = RecallQuery {
        actor,
        text: "",
        project: Some(req.project),
        mode: RecallMode::Current,
        valid_at: None,
        known_at: None,
        limit: RELEVANT_LIMIT,
        rank: Rank::Fulltext,
    };
    let mut current: Vec<Candidate> = candidates(client, CandidateFilter::Current, &query, emb)?
        .into_iter()
        .filter(|c| c.status != "stale")
        .collect();
    current.sort_by(|a, b| b.valid_from.cmp(&a.valid_from).then(a.id.cmp(&b.id)));

    let mut decisions: Vec<&Candidate> = current
        .iter()
        .filter(|c| DECISION_CATEGORIES.contains(&c.category.as_str()))
        .collect();
    let task = req.task.map(str::trim).filter(|t| !t.is_empty());
    if let Some(task) = task {
        // With a task, decisions that match it lead: full-text rank descending, ties and the
        // rest newest first. The stable sort keeps the newest-first order inside each group.
        let text: String = task.chars().take(MAX_QUERY_CHARS).collect();
        let ids: Vec<i64> = decisions.iter().map(|c| c.id).collect();
        let ranks = fulltext_ranks(client, &query_words(&text), &ids)?;
        let rank = |c: &Candidate| ranks.get(&c.id).copied().unwrap_or(0.0).max(0.0);
        decisions.sort_by(|a, b| rank(b).total_cmp(&rank(a)));
    }
    for c in decisions {
        shown.insert(c.id);
        lines.push(candidate_line(Section::Decisions, c, &mut origins));
    }

    if let Some(task) = task {
        let text: String = task.chars().take(MAX_QUERY_CHARS).collect();
        let q = RecallQuery {
            text: &text,
            ..query
        };
        let hits = recall(client, &q, emb)?.hits;
        for h in hits {
            // Without a match the full-text rank is 0: not relevant to the task.
            if h.score <= 0.0 || h.status == "stale" || !shown.insert(h.id) {
                continue;
            }
            lines.push(hit_line(Section::Relevant, &h, &mut origins));
        }
    }

    sessions(client, req.project, &mut lines)?;

    let mut questions = 0;
    for c in &current {
        if questions == QUESTION_LIMIT {
            break;
        }
        let lower = c.content.to_lowercase();
        if shown.contains(&c.id)
            || !(lower.contains('?') || lower.contains("todo") || lower.contains("open"))
        {
            continue;
        }
        shown.insert(c.id);
        questions += 1;
        lines.push(candidate_line(Section::OpenQuestions, c, &mut origins));
    }

    review(client, actor, req.project, &shown, &mut lines)?;

    // Sources: the sessions behind the lines that made it into the pack, first appearance first.
    let first = pack(&header, &lines, req.budget).map_err(anyhow::Error::new)?;
    let mut sources: Vec<Line> = Vec::new();
    let mut seen: HashSet<i64> = HashSet::new();
    // A session is dated by its start, the same date its own citation carries.
    let ids: Vec<i64> = origins.values().map(|o| o.id).collect();
    let mut started: HashMap<i64, DateTime<Utc>> = HashMap::new();
    for r in client.query(
        "SELECT id, started_at FROM conversations WHERE id = ANY($1)",
        &[&ids],
    )? {
        started.insert(r.get(0), r.get(1));
    }
    for o in origins.values_mut() {
        if let Some(d) = started.get(&o.id) {
            o.date = Some(*d);
        }
    }
    for item in &first.items {
        let Some(cite) = &item.cite else { continue };
        let origin = match cite.kind {
            "memory" => origins.get(&cite.id).cloned(),
            "session" => Some(Origin {
                id: cite.id,
                title: cite.title.clone(),
                date: cite.date,
            }),
            _ => None,
        };
        let Some(o) = origin else { continue };
        if !seen.insert(o.id) {
            continue;
        }
        let date = o.date.map(|d| format!(" · {}", ymd(d))).unwrap_or_default();
        sources.push(Line {
            section: Section::Sources,
            text: format!("- [s#{}{}] \"{}\"", o.id, date, o.label()),
            cite: Some(Citation {
                kind: "session",
                id: o.id,
                date: o.date,
                title: o.title.clone(),
            }),
        });
    }
    if sources.is_empty() {
        return Ok(first);
    }
    lines.extend(sources);
    pack(&header, &lines, req.budget).map_err(anyhow::Error::new)
}

/// Recent human sessions of the project: the summary as title, else the first user message.
fn sessions<C: GenericClient>(
    client: &mut C,
    project: &str,
    lines: &mut Vec<Line>,
) -> anyhow::Result<()> {
    let rows = client.query(
        "SELECT c.id, c.started_at, COALESCE(c.source_tool, 'unknown'),
                NULLIF(btrim(c.summary), ''),
                (SELECT left(m.content, 400) FROM messages m
                  WHERE m.conversation_id = c.id AND m.role::text = 'user'
                    AND btrim(COALESCE(m.content, '')) <> ''
                  ORDER BY m.created_at, m.id LIMIT 1)
           FROM conversations c
          WHERE c.project_name = $1 AND c.generated_by IS NULL
          ORDER BY c.started_at DESC, c.id DESC
          LIMIT $2",
        &[&project, &SESSION_LIMIT],
    )?;
    for r in rows {
        let id: i64 = r.get(0);
        let started: DateTime<Utc> = r.get(1);
        let tool: String = r.get(2);
        let summary: Option<String> = r.get(3);
        let first_message: Option<String> = r.get(4);
        let title = summary
            .or(first_message)
            .map(|t| session_label(&t))
            .unwrap_or_else(|| format!("session {id}"));
        lines.push(Line {
            section: Section::Sessions,
            text: format!("- [s#{id} · {} · {tool}] {title}", ymd(started)),
            cite: Some(Citation {
                kind: "session",
                id,
                date: Some(started),
                title: Some(title),
            }),
        });
    }
    Ok(())
}

/// Notes that are stale or have lapsed and have no successor; replaced and forgotten notes stay out.
fn review<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    project: &str,
    shown: &HashSet<i64>,
    lines: &mut Vec<Line>,
) -> anyhow::Result<()> {
    let skip: Vec<i64> = shown.iter().copied().collect();
    let mut params: Vec<&(dyn postgres::types::ToSql + Sync)> =
        vec![&project, &skip, &REVIEW_LIMIT];
    let seen = push_visibility(actor, "mc", &mut params);
    let rows = client.query(
        &format!(
            "SELECT mc.id, mc.content, COALESCE(mc.status, 'active'),
                COALESCE(mc.valid_from, mc.source_at, mc.created_at)
           FROM memory_chunks mc
          WHERE (mc.project_name = $1 OR mc.project_name IS NULL)
            AND COALESCE(mc.status, 'active') NOT IN ('forgotten', 'superseded', 'merged')
            AND mc.superseded_by IS NULL
            AND NOT (mc.id = ANY($2))
            AND (mc.status = 'stale' OR mc.expires_at <= now() OR mc.valid_to <= now())
            AND {seen}
          ORDER BY COALESCE(mc.valid_from, mc.source_at, mc.created_at) DESC, mc.id
          LIMIT $3"
        ),
        &params,
    )?;
    for r in rows {
        let id: i64 = r.get(0);
        let content: String = r.get(1);
        let status: String = r.get(2);
        let date: DateTime<Utc> = r.get(3);
        let label = if status == "stale" { "stale" } else { "lapsed" };
        lines.push(Line {
            section: Section::Review,
            text: format!("- [m#{id} · {} · {label}] {content}", ymd(date)),
            cite: Some(Citation {
                kind: "memory",
                id,
                date: Some(date),
                title: None,
            }),
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn words(n: usize) -> String {
        vec!["w"; n].join(" ")
    }

    fn line(section: Section, text: &str, cite: Option<Citation>) -> Line {
        Line {
            section,
            text: text.to_string(),
            cite,
        }
    }

    fn note(id: i64) -> Option<Citation> {
        Some(Citation {
            kind: "memory",
            id,
            date: None,
            title: None,
        })
    }

    #[test]
    fn estimate_counts_characters() {
        assert_eq!(estimate_tokens("äöü"), 1);
        assert_eq!(estimate_tokens("a b c"), 3);
        assert_eq!(estimate_tokens("abcdefghi"), 3);
        assert_eq!(estimate_tokens("😀"), 1);
        assert_eq!(estimate_tokens(""), 1);
        assert_eq!(estimate_tokens(&"x".repeat(40)), 10);
    }

    #[test]
    fn pack_rejects_budgets_out_of_range() {
        assert_eq!(
            pack("h", &[], 63).err().unwrap(),
            PackError::BudgetOutOfRange(63)
        );
        assert_eq!(
            pack("h", &[], 32_769).err().unwrap(),
            PackError::BudgetOutOfRange(32_769)
        );
        assert!(pack("h", &[], 64).is_ok());
    }

    #[test]
    fn pack_errors_when_the_header_does_not_fit() {
        assert_eq!(
            pack(&words(300), &[], 64).err().unwrap(),
            PackError::HeaderTooLarge
        );
    }

    #[test]
    fn an_oversized_line_is_skipped() {
        let lines = vec![
            line(Section::Relevant, &words(5_000), None),
            line(Section::Relevant, "short line", None),
        ];
        let p = pack("head", &lines, 200).unwrap();
        assert_eq!(p.skipped, 1);
        assert_eq!(p.items.len(), 1);
        assert!(p.text.contains("short line"));
        assert!(!p.text.contains("w w w"));
    }

    #[test]
    fn multiline_content_becomes_one_line() {
        let p = pack("head", &[line(Section::Decisions, "a\nb\r\nc", None)], 200).unwrap();
        assert_eq!(p.items[0].text, "a b  c");
        assert_eq!(p.items[0].tokens, 3);
        assert_eq!(p.text.lines().count(), 3);
    }

    #[test]
    fn section_heading_only_with_a_line() {
        let lines = vec![
            line(Section::Decisions, &words(500), None),
            line(Section::Relevant, "fits", None),
        ];
        let p = pack("head", &lines, 100).unwrap();
        assert!(!p.text.contains("## Decisions"));
        assert!(p.text.contains("## Relevant to the task"));
        assert_eq!(p.skipped, 1);
    }

    #[test]
    fn citations_are_unique() {
        let lines = vec![
            line(Section::Decisions, "one", note(7)),
            line(Section::Relevant, "two", note(7)),
            line(Section::Relevant, "three", note(8)),
        ];
        let p = pack("head", &lines, 500).unwrap();
        assert_eq!(p.citations.len(), 2);
    }

    fn r1_sum(p: &ContextPack) -> usize {
        p.text.lines().map(|l| estimate_tokens(l) + 1).sum()
    }

    #[test]
    fn multiline_header_becomes_one_line() {
        let p = pack("a\n\n\n\n\n\n\n\n", &[], 64).unwrap();
        assert_eq!(p.text.lines().count(), 1);
        assert!(!p.text.contains('\n'));
        assert_eq!(p.tokens_used, r1_sum(&p));
        assert!(p.tokens_used <= 64);
    }

    #[test]
    fn empty_header_keeps_the_accounting_exact() {
        for h in ["", "\n", "  \r\n "] {
            let p = pack(h, &[line(Section::Decisions, "x", None)], 64).unwrap();
            assert_eq!(p.text.lines().next().map(estimate_tokens), Some(1), "{h:?}");
            assert_eq!(p.tokens_used, r1_sum(&p), "{h:?}");
        }
        let p = pack("", &[], 64).unwrap();
        assert_eq!(p.tokens_used, r1_sum(&p));
    }

    #[test]
    fn a_header_that_exactly_fills_the_budget_is_ok() {
        let p = pack(&words(63), &[], 64).unwrap();
        assert_eq!(p.tokens_used, 64);
        assert!(p.within_budget);
        assert_eq!(
            pack(&words(64), &[], 64).err().unwrap(),
            PackError::HeaderTooLarge
        );
    }

    #[test]
    fn a_line_landing_exactly_on_the_budget_is_included() {
        // header cost 2 + heading "## Decisions" (12 chars -> 3 tokens, cost 4) + line of 57 words (cost 58) = 64
        let p = pack("h", &[line(Section::Decisions, &words(57), None)], 64).unwrap();
        assert_eq!(p.tokens_used, 64);
        assert_eq!(p.items.len(), 1);
        let p = pack("h", &[line(Section::Decisions, &words(58), None)], 64).unwrap();
        assert_eq!(p.skipped, 1);
    }

    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 ^= self.0 << 13;
            self.0 ^= self.0 >> 7;
            self.0 ^= self.0 << 17;
            self.0
        }
        fn range(&mut self, lo: usize, hi_incl: usize) -> usize {
            lo + (self.next() % (hi_incl - lo + 1) as u64) as usize
        }
    }

    /// 1..=max words of 1..=12 characters each, so both estimate branches occur.
    fn mixed(rng: &mut Rng, max: usize) -> String {
        let n = rng.range(1, max);
        (0..n)
            .map(|_| "x".repeat(rng.range(1, 12)))
            .collect::<Vec<_>>()
            .join(" ")
    }

    #[test]
    fn never_over_budget_property() {
        let sections = [
            Section::Decisions,
            Section::Relevant,
            Section::Sessions,
            Section::OpenQuestions,
            Section::Review,
            Section::Sources,
        ];
        let mut rng = Rng(0x9E37_79B9_7F4A_7C15);
        for case in 0..1_000 {
            let budget = rng.range(64, 4096);
            let header = mixed(&mut rng, 400);
            let n = rng.range(0, 40);
            let mut idx: Vec<usize> = (0..n).map(|_| rng.range(0, 5)).collect();
            idx.sort();
            let lines: Vec<Line> = idx
                .iter()
                .map(|&s| line(sections[s], &mixed(&mut rng, 400), None))
                .collect();
            let header_fits = estimate_tokens(&header.replace(['\n', '\r'], " ")) < budget;
            match pack(&header, &lines, budget) {
                Ok(p) => {
                    assert!(header_fits, "case {case}: Ok with oversized header");
                    assert!(p.tokens_used <= budget, "case {case}");
                    assert!(p.within_budget);
                    let sum: usize = p.text.lines().map(|l| estimate_tokens(l) + 1).sum();
                    assert_eq!(p.tokens_used, sum, "case {case}");
                }
                Err(e) => {
                    assert_eq!(e, PackError::HeaderTooLarge, "case {case}");
                    assert!(!header_fits, "case {case}");
                }
            }
        }
    }

    // ---- build_context (database) ----

    use crate::extract_memory::category_column_type;
    use chrono::{Duration, TimeZone};
    use postgres::NoTls;

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
        cfg.connect(NoTls).ok()
    }

    macro_rules! db_tx {
        ($name:literal, $tx:ident) => {
            let Some(mut client) = test_db() else {
                eprintln!("skipping {}: no PostgreSQL reachable", $name);
                return;
            };
            let mut $tx = client.transaction().unwrap();
        };
    }

    fn proj() -> String {
        format!("ctx-{}", uuid::Uuid::new_v4().simple())
    }

    fn day(d: u32) -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2026, 3, d, 12, 0, 0).unwrap()
    }

    struct Seed<'a> {
        content: &'a str,
        category: &'a str,
        project: Option<&'a str>,
        source: Option<i64>,
        status: &'a str,
        valid_from: DateTime<Utc>,
        expires_at: Option<DateTime<Utc>>,
    }

    fn seed<'a>(content: &'a str, project: Option<&'a str>, category: &'a str, d: u32) -> Seed<'a> {
        Seed {
            content,
            category,
            project,
            source: None,
            status: "active",
            valid_from: day(d),
            expires_at: None,
        }
    }

    fn insert<C: GenericClient>(tx: &mut C, s: &Seed) -> i64 {
        let ct = category_column_type(tx).unwrap();
        let source_type = if s.source.is_some() {
            "conversation"
        } else {
            "manual"
        };
        tx.query_one(
            &format!(
                "INSERT INTO memory_chunks (source_type, source_id, content, category, \
                 project_name, confidence, tags, status, valid_from, expires_at) \
                 VALUES ($1, $2, $3, $4::text::{ct}, $5, 0.8::float8, '{{}}', $6, $7, $8) \
                 RETURNING id"
            ),
            &[
                &source_type,
                &s.source,
                &s.content,
                &s.category,
                &s.project,
                &s.status,
                &s.valid_from,
                &s.expires_at,
            ],
        )
        .unwrap()
        .get(0)
    }

    fn conv<C: GenericClient>(tx: &mut C, project: &str, summary: Option<&str>, d: u32) -> i64 {
        tx.query_one(
            "INSERT INTO conversations (session_id, project_path, started_at, summary) \
             VALUES ($1, $2, $3, $4) RETURNING id",
            &[
                &uuid::Uuid::new_v4(),
                &format!("/fictional/{project}"),
                &day(d),
                &summary,
            ],
        )
        .unwrap()
        .get(0)
    }

    fn ctx<C: GenericClient>(tx: &mut C, project: &str, task: Option<&str>) -> ContextPack {
        build_context(
            tx,
            &Actor::local(),
            &ContextRequest {
                project,
                task,
                budget: 8_000,
            },
            None,
        )
        .unwrap()
    }

    fn pos(p: &ContextPack, needle: &str) -> usize {
        p.text
            .find(needle)
            .unwrap_or_else(|| panic!("{needle:?} missing in:\n{}", p.text))
    }

    #[test]
    fn decisions_come_first_and_stay_in_the_project() {
        db_tx!("decisions_come_first_and_stay_in_the_project", tx);
        let (p, q) = (proj(), proj());
        conv(&mut tx, &p, Some("Planning"), 1);
        insert(
            &mut tx,
            &seed("alpha decision in P", Some(&p), "decision", 5),
        );
        insert(
            &mut tx,
            &seed("beta decision in Q", Some(&q), "decision", 6),
        );
        insert(
            &mut tx,
            &seed("gamma decision without project", None, "decision", 4),
        );
        let out = ctx(&mut tx, &p, None);
        assert!(out.text.starts_with(&format!("Context for {p} · ")));
        assert!(out.text.contains("alpha decision in P"));
        assert!(out.text.contains("gamma decision without project"));
        assert!(!out.text.contains("beta decision in Q"));
        let d = pos(&out, "## Decisions");
        assert!(d < pos(&out, "## Recent sessions"));
        assert!(pos(&out, "alpha decision in P") < pos(&out, "gamma decision without project"));
        assert!(out.text.contains("· decision] alpha decision in P"));
    }

    #[test]
    fn replaced_and_forgotten_notes_stay_out() {
        db_tx!("replaced_and_forgotten_notes_stay_out", tx);
        let p = proj();
        let mut old = seed("old replaced marker", Some(&p), "decision", 1);
        old.status = "superseded";
        insert(&mut tx, &old);
        let mut gone = seed("forgotten marker", Some(&p), "decision", 2);
        gone.status = "forgotten";
        insert(&mut tx, &gone);
        let mut lapsed = seed("lapsed marker", Some(&p), "decision", 3);
        lapsed.expires_at = Some(Utc::now() - Duration::days(2));
        insert(&mut tx, &lapsed);
        insert(&mut tx, &seed("live marker", Some(&p), "decision", 4));
        let out = ctx(&mut tx, &p, Some("marker"));
        assert!(!out.text.contains("old replaced marker"));
        assert!(!out.text.contains("forgotten marker"));
        assert!(out.text.contains("live marker"));
        let review = pos(&out, "## Needs review");
        let at = pos(&out, "lapsed marker");
        assert!(at > review, "lapsed note only in Review");
        assert_eq!(out.text.matches("lapsed marker").count(), 1);
    }

    #[test]
    fn a_note_appears_once() {
        db_tx!("a_note_appears_once", tx);
        let p = proj();
        insert(
            &mut tx,
            &seed("use zebra stripes everywhere?", Some(&p), "decision", 3),
        );
        let out = ctx(&mut tx, &p, Some("zebra stripes"));
        assert_eq!(out.text.matches("zebra stripes everywhere").count(), 1);
        assert!(!out.text.contains("## Relevant to the task"));
        assert!(!out.text.contains("## Open questions"));
    }

    #[test]
    fn relevant_needs_a_task() {
        db_tx!("relevant_needs_a_task", tx);
        let p = proj();
        insert(&mut tx, &seed("quokka detail", Some(&p), "insight", 3));
        let without = ctx(&mut tx, &p, None);
        assert!(!without.text.contains("## Relevant to the task"));
        assert!(!without.text.contains("quokka detail"));
        let blank = ctx(&mut tx, &p, Some("   "));
        assert!(!blank.text.contains("## Relevant to the task"));
        let with = ctx(&mut tx, &p, Some("quokka"));
        assert!(with.text.contains("## Relevant to the task"));
        assert!(with.text.contains("quokka detail"));
    }

    /// Six decisions: the three newest do not mention the task words, two older ones do
    /// (the day-3 one with both words, so the higher rank), one old one does not.
    fn seed_task_first<C: GenericClient>(tx: &mut C, p: &str) {
        insert(
            tx,
            &seed(
                "newest unrelated choice about logging",
                Some(p),
                "decision",
                20,
            ),
        );
        insert(
            tx,
            &seed(
                "second unrelated choice about caching",
                Some(p),
                "decision",
                19,
            ),
        );
        insert(
            tx,
            &seed(
                "third unrelated choice about retries",
                Some(p),
                "decision",
                18,
            ),
        );
        insert(
            tx,
            &seed("quokka only matches one word", Some(p), "decision", 4),
        );
        insert(
            tx,
            &seed("quokka habitat matches both words", Some(p), "decision", 3),
        );
        insert(
            tx,
            &seed(
                "oldest unrelated choice about naming",
                Some(p),
                "decision",
                2,
            ),
        );
    }

    fn decision_lines(out: &ContextPack) -> Vec<&str> {
        out.text
            .lines()
            .skip_while(|l| *l != "## Decisions")
            .skip(1)
            .take_while(|l| l.starts_with("- "))
            .collect()
    }

    #[test]
    fn with_a_task_matching_decisions_come_first() {
        db_tx!("with_a_task_matching_decisions_come_first", tx);
        let p = proj();
        seed_task_first(&mut tx, &p);
        let out = ctx(&mut tx, &p, Some("quokka habitat"));
        let d = decision_lines(&out);
        assert_eq!(d.len(), 6, "{}", out.text);
        assert!(d[0].contains("quokka habitat matches both words"), "{d:#?}");
        assert!(d[1].contains("quokka only matches one word"), "{d:#?}");
        assert!(d[2].contains("newest unrelated"), "{d:#?}");
        assert!(d[3].contains("second unrelated"), "{d:#?}");
        assert!(d[4].contains("third unrelated"), "{d:#?}");
        assert!(d[5].contains("oldest unrelated"), "{d:#?}");
    }

    #[test]
    fn without_a_task_decisions_stay_newest_first() {
        db_tx!("without_a_task_decisions_stay_newest_first", tx);
        let p = proj();
        seed_task_first(&mut tx, &p);
        let out = ctx(&mut tx, &p, None);
        let d = decision_lines(&out);
        let want = [
            "newest",
            "second",
            "third",
            "quokka only",
            "quokka habitat",
            "oldest",
        ];
        assert_eq!(d.len(), 6, "{}", out.text);
        for (line, w) in d.iter().zip(want) {
            assert!(line.contains(w), "{w}: {d:#?}");
        }
    }

    #[test]
    fn task_first_order_respects_the_budget() {
        db_tx!("task_first_order_respects_the_budget", tx);
        let p = proj();
        seed_task_first(&mut tx, &p);
        let req = ContextRequest {
            project: &p,
            task: Some("quokka habitat"),
            budget: 70,
        };
        let out = build_context(&mut tx, &Actor::local(), &req, None).unwrap();
        let d = decision_lines(&out);
        assert!(out.within_budget);
        assert_eq!(d.len(), 2, "{}", out.text);
        assert!(d[0].contains("quokka habitat"), "{d:#?}");
        assert!(d[1].contains("quokka only"), "{d:#?}");
    }

    #[test]
    fn every_citation_resolves() {
        db_tx!("every_citation_resolves", tx);
        let p = proj();
        let g = conv(&mut tx, &p, Some("Design talk"), 2);
        let mut s = seed("cited decision", Some(&p), "decision", 5);
        s.source = Some(g);
        insert(&mut tx, &s);
        insert(&mut tx, &seed("a todo item", Some(&p), "insight", 4));
        let mut stale = seed("stale thing", Some(&p), "insight", 3);
        stale.status = "stale";
        insert(&mut tx, &stale);
        let out = ctx(&mut tx, &p, Some("cited"));
        assert!(!out.citations.is_empty());
        for c in &out.citations {
            let table = match c.kind {
                "memory" => "memory_chunks",
                "session" => "conversations",
                k => panic!("unknown kind {k}"),
            };
            let n: i64 = tx
                .query_one(
                    &format!("SELECT count(*) FROM {table} WHERE id = $1"),
                    &[&c.id],
                )
                .unwrap()
                .get(0);
            assert_eq!(n, 1, "{} {}", c.kind, c.id);
        }
        assert!(out
            .citations
            .iter()
            .any(|c| c.kind == "session" && c.id == g));
        assert!(out.citations.iter().any(|c| c.kind == "memory"));
    }

    #[test]
    fn sources_list_the_root_session() {
        db_tx!("sources_list_the_root_session", tx);
        let p = proj();
        let g = conv(&mut tx, &p, Some("Root session"), 2);
        let mut a = seed("first version", Some(&p), "decision", 3);
        a.source = Some(g);
        a.status = "superseded";
        let a_id = insert(&mut tx, &a);
        let b_id = insert(&mut tx, &seed("second version", Some(&p), "decision", 6));
        tx.execute(
            "UPDATE memory_chunks SET superseded_by = $1 WHERE id = $2",
            &[&b_id, &a_id],
        )
        .unwrap();
        let out = ctx(&mut tx, &p, None);
        assert!(out.text.contains("second version"));
        assert!(!out.text.contains("first version"));
        let sources = &out.text[pos(&out, "## Sources")..];
        assert!(
            sources.contains(&format!("- [s#{g} · 2026-03-02] \"Root session\"")),
            "{sources}"
        );
        assert!(out
            .text
            .contains("(source: session \"Root session\", 2026-03-02)"));
    }

    #[test]
    fn session_titles_are_capped_everywhere() {
        db_tx!("session_titles_are_capped_everywhere", tx);
        let p = proj();
        let long = (0..100)
            .map(|i| format!("w{i:03}"))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(long.chars().count() > 400);
        let g = conv(&mut tx, &p, Some(&long), 2);
        let mut s = seed("titled decision", Some(&p), "decision", 5);
        s.source = Some(g);
        insert(&mut tx, &s);
        let out = ctx(&mut tx, &p, None);
        let mut checked = 0;
        for c in out.citations.iter().filter(|c| c.title.is_some()) {
            let t = c.title.as_deref().unwrap();
            assert!(t.chars().count() <= SESSION_TITLE_CHARS + 1, "{t}");
            assert!(!t.contains(['\n', '\r']), "{t:?}");
            checked += 1;
        }
        assert!(
            checked >= 2,
            "memory and session citation: {:?}",
            out.citations
        );
        let mut quoted = 0;
        for l in out.text.lines() {
            if let Some(i) = l.find("session \"") {
                let rest = &l[i + 9..];
                let t = &rest[..rest.find('"').unwrap()];
                assert!(t.chars().count() <= SESSION_TITLE_CHARS + 1, "{l}");
                quoted += 1;
            }
            if l.starts_with("- [s#") && l.contains(" · 2026-03-02] \"") {
                let t = l.split('"').nth(1).unwrap();
                assert!(t.chars().count() <= SESSION_TITLE_CHARS + 1, "{l}");
                quoted += 1;
            }
        }
        assert!(quoted >= 2, "note line and Sources line: {}", out.text);
    }

    #[test]
    fn session_label_flattens_and_cuts() {
        assert_eq!(session_label("  a\n b \t c "), "a b c");
        let cut = session_label(&"ä ".repeat(200));
        assert_eq!(cut.chars().count(), SESSION_TITLE_CHARS + 1);
        assert!(cut.ends_with('…'));
        assert_eq!(session_label(&"x".repeat(120)), "x".repeat(120));
    }

    #[test]
    fn sources_show_the_session_start_not_the_note_source_date() {
        db_tx!("sources_show_the_session_start", tx);
        let p = proj();
        let g = conv(&mut tx, &p, Some("Kickoff"), 2);
        let mut s = seed("dated decision", Some(&p), "decision", 5);
        s.source = Some(g);
        let id = insert(&mut tx, &s);
        tx.execute(
            "UPDATE memory_chunks SET source_at = $2 WHERE id = $1",
            &[&id, &day(9)],
        )
        .unwrap();
        let out = ctx(&mut tx, &p, None);
        let sources = &out.text[pos(&out, "## Sources")..];
        assert!(
            sources.contains(&format!("- [s#{g} · 2026-03-02] \"Kickoff\"")),
            "{sources}"
        );
        let c = out
            .citations
            .iter()
            .find(|c| c.kind == "session" && c.id == g)
            .unwrap();
        assert_eq!(c.date, Some(day(2)));
    }

    #[test]
    fn an_oversized_header_fails_before_any_query() {
        db_tx!("an_oversized_header_fails_before_any_query", tx);
        // A failed statement poisons the transaction: any later query would error differently.
        assert!(tx.execute("SELECT 1/0", &[]).is_err());
        let project = "word ".repeat(200);
        let e = build_context(
            &mut tx,
            &Actor::local(),
            &ContextRequest {
                project: &project,
                task: Some("x"),
                budget: 64,
            },
            None,
        )
        .unwrap_err();
        assert_eq!(input_error(&e), Some(&PackError::HeaderTooLarge), "{e}");
    }

    #[test]
    fn empty_project_gives_only_the_header() {
        db_tx!("empty_project_gives_only_the_header", tx);
        let p = proj();
        let out = ctx(&mut tx, &p, Some("anything"));
        assert!(out.items.is_empty());
        assert!(!out.text.contains('\n'));
        assert_eq!(
            out.text,
            format!(
                "Context for {p} · {} · budget 8000 tokens (estimated)",
                Utc::now().format("%Y-%m-%d")
            )
        );
    }

    #[test]
    fn context_pack_hides() {
        use crate::access::team_test::{assert_hidden, note, two_accounts};
        db_tx!("context_pack_hides", tx);
        let (a, b) = two_accounts(&mut tx);
        let p = proj();
        let secret = format!("secret {p}");
        // A decision, an open question and a lapsed note of A's, all private.
        let owner = a.user_id;
        note(
            &mut tx,
            owner,
            "private",
            &format!("we chose {secret}"),
            "decision",
            Some(&p),
        );
        note(
            &mut tx,
            owner,
            "private",
            &format!("todo open {secret}?"),
            "insight",
            Some(&p),
        );
        let lapsed = note(
            &mut tx,
            owner,
            "private",
            &format!("lapsed {secret}"),
            "insight",
            Some(&p),
        );
        tx.execute(
            "UPDATE memory_chunks SET expires_at = now() - interval '1 day' WHERE id = $1",
            &[&lapsed],
        )
        .unwrap();
        let shared = note(
            &mut tx,
            None,
            "shared",
            &format!("shared decision {p}"),
            "decision",
            Some(&p),
        );
        let pack = |tx: &mut postgres::Transaction<'_>, actor: &Actor| {
            build_context(
                tx,
                actor,
                &ContextRequest {
                    project: &p,
                    task: Some("secret"),
                    budget: 8_000,
                },
                None,
            )
            .unwrap()
        };
        let mine = pack(&mut tx, &a);
        assert!(
            mine.text.contains(&secret),
            "A sees her notes: {}",
            mine.text
        );
        let theirs = pack(&mut tx, &b);
        assert_hidden(&theirs.text, &secret, "the context pack");
        assert_hidden(
            &serde_json::to_string(&theirs).unwrap(),
            &secret,
            "the pack JSON",
        );
        assert!(cited_note_ids(&theirs).contains(&shared), "{}", theirs.text);
    }
}
