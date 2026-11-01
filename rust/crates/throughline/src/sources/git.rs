//! Git repositories as a source.
//!
//! Reads the local branches of every repository in `THROUGHLINE_GIT_ROOTS`
//! (colon-separated, `~` expanded against `$HOME`) with the `git` binary,
//! started without a shell. `safe.directory` is set for the configured
//! roots only, and git never looks above a root for a repository. Without
//! roots the source does nothing; without a `git` binary it is not present
//! (`doctor` says so) and `ingest --source all` goes on with the rest.
//!
//! One repository is one conversation:
//! - `session_id` is a stable UUID of `git/<folder name>`, `project_path` is
//!   the root, so the project is the folder name; `source_tool` is `git`;
//! - each commit is a message, role `document`: subject, a blank line, the
//!   body, and `Files:` with up to 50 changed paths (`and N more` after
//!   that; a merge lists none). `created_at` is the committer date, the
//!   metadata hold the SHA, the number of parents and of changed files;
//! - a message's uuid is a stable UUID of its SHA, so a rerun inserts only
//!   the commits it has not stored and never rewrites one. Commits that a
//!   history rewrite drops stay in the store.
//!
//! No author: the log format asks git for no author or committer name or
//! mail, and the text loses trailer lines that name people (`Signed-off-by:`,
//! `Co-authored-by:`, `Cc:` …), mail addresses and the account in a GitHub
//! merge subject. There is nothing to filter commits by person (organisation
//! rule: no employee monitoring). A name written into the text itself stays.
//!
//! A run hashes `git for-each-ref refs/heads` first and skips a repository
//! whose hash is in the ingestion log. Otherwise it reads the newest 5,000
//! commits across the local branches, in date order, with one line on
//! stderr that counts the older commits it did not read.

use crate::adapters::stable_uuid;
use crate::ignore::{project_name_of, IgnoreRules};
use crate::types::{IngestSummary, NormalisedConversation, NormalisedMessage, Role};
use crate::writer as w;
use anyhow::{bail, Context, Result};
use chrono::{DateTime, Utc};
use postgres::GenericClient;
use regex::Regex;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;
use uuid::Uuid;

/// Source name, as `ingest --source` takes it and `source_tool` stores it.
pub const NAME: &str = "git";
/// Colon-separated repository roots; `~` is expanded against `$HOME`.
pub const ROOTS_ENV: &str = "THROUGHLINE_GIT_ROOTS";
/// Commits read per repository and run, the newest across local branches.
pub const MAX_COMMITS: usize = 5_000;
/// Changed paths listed per commit.
pub const MAX_FILES: usize = 50;

const NS: Uuid = Uuid::from_u128(0x5b0e_2c47_8d1a_4f63_b9e2_71c4_d08a_6f35);

/// SHA, parents, committer date (Unix seconds), subject, body. Nothing
/// about a person. Records start with 0x1E, fields end with NUL, then `-z
/// --name-only` adds the changed paths, each ending with NUL.
const LOG_FORMAT: &str = "%x1e%H%x00%P%x00%ct%x00%s%x00%b%x00";

/// Variables that would point git at another repository than the root.
const REPO_ENV: [&str; 6] = [
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_NAMESPACE",
];

/// The configured roots and how to find `git`.
#[derive(Debug, Clone)]
pub struct GitSource {
    roots: Vec<PathBuf>,
    search_path: Option<OsString>,
}

impl GitSource {
    /// The roots in [`ROOTS_ENV`]. Unset or empty: no roots, a no-op.
    pub fn from_env() -> Self {
        Self::new(super::roots_from_env(ROOTS_ENV))
    }

    pub fn new(roots: Vec<PathBuf>) -> Self {
        Self {
            roots,
            search_path: None,
        }
    }

    /// Look for `git` on this `PATH` instead of the process's.
    pub fn with_search_path(mut self, path: impl Into<OsString>) -> Self {
        self.search_path = Some(path.into());
        self
    }

    pub fn roots(&self) -> &[PathBuf] {
        &self.roots
    }

    fn command(&self) -> Command {
        let mut cmd = Command::new("git");
        if let Some(path) = &self.search_path {
            cmd.env("PATH", path);
        }
        for var in REPO_ENV {
            cmd.env_remove(var);
        }
        cmd.stdin(Stdio::null());
        cmd
    }

    /// The version `git --version` prints, `None` when no git runs.
    pub fn version(&self) -> Option<String> {
        let out = self.command().arg("--version").output().ok()?;
        if !out.status.success() {
            return None;
        }
        let text = String::from_utf8_lossy(&out.stdout);
        let text = text.trim();
        Some(
            text.strip_prefix("git version ")
                .unwrap_or(text)
                .to_string(),
        )
    }

    /// Roots are configured and a git runs.
    pub fn is_present(&self) -> bool {
        !self.roots.is_empty() && self.version().is_some()
    }

    /// git inside `root`: no search for a repository above it, and
    /// `safe.directory` for this root alone (as given and resolved), so a
    /// repository owned by another user, as in the container, can be read.
    fn git_in(&self, root: &Path, args: &[&str]) -> Result<Vec<u8>> {
        let mut cmd = self.command();
        if let Some(parent) = root.parent() {
            cmd.env("GIT_CEILING_DIRECTORIES", parent);
        }
        let mut safe = vec![root.to_path_buf()];
        if let Ok(real) = root.canonicalize() {
            if real != root {
                safe.push(real);
            }
        }
        for dir in safe {
            let mut value = OsString::from("safe.directory=");
            value.push(dir.as_os_str());
            cmd.arg("-c").arg(value);
        }
        let out = cmd
            .arg("-C")
            .arg(root)
            .args(args)
            .output()
            .with_context(|| format!("start git {}", args[0]))?;
        if !out.status.success() {
            bail!(
                "git {} failed: {}",
                args[0],
                String::from_utf8_lossy(&out.stderr).trim()
            );
        }
        Ok(out.stdout)
    }

    /// The hash of the local branch tips, `None` before the first commit.
    fn fingerprint(&self, root: &Path) -> Result<Option<String>> {
        let refs = self.git_in(
            root,
            &[
                "for-each-ref",
                "--format=%(objectname) %(refname)",
                "refs/heads",
            ],
        )?;
        Ok((!refs.is_empty()).then(|| format!("{:x}", Sha256::digest(&refs))))
    }

    /// The newest [`MAX_COMMITS`] commits of the local branches, newest
    /// first, and how many the branches hold in all.
    fn read(&self, root: &Path) -> Result<(Vec<Commit>, usize)> {
        let max = format!("--max-count={MAX_COMMITS}");
        let format = format!("--format={LOG_FORMAT}");
        let raw = self.git_in(
            root,
            &[
                "log",
                "--branches",
                "--date-order",
                "--no-color",
                "--no-show-signature",
                "-z",
                "--name-only",
                &max,
                &format,
            ],
        )?;
        let commits = parse_log(&raw);
        let total = if commits.len() < MAX_COMMITS {
            commits.len()
        } else {
            let count = self.git_in(root, &["rev-list", "--count", "--branches"])?;
            String::from_utf8_lossy(&count)
                .trim()
                .parse()
                .context("count commits")?
        };
        Ok((commits, total))
    }
}

/// `git: <n> roots configured, git <version|missing>`.
pub fn doctor_line(source: &GitSource) -> String {
    let n = source.roots.len();
    format!(
        "git: {n} {} configured, git {}",
        if n == 1 { "root" } else { "roots" },
        source.version().as_deref().unwrap_or("missing")
    )
}

/// Ingest every configured repository. Ignore rules apply to the root
/// (`path:`) and its folder name (`project:`).
pub fn run<C: GenericClient>(
    client: &mut C,
    source: &GitSource,
    verbose: bool,
    rules: &IgnoreRules,
) -> Result<IngestSummary> {
    let mut summary = IngestSummary::new(NAME);
    if source.roots.is_empty() {
        return Ok(summary);
    }
    if source.version().is_none() {
        eprintln!(
            "[{NAME}] not present: no git binary found, {} configured root(s) not read",
            source.roots.len()
        );
        return Ok(summary);
    }
    let mut names = HashSet::new();
    for root in &source.roots {
        summary.files_seen += 1;
        let project_path = root.to_string_lossy().to_string();
        let name = project_name_of(Some(&project_path));
        if rules.matches_path(root) {
            summary.ignored += 1;
            summary.ignored_path += 1;
            continue;
        }
        if rules.matches_project(&name) {
            summary.ignored += 1;
            summary.ignored_project += 1;
            continue;
        }
        // The conversation is keyed by the folder name.
        if !names.insert(name.clone()) {
            summary.errors += 1;
            eprintln!(
                "[{NAME}] {project_path}: another root has the folder name {name:?}, skipped"
            );
            continue;
        }
        match ingest_repo(client, source, root, &name) {
            Ok(None) => {
                summary.skipped += 1;
                if verbose {
                    println!("  [{NAME}] {name}: unchanged");
                }
            }
            Ok(Some(new)) => {
                summary.ingested += 1;
                summary.messages_written += new;
                if verbose {
                    println!("  [{NAME}] {name}: {new} new commit(s)");
                }
            }
            Err(e) => {
                summary.errors += 1;
                eprintln!("[{NAME}] {project_path}: {e:#}");
            }
        }
    }
    if summary.ingested > 0 {
        best_effort(client, |tx| w::backfill_projects(tx).map(drop));
    }
    best_effort(client, |tx| {
        crate::ingest_run::record_ignore_audit(tx, NAME, &summary);
        Ok(())
    });
    Ok(summary)
}

/// Best effort, as in the adapter runner, but in a transaction of its own
/// (a savepoint when the caller holds one): a failed statement, such as the
/// projects backfill on a schema with an enum `status`, cannot abort the
/// caller's transaction.
fn best_effort<C: GenericClient>(
    client: &mut C,
    work: impl FnOnce(&mut postgres::Transaction<'_>) -> Result<()>,
) {
    if let Ok(mut tx) = client.transaction() {
        if work(&mut tx).is_ok() {
            let _ = tx.commit();
        }
    }
}

/// One repository. `None`: no branch moved since the last run (or no
/// branch yet). `Some(n)`: `n` commits were new.
fn ingest_repo<C: GenericClient>(
    client: &mut C,
    source: &GitSource,
    root: &Path,
    name: &str,
) -> Result<Option<usize>> {
    // Before the log: a commit made in between moves the hash again.
    let Some(fingerprint) = source.fingerprint(root)? else {
        return Ok(None);
    };
    let key = PathBuf::from(format!("{NAME}:{}", root.display()));
    if matches!(w::already_ingested(client, &key, &fingerprint)?, Some(n) if n != 0) {
        return Ok(None);
    }
    let (commits, total) = source.read(root)?;
    if total > commits.len() {
        eprintln!(
            "[{NAME}] {name}: read the newest {} of {total} commits, {} older not read",
            commits.len(),
            total - commits.len()
        );
    }
    let conv = conversation(root, name, &commits, total);

    let mut tx = client.transaction()?;
    let id = w::upsert_conversation(&mut tx, &conv)?;
    let before: i64 = tx
        .query_one(
            "SELECT count(*) FROM messages WHERE conversation_id = $1",
            &[&id],
        )?
        .get(0);
    w::replace_messages(&mut tx, id, &conv)?;
    // The store keeps commits a later run no longer reads (cap, rewrite).
    let stored: i32 = tx
        .query_one(
            "UPDATE conversations c SET message_count = s.n, started_at = s.first, \
             ended_at = s.last \
             FROM (SELECT count(*)::int AS n, min(created_at) AS first, \
                   max(created_at) AS last FROM messages WHERE conversation_id = $1) s \
             WHERE c.id = $1 RETURNING s.n",
            &[&id],
        )?
        .get(0);
    w::record_ingest(&mut tx, &key, &fingerprint, stored as usize)?;
    tx.commit()?;
    Ok(Some((i64::from(stored) - before).max(0) as usize))
}

fn conversation(
    root: &Path,
    name: &str,
    commits: &[Commit],
    total: usize,
) -> NormalisedConversation {
    // Oldest first, so message ids follow the history.
    let messages: Vec<NormalisedMessage> = commits
        .iter()
        .rev()
        .map(|c| NormalisedMessage {
            role: Role::Document.as_str().into(),
            content: commit_content(&c.subject, &c.body, &c.files),
            created_at: Some(c.committed),
            uuid: Some(stable_uuid(&NS, None, &c.sha)),
            metadata: json!({
                "sha": c.sha,
                "parents": c.parents,
                "files_changed": c.files.len(),
            }),
            ..Default::default()
        })
        .collect();
    let times = || messages.iter().filter_map(|m| m.created_at);
    NormalisedConversation {
        session_id: stable_uuid(&NS, None, &format!("{NAME}/{name}")),
        project_path: Some(root.to_string_lossy().to_string()),
        model: None,
        entrypoint: None,
        git_branch: None,
        started_at: times().min().unwrap_or_else(Utc::now),
        ended_at: times().max(),
        token_count_in: None,
        token_count_out: None,
        summary: Some(format!("{name} commits")),
        metadata: json!({ "commits_read": commits.len(), "commits_total": total }),
        source_tool: Some(NAME.into()),
        messages,
    }
}

/// Subject, blank line, body, blank line, `Files:` with at most
/// [`MAX_FILES`] paths. People are taken out first.
fn commit_content(subject: &str, body: &str, files: &[String]) -> String {
    let mut parts = vec![without_mail(&without_merge_account(subject.trim()))];
    let body = without_mail(&without_people_lines(body));
    if !body.is_empty() {
        parts.push(body);
    }
    if !files.is_empty() {
        let shown = files[..files.len().min(MAX_FILES)].join(", ");
        parts.push(match files.len().saturating_sub(MAX_FILES) {
            0 => format!("Files: {shown}"),
            more => format!("Files: {shown} and {more} more"),
        });
    }
    parts.join("\n\n")
}

/// Drop trailer lines that name people: `<Something>-by:` and `Cc:`.
fn without_people_lines(body: &str) -> String {
    let names_people = |line: &str| {
        line.split_once(':').is_some_and(|(key, _)| {
            let key = key.trim().to_ascii_lowercase();
            !key.is_empty()
                && key.chars().all(|c| c.is_ascii_alphabetic() || c == '-')
                && (key.ends_with("-by") || key == "cc")
        })
    };
    let kept: Vec<&str> = body.lines().filter(|l| !names_people(l)).collect();
    kept.join("\n").trim().to_string()
}

/// `Merge pull request #7 from account/branch` → `… from branch`.
fn without_merge_account(subject: &str) -> String {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"^(Merge pull request #\d+ from )[^/\s]+/(\S+)").expect("valid merge regex")
    })
    .replace(subject, "$1$2")
    .into_owned()
}

/// Mail addresses become `[mail]`.
fn without_mail(text: &str) -> String {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"<?[\w.+-]+@[\w-]+(?:\.[\w-]+)+>?").expect("valid mail regex"))
        .replace_all(text, "[mail]")
        .into_owned()
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Commit {
    sha: String,
    parents: usize,
    committed: DateTime<Utc>,
    subject: String,
    body: String,
    files: Vec<String>,
}

/// The commits in `git log` output written with [`LOG_FORMAT`]. A 0x1E
/// inside a body does not start a record: a record starts with a SHA and
/// a NUL. Malformed records are dropped.
fn parse_log(raw: &[u8]) -> Vec<Commit> {
    let starts_record = |part: &[u8]| {
        part.iter()
            .position(|b| *b == 0)
            .is_some_and(|n| (n == 40 || n == 64) && part[..n].iter().all(u8::is_ascii_hexdigit))
    };
    let mut records: Vec<Vec<u8>> = Vec::new();
    for part in raw.split(|b| *b == 0x1e).skip(1) {
        match records.last_mut() {
            Some(last) if !starts_record(part) => {
                last.push(0x1e);
                last.extend_from_slice(part);
            }
            _ => records.push(part.to_vec()),
        }
    }
    records
        .iter()
        .filter_map(|record| {
            let fields: Vec<String> = record
                .split(|b| *b == 0)
                .map(|f| String::from_utf8_lossy(f).into_owned())
                .collect();
            if fields.len() < 5 {
                return None;
            }
            let seconds: i64 = fields[2].trim().parse().ok()?;
            Some(Commit {
                sha: fields[0].clone(),
                parents: fields[1].split_whitespace().count(),
                committed: DateTime::from_timestamp(seconds, 0)?,
                subject: fields[3].clone(),
                body: fields[4].clone(),
                files: fields[5..]
                    .iter()
                    .map(|f| f.trim_start_matches('\n'))
                    .filter(|f| !f.is_empty())
                    .map(String::from)
                    .collect(),
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;
    use std::fs;

    /// Connect to the database (`PG*` env). Skip when unreachable so offline
    /// `cargo test --lib` stays green. Mirrors the writer.rs test helper.
    fn test_db() -> Option<postgres::Client> {
        use postgres::NoTls;
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

    /// Invented people. None of them may reach the store.
    const AUTHOR: (&str, &str) = ("Erika Mustermann", "erika.mustermann@example.invalid");
    const COMMITTER: (&str, &str) = ("Max Beispiel", "max.beispiel@example.invalid");
    const PEOPLE: [&str; 7] = [
        "Erika",
        "Mustermann",
        "Max Beispiel",
        "Jonas",
        "Probe",
        "example.invalid",
        "erikam",
    ];

    /// Commit 3: a body, three files, a mail address and two trailers
    /// that name people.
    const SPRING: &str = "Record the spring tide\n\n\
        Gauge read twice at dawn; ask erika.mustermann@example.invalid for the log.\n\n\
        Signed-off-by: Erika Mustermann <erika.mustermann@example.invalid>\n\
        Co-authored-by: Jonas Probe <jonas.probe@example.invalid>";
    /// Commit 25: a GitHub merge subject names the account of the branch.
    const MERGE: &str = "Merge pull request #7 from erikam/tide-fix";

    fn git_missing(test: &str) -> bool {
        let missing = GitSource::new(Vec::new()).version().is_none();
        if missing {
            eprintln!("skipping {test}: no `git` binary on PATH (the tl-rust-dev image has none; CI has one)");
        }
        missing
    }

    /// A repository built with the `git` binary from invented content. Every
    /// commit is one minute after the one before, author and committer date
    /// alike.
    struct Repo {
        tmp: tempfile::TempDir,
        root: PathBuf,
        clock: i64,
        shas: Vec<String>,
        dates: Vec<DateTime<Utc>>,
    }

    impl Repo {
        fn new(name: &str) -> Self {
            let tmp = tempfile::tempdir().unwrap();
            let root = tmp.path().join(name);
            fs::create_dir_all(&root).unwrap();
            let repo = Repo {
                tmp,
                root,
                clock: 1_900_000_000,
                shas: Vec::new(),
                dates: Vec::new(),
            };
            repo.git(&["init", "-q"]);
            repo.git(&["checkout", "-q", "-b", "main"]);
            repo
        }

        fn git(&self, args: &[&str]) -> String {
            let date = format!("{} +0000", self.clock);
            let out = Command::new("git")
                .current_dir(&self.root)
                .args(args)
                .env("HOME", self.tmp.path())
                .env("GIT_CONFIG_GLOBAL", "/dev/null")
                .env("GIT_CONFIG_NOSYSTEM", "1")
                .env("GIT_AUTHOR_NAME", AUTHOR.0)
                .env("GIT_AUTHOR_EMAIL", AUTHOR.1)
                .env("GIT_COMMITTER_NAME", COMMITTER.0)
                .env("GIT_COMMITTER_EMAIL", COMMITTER.1)
                .env("GIT_AUTHOR_DATE", &date)
                .env("GIT_COMMITTER_DATE", &date)
                .output()
                .unwrap();
            assert!(
                out.status.success(),
                "git {args:?}: {}",
                String::from_utf8_lossy(&out.stderr)
            );
            String::from_utf8_lossy(&out.stdout).trim().to_string()
        }

        fn commit(&mut self, message: &str, files: &[&str]) {
            self.clock += 60;
            for file in files {
                let path = self.root.join(file);
                fs::create_dir_all(path.parent().unwrap()).unwrap();
                fs::write(&path, format!("{message}\n{}\n", self.clock)).unwrap();
            }
            self.git(&["add", "-A"]);
            self.git(&["commit", "-q", "--allow-empty", "-m", message]);
            self.shas.push(self.git(&["rev-parse", "HEAD"]));
            self.dates
                .push(DateTime::from_timestamp(self.clock, 0).unwrap());
        }

        /// Ledger entry `n`: commit 3 is [`SPRING`], commit 25 is [`MERGE`].
        fn entry(&mut self, n: usize) {
            let file = format!("ledger/{n:02}.txt");
            match n {
                3 => self.commit(SPRING, &["tides/spring.txt", "tides/neap.txt", "gauge.txt"]),
                25 => self.commit(MERGE, &[&file]),
                _ => self.commit(&format!("Ledger entry {n:02}"), &[&file]),
            }
        }

        /// 30 commits on two branches: 1-10 on `main`, 11-20 on `soundings`
        /// (forked after 10), 21-30 on `main` again.
        fn thirty(name: &str) -> Self {
            let mut repo = Repo::new(name);
            for n in 1..=10 {
                repo.entry(n);
            }
            repo.git(&["checkout", "-q", "-b", "soundings"]);
            for n in 11..=20 {
                repo.entry(n);
            }
            repo.git(&["checkout", "-q", "main"]);
            for n in 21..=30 {
                repo.entry(n);
            }
            repo
        }
    }

    fn conversation_id<C: GenericClient>(client: &mut C, name: &str) -> Option<i64> {
        let session = stable_uuid(&NS, None, &format!("git/{name}"));
        client
            .query_opt(
                "SELECT id FROM conversations WHERE session_id = $1",
                &[&session],
            )
            .unwrap()
            .map(|r| r.get(0))
    }

    type Row = (i64, String, String, Option<Uuid>, DateTime<Utc>, Value);

    fn messages<C: GenericClient>(client: &mut C, conversation_id: i64) -> Vec<Row> {
        client
            .query(
                "SELECT id, role::text, content, uuid, created_at, metadata FROM messages \
                 WHERE conversation_id = $1 ORDER BY created_at, id",
                &[&conversation_id],
            )
            .unwrap()
            .iter()
            .map(|r| (r.get(0), r.get(1), r.get(2), r.get(3), r.get(4), r.get(5)))
            .collect()
    }

    #[test]
    fn git_repo_yields_one_conversation_with_thirty_commits_in_order() {
        let test = "git_repo_yields_one_conversation_with_thirty_commits_in_order";
        if git_missing(test) {
            return;
        }
        let Some(mut client) = test_db() else {
            eprintln!("skipping {test}: no PostgreSQL reachable");
            return;
        };
        let repo = Repo::thirty("harbor-ledger-order");
        let mut tx = client.transaction().expect("begin test tx");
        let source = GitSource::new(vec![repo.root.clone()]);
        let s = run(&mut tx, &source, false, &IgnoreRules::default()).unwrap();
        assert_eq!(
            (
                s.files_seen,
                s.ingested,
                s.skipped,
                s.errors,
                s.messages_written
            ),
            (1, 1, 0, 0, 30)
        );

        let session = stable_uuid(&NS, None, "git/harbor-ledger-order");
        let rows = tx
            .query(
                "SELECT id, project_path, project_name, source_tool, message_count \
                 FROM conversations WHERE session_id = $1",
                &[&session],
            )
            .unwrap();
        assert_eq!(rows.len(), 1, "one conversation per repository");
        let id: i64 = rows[0].get(0);
        let root = repo.root.to_str().unwrap();
        assert_eq!(rows[0].get::<_, Option<String>>(1).as_deref(), Some(root));
        assert_eq!(
            rows[0].get::<_, Option<String>>(2).as_deref(),
            Some("harbor-ledger-order")
        );
        assert_eq!(rows[0].get::<_, Option<String>>(3).as_deref(), Some(NAME));
        assert_eq!(rows[0].get::<_, i32>(4), 30);

        let rows = messages(&mut tx, id);
        assert_eq!(rows.len(), 30);
        let shas: Vec<&str> = rows.iter().map(|r| r.5["sha"].as_str().unwrap()).collect();
        assert_eq!(shas, repo.shas, "committer-date order across both branches");
        let dates: Vec<DateTime<Utc>> = rows.iter().map(|r| r.4).collect();
        assert_eq!(dates, repo.dates, "created_at is the committer date");
        for (n, row) in rows.iter().enumerate() {
            assert_eq!(row.1, "document");
            assert_eq!(row.3, Some(stable_uuid(&NS, None, &repo.shas[n])));
            assert_eq!(row.5["parents"], if n == 0 { 0 } else { 1 });
        }
        assert_eq!(
            rows[2].2,
            "Record the spring tide\n\n\
             Gauge read twice at dawn; ask [mail] for the log.\n\n\
             Files: gauge.txt, tides/neap.txt, tides/spring.txt"
        );
        assert_eq!(rows[2].5["files_changed"], 3);
        assert_eq!(rows[11].2, "Ledger entry 12\n\nFiles: ledger/12.txt");
        assert_eq!(
            rows[24].2,
            "Merge pull request #7 from tide-fix\n\nFiles: ledger/25.txt"
        );
    }

    #[test]
    fn git_second_run_adds_exactly_the_new_commits() {
        let test = "git_second_run_adds_exactly_the_new_commits";
        if git_missing(test) {
            return;
        }
        let Some(mut client) = test_db() else {
            eprintln!("skipping {test}: no PostgreSQL reachable");
            return;
        };
        let mut repo = Repo::thirty("harbor-ledger-rerun");
        let mut tx = client.transaction().expect("begin test tx");
        let source = GitSource::new(vec![repo.root.clone()]);
        let rules = IgnoreRules::default();
        let first = run(&mut tx, &source, false, &rules).unwrap();
        assert_eq!(first.messages_written, 30);
        let id = conversation_id(&mut tx, "harbor-ledger-rerun").unwrap();
        let before: Vec<i64> = messages(&mut tx, id).iter().map(|r| r.0).collect();

        repo.git(&["checkout", "-q", "soundings"]);
        repo.commit("Sounding at the north pier", &["soundings/north.txt"]);
        repo.commit("Sounding at the south pier", &["soundings/south.txt"]);
        repo.git(&["checkout", "-q", "main"]);
        repo.commit("Close the spring ledger", &["ledger/close.txt"]);

        let second = run(&mut tx, &source, false, &rules).unwrap();
        assert_eq!(
            (
                second.ingested,
                second.skipped,
                second.errors,
                second.messages_written
            ),
            (1, 0, 0, 3)
        );
        let rows = messages(&mut tx, id);
        assert_eq!(rows.len(), 33, "exactly the three new commits");
        let mut ids: Vec<i64> = rows.iter().map(|r| r.0).collect();
        ids.sort_unstable();
        assert_eq!(ids[..30], before[..], "stored commits keep their rows");
        let shas: Vec<&str> = rows.iter().map(|r| r.5["sha"].as_str().unwrap()).collect();
        assert_eq!(shas, repo.shas);
        let count: i32 = tx
            .query_one(
                "SELECT message_count FROM conversations WHERE id = $1",
                &[&id],
            )
            .unwrap()
            .get(0);
        assert_eq!(count, 33);

        // No branch moved: the repository is skipped without reading the log.
        let third = run(&mut tx, &source, false, &rules).unwrap();
        assert_eq!(
            (
                third.ingested,
                third.skipped,
                third.errors,
                third.messages_written
            ),
            (0, 1, 0, 0)
        );
        assert_eq!(messages(&mut tx, id).len(), 33);
    }

    #[test]
    fn git_commits_carry_no_author() {
        let test = "git_commits_carry_no_author";
        if git_missing(test) {
            return;
        }
        // The log format asks git for no person at all.
        for placeholder in [
            "%an", "%aN", "%ae", "%aE", "%al", "%aL", "%cn", "%cN", "%ce", "%cE", "%cl", "%cL",
            "%G",
        ] {
            assert!(
                !LOG_FORMAT.contains(placeholder),
                "{placeholder} in {LOG_FORMAT}"
            );
        }
        let Some(mut client) = test_db() else {
            eprintln!("skipping {test}: no PostgreSQL reachable");
            return;
        };
        let repo = Repo::thirty("harbor-ledger-people");
        // Negative control: the fixture does carry every person.
        let raw = repo.git(&["log", "--branches", "--format=%an %ae %cn %ce%n%B"]);
        for person in PEOPLE {
            assert!(raw.contains(person), "the fixture lacks {person:?}");
        }

        let mut tx = client.transaction().expect("begin test tx");
        let source = GitSource::new(vec![repo.root.clone()]);
        let s = run(&mut tx, &source, true, &IgnoreRules::default()).unwrap();
        assert_eq!(s.messages_written, 30);
        let session = stable_uuid(&NS, None, "git/harbor-ledger-people");
        let stored: Vec<String> = tx
            .query(
                "SELECT m.content || ' ' || m.metadata::text FROM messages m \
                 JOIN conversations c ON c.id = m.conversation_id WHERE c.session_id = $1 \
                 UNION ALL \
                 SELECT coalesce(summary, '') || ' ' || metadata::text \
                 FROM conversations WHERE session_id = $1",
                &[&session],
            )
            .unwrap()
            .iter()
            .map(|r| r.get(0))
            .collect();
        assert_eq!(stored.len(), 31);
        for text in &stored {
            for person in PEOPLE {
                assert!(!text.contains(person), "{person:?} stored in {text:?}");
            }
        }
    }

    #[test]
    fn git_missing_binary_is_reported_not_fatal() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().join("harbor-ledger-nogit");
        let bin = tmp.path().join("bin");
        fs::create_dir_all(&root).unwrap();
        fs::create_dir_all(&bin).unwrap();
        // A PATH that holds no git.
        let source = GitSource::new(vec![root]).with_search_path(&bin);
        assert_eq!(source.version(), None);
        assert!(!source.is_present());
        assert_eq!(doctor_line(&source), "git: 1 root configured, git missing");

        let Some(mut client) = test_db() else {
            eprintln!("skipping the store part of git_missing_binary_is_reported_not_fatal: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let s = run(&mut tx, &source, true, &IgnoreRules::default())
            .expect("a missing git is not an error");
        assert_eq!(
            (
                s.files_seen,
                s.ingested,
                s.skipped,
                s.errors,
                s.messages_written
            ),
            (0, 0, 0, 0, 0)
        );
        assert_eq!(conversation_id(&mut tx, "harbor-ledger-nogit"), None);
    }

    #[test]
    fn git_without_roots_is_a_noop() {
        if std::env::var_os(ROOTS_ENV).is_none() {
            assert!(GitSource::from_env().roots().is_empty());
        }
        let source = GitSource::new(Vec::new());
        assert!(!source.is_present());
        let line = doctor_line(&source);
        assert!(line.starts_with("git: 0 roots configured, git "), "{line}");

        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping the store part of git_without_roots_is_a_noop: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let s = run(&mut tx, &source, true, &IgnoreRules::default()).unwrap();
        assert_eq!(
            (
                s.files_seen,
                s.ingested,
                s.skipped,
                s.errors,
                s.messages_written
            ),
            (0, 0, 0, 0, 0)
        );
    }

    #[test]
    fn git_commit_text_caps_files_and_drops_people() {
        let files: Vec<String> = (1..=53).map(|n| format!("chart/{n:02}.txt")).collect();
        let text = commit_content("Chart the bay", "", &files);
        let expected_list: Vec<String> = files[..MAX_FILES].to_vec();
        assert_eq!(
            text,
            format!(
                "Chart the bay\n\nFiles: {} and 3 more",
                expected_list.join(", ")
            )
        );
        assert_eq!(
            commit_content("Chart the bay", "", &files[..MAX_FILES]),
            format!("Chart the bay\n\nFiles: {}", expected_list.join(", "))
        );
        assert_eq!(
            commit_content("Merge branch 'soundings'", "", &[]),
            "Merge branch 'soundings'"
        );
        assert_eq!(
            commit_content(
                MERGE,
                "Fix the tide table\n\nReviewed-by: Jonas Probe <jonas.probe@example.invalid>\nCC: Max Beispiel\nRefs: chart 7",
                &[]
            ),
            "Merge pull request #7 from tide-fix\n\nFix the tide table\n\nRefs: chart 7"
        );
    }

    #[test]
    fn git_log_records_split_at_the_marker_only() {
        let sha = "a".repeat(40);
        let parent = "b".repeat(40);
        let raw = format!(
            "\x1e{sha}\0{parent}\x001900000060\0Odd body\0line with \x1e marker\n\0\0\nx.txt\0y z.txt\0\
             \x1e{parent}\0\x001900000000\0Root\0\0\0"
        );
        let commits = parse_log(raw.as_bytes());
        assert_eq!(commits.len(), 2);
        assert_eq!(commits[0].sha, sha);
        assert_eq!(commits[0].parents, 1);
        assert_eq!(commits[0].body, "line with \x1e marker\n");
        assert_eq!(commits[0].files, ["x.txt", "y z.txt"]);
        assert_eq!(
            commits[0].committed,
            DateTime::from_timestamp(1_900_000_060, 0).unwrap()
        );
        assert_eq!(commits[1].parents, 0);
        assert!(commits[1].files.is_empty());
    }
}
