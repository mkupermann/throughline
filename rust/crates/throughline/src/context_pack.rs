//! Context pack: header, section headings and cited lines packed into a hard token budget.

use chrono::{DateTime, Utc};
use serde::Serialize;

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
}
