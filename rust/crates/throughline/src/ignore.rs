//! Ingest exclusion rules (privacy).
//!
//! What matches a rule is skipped before parsing and never reaches the
//! database. The ingest log keeps counters and rule kinds only — never the
//! path or content of an excluded file.
//!
//! Rule sources (all optional, combined as a union):
//! (a) `~/.throughline-ignore` — one rule per line, `#` starts a comment,
//! (b) `THROUGHLINE_INGEST_IGNORE` — comma-separated rules,
//! (c) CLI `ingest --ignore <rule>` — repeatable.
//!
//! Rule kinds: `path:<glob>` matches the absolute source path (`*`, `**`),
//! `project:<name>` matches the recognised project name exactly
//! (case-insensitive). A rule without prefix is a `path:` rule.

use std::path::Path;

/// Which side of ingest a rule filters on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuleKind {
    Path,
    Project,
}

impl RuleKind {
    pub fn as_str(self) -> &'static str {
        match self {
            RuleKind::Path => "path",
            RuleKind::Project => "project",
        }
    }
}

/// One exclusion rule as written by the user.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IgnoreRule {
    pub kind: RuleKind,
    pub pattern: String,
}

impl IgnoreRule {
    /// Parse a single rule. Empty lines and `#` comments yield `None`.
    /// A rule without prefix is a `path:` rule.
    pub fn parse(raw: &str) -> Option<IgnoreRule> {
        let s = raw.trim();
        if s.is_empty() || s.starts_with('#') {
            return None;
        }
        if let Some(rest) = s.strip_prefix("path:") {
            let pattern = rest.trim();
            if pattern.is_empty() {
                return None;
            }
            return Some(IgnoreRule {
                kind: RuleKind::Path,
                pattern: pattern.to_string(),
            });
        }
        if let Some(rest) = s.strip_prefix("project:") {
            let pattern = rest.trim();
            if pattern.is_empty() {
                return None;
            }
            return Some(IgnoreRule {
                kind: RuleKind::Project,
                pattern: pattern.to_string(),
            });
        }
        Some(IgnoreRule {
            kind: RuleKind::Path,
            pattern: s.to_string(),
        })
    }
}

/// Rules from one source, as text.
pub fn parse_rule_lines(text: &str) -> Vec<IgnoreRule> {
    text.lines().filter_map(IgnoreRule::parse).collect()
}

/// Comma-separated rules (environment variable form).
pub fn parse_rule_csv(raw: &str) -> Vec<IgnoreRule> {
    raw.split(',').filter_map(IgnoreRule::parse).collect()
}

/// CLI `--ignore` arguments, one rule each.
pub fn parse_rule_args(args: &[String]) -> Vec<IgnoreRule> {
    args.iter().filter_map(|a| IgnoreRule::parse(a)).collect()
}

/// Read `~/.throughline-ignore`. Missing file is silent; an unreadable file
/// warns on stderr and contributes no rules (the ingest must not abort).
pub fn read_ignore_file(path: &Path) -> Vec<IgnoreRule> {
    match std::fs::read_to_string(path) {
        Ok(text) => parse_rule_lines(&text),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Vec::new(),
        Err(e) => {
            eprintln!(
                "[throughline] warning: cannot read ignore file {}: {e}",
                path.display()
            );
            Vec::new()
        }
    }
}

/// Compiled union of all rule sources.
#[derive(Debug, Default)]
pub struct IgnoreRules {
    rules: Vec<CompiledRule>,
}

#[derive(Debug)]
struct CompiledRule {
    kind: RuleKind,
    path_glob: Option<glob::Pattern>,
    project_lower: Option<String>,
}

impl IgnoreRules {
    /// Union of the three sources. Every matching rule excludes; sources
    /// never cancel each other out.
    pub fn load(cli: &[String]) -> IgnoreRules {
        let mut rules = Vec::new();
        if let Some(home) = dirs::home_dir() {
            rules.extend(read_ignore_file(&home.join(".throughline-ignore")));
        }
        if let Ok(raw) = std::env::var("THROUGHLINE_INGEST_IGNORE") {
            rules.extend(parse_rule_csv(&raw));
        }
        rules.extend(parse_rule_args(cli));
        IgnoreRules::from_rules(rules)
    }

    pub fn from_rules(rules: Vec<IgnoreRule>) -> IgnoreRules {
        let compiled = rules
            .into_iter()
            .map(|r| CompiledRule {
                kind: r.kind,
                path_glob: if r.kind == RuleKind::Path {
                    glob::Pattern::new(&r.pattern).ok()
                } else {
                    None
                },
                project_lower: if r.kind == RuleKind::Project {
                    Some(r.pattern.to_lowercase())
                } else {
                    None
                },
            })
            .collect();
        IgnoreRules { rules: compiled }
    }

    pub fn is_empty(&self) -> bool {
        self.rules.is_empty()
    }

    pub fn len(&self) -> usize {
        self.rules.len()
    }

    /// First `path:` rule matching the absolute source file path, if any.
    pub fn matches_path(&self, path: &Path) -> bool {
        let haystack = path_to_glob_text(path);
        self.rules.iter().any(|r| {
            r.kind == RuleKind::Path
                && r.path_glob
                    .as_ref()
                    .map(|g| g.matches_with(&haystack, MATCH_OPTIONS))
                    .unwrap_or(false)
        })
    }

    /// `project:` rule matching the recognised project name (case-insensitive).
    pub fn matches_project(&self, project_name: &str) -> bool {
        let name = project_name.to_lowercase();
        self.rules.iter().any(|r| {
            r.kind == RuleKind::Project && r.project_lower.as_deref() == Some(name.as_str())
        })
    }
}

const MATCH_OPTIONS: glob::MatchOptions = glob::MatchOptions {
    case_sensitive: true,
    require_literal_separator: true,
    require_literal_leading_dot: false,
};

/// Absolute source path in glob-matchable form (forward slashes).
fn path_to_glob_text(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

/// Project name as the schema derives it from `project_path`
/// (`split_part(replace(project_path, '\', '/'), '/', -1)`, NULL → `unknown`).
pub fn project_name_of(project_path: Option<&str>) -> String {
    match project_path {
        None => "unknown".to_string(),
        Some(p) => {
            let norm = p.replace('\\', "/");
            match norm.rsplit('/').next() {
                Some(last) => last.to_string(),
                None => String::new(),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn rules(items: &[(&str, &str)]) -> IgnoreRules {
        IgnoreRules::from_rules(
            items
                .iter()
                .map(|(k, p)| IgnoreRule {
                    kind: if *k == "project" {
                        RuleKind::Project
                    } else {
                        RuleKind::Path
                    },
                    pattern: (*p).to_string(),
                })
                .collect(),
        )
    }

    // --- rule parsing (file, env, CLI, comments) ---

    #[test]
    fn parse_file_lines_comments_and_blanks() {
        let text = "\
# whole-line comment
path:**/drop.jsonl

project:secret
   # indented comment
bare-glob.jsonl
project:
path:
";
        let parsed = parse_rule_lines(text);
        assert_eq!(
            parsed,
            vec![
                IgnoreRule {
                    kind: RuleKind::Path,
                    pattern: "**/drop.jsonl".into()
                },
                IgnoreRule {
                    kind: RuleKind::Project,
                    pattern: "secret".into()
                },
                IgnoreRule {
                    kind: RuleKind::Path,
                    pattern: "bare-glob.jsonl".into()
                },
            ]
        );
    }

    #[test]
    fn parse_env_is_comma_separated() {
        let parsed = parse_rule_csv("**/a.jsonl,project:demo,**/b.jsonl");
        assert_eq!(parsed.len(), 3);
        assert_eq!(parsed[0].kind, RuleKind::Path);
        assert_eq!(
            parsed[1],
            IgnoreRule {
                kind: RuleKind::Project,
                pattern: "demo".into()
            }
        );
        assert_eq!(parsed[2].pattern, "**/b.jsonl");
        // trailing comma / empties do not create rules
        assert_eq!(parse_rule_csv("project:x,").len(), 1);
        assert!(parse_rule_csv("").is_empty());
    }

    #[test]
    fn parse_cli_args_one_rule_each() {
        let args = vec![
            "path:**/keep.jsonl".to_string(),
            "project:Secret".to_string(),
            "# a comment is not a rule".to_string(),
            "bare.jsonl".to_string(),
        ];
        let parsed = parse_rule_args(&args);
        assert_eq!(parsed.len(), 3);
        assert_eq!(parsed[0].kind, RuleKind::Path);
        assert_eq!(parsed[1].kind, RuleKind::Project);
        assert_eq!(parsed[2].kind, RuleKind::Path);
        assert_eq!(parsed[2].pattern, "bare.jsonl");
    }

    #[test]
    fn unprefixed_rule_is_path() {
        let r = IgnoreRule::parse("**/x.jsonl").unwrap();
        assert_eq!(r.kind, RuleKind::Path);
        assert_eq!(r.pattern, "**/x.jsonl");
    }

    /// Throwaway dir for file-reading tests, removed when the test ends.
    fn unit_dir() -> tempfile::TempDir {
        tempfile::tempdir().expect("temp dir")
    }

    #[test]
    fn read_ignore_file_missing_is_silent_and_empty() {
        let dir = unit_dir();
        let missing = dir.path().join("definitely-not-a-real-ignore-file");
        assert!(read_ignore_file(&missing).is_empty());
    }

    #[test]
    fn read_ignore_file_unreadable_does_not_abort() {
        // A directory cannot be read as a file → warn path, zero rules, no panic.
        let dir = unit_dir();
        assert!(read_ignore_file(dir.path()).is_empty());
    }

    #[test]
    fn read_ignore_file_parses_rules() {
        let dir = unit_dir();
        let path = dir.path().join("unit_ignore_file");
        std::fs::write(&path, "# c\nproject:acme\n**/drop.jsonl\n").unwrap();
        let parsed = read_ignore_file(&path);
        assert_eq!(parsed.len(), 2);
        assert_eq!(parsed[0].kind, RuleKind::Project);
        assert_eq!(parsed[1].kind, RuleKind::Path);
    }

    // --- path glob match, positive and negative ---

    #[test]
    fn path_glob_match_positive() {
        let r = rules(&[("path", "**/drop.jsonl")]);
        assert!(r.matches_path(&PathBuf::from("C:/corpus/m9-demo/drop.jsonl")));
        assert!(r.matches_path(&PathBuf::from("/home/u/proj/drop.jsonl")));
        let star = rules(&[("path", "*/demo/*.jsonl")]);
        assert!(star.matches_path(&PathBuf::from("corpus/demo/keep.jsonl")));
    }

    #[test]
    fn path_glob_match_negative() {
        let r = rules(&[("path", "**/drop.jsonl")]);
        assert!(!r.matches_path(&PathBuf::from("C:/corpus/m9-demo/keep.jsonl")));
        assert!(!r.matches_path(&PathBuf::from("C:/corpus/drop.jsonl/inner")));
        // `*` does not cross separators when require_literal_separator is on
        let star = rules(&[("path", "*.jsonl")]);
        assert!(star.matches_path(&PathBuf::from("keep.jsonl")));
        assert!(!star.matches_path(&PathBuf::from("sub/keep.jsonl")));
        // `**` does
        let rec = rules(&[("path", "**/m9-*/**")]);
        assert!(rec.matches_path(&PathBuf::from("x/m9-demo/inner/keep.jsonl")));
        assert!(!rec.matches_path(&PathBuf::from("x/other/inner/keep.jsonl")));
    }

    #[test]
    fn windows_separators_match_forward_slash_globs() {
        let r = rules(&[("path", "**/m9-demo/drop.jsonl")]);
        assert!(r.matches_path(&PathBuf::from("C:\\corpus\\m9-demo\\drop.jsonl")));
    }

    // --- project match, case-insensitive ---

    #[test]
    fn project_match_case_insensitive() {
        let r = rules(&[("project", "Secret")]);
        assert!(r.matches_project("secret"));
        assert!(r.matches_project("SECRET"));
        assert!(r.matches_project("Secret"));
        assert!(!r.matches_project("secretive"));
        assert!(!r.matches_project("m9-demo"));
    }

    #[test]
    fn project_name_of_follows_schema() {
        assert_eq!(project_name_of(None), "unknown");
        assert_eq!(project_name_of(Some("/work/m9-demo")), "m9-demo");
        assert_eq!(project_name_of(Some("C:\\work\\m9-demo")), "m9-demo");
        assert_eq!(project_name_of(Some("m9-demo")), "m9-demo");
    }

    // --- precedence / union of the three sources ---

    #[test]
    fn three_sources_are_unioned() {
        // file rule, env rule, CLI rule — each must exclude on its own.
        let mut ruleset = Vec::new();
        ruleset.extend(parse_rule_lines("path:**/from-file.jsonl\n"));
        ruleset.extend(parse_rule_csv("path:**/from-env.jsonl,project:envproj"));
        ruleset.extend(parse_rule_args(&["path:**/from-cli.jsonl".into()]));
        let u = IgnoreRules::from_rules(ruleset);
        assert_eq!(u.len(), 4);
        assert!(u.matches_path(&PathBuf::from("/a/from-file.jsonl")));
        assert!(u.matches_path(&PathBuf::from("/a/from-env.jsonl")));
        assert!(u.matches_path(&PathBuf::from("/a/from-cli.jsonl")));
        assert!(u.matches_project("EnvProj"));
        assert!(!u.matches_path(&PathBuf::from("/a/other.jsonl")));
        assert!(!u.matches_project("other"));
    }

    #[test]
    fn sources_do_not_override_each_other() {
        // The same pattern from two sources still excludes (no allow-list,
        // no negation, later sources never cancel earlier ones).
        let mut ruleset = parse_rule_lines("path:**/drop.jsonl\n");
        ruleset.extend(parse_rule_args(&["path:**/drop.jsonl".into()]));
        let u = IgnoreRules::from_rules(ruleset);
        assert_eq!(u.len(), 2);
        assert!(u.matches_path(&PathBuf::from("/x/drop.jsonl")));
    }

    #[test]
    fn empty_ruleset_matches_nothing() {
        let u = IgnoreRules::default();
        assert!(u.is_empty());
        assert!(!u.matches_path(&PathBuf::from("/x/drop.jsonl")));
        assert!(!u.matches_project("secret"));
    }
}
