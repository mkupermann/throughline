//! Secrets found in session messages and AI configuration files (M24).
//!
//! The local database may hold every secret the user ever pasted. This
//! module finds the ones with a clear pattern, stores each value once in
//! `secrets` and every place it was seen in `secret_occurrences`. The list
//! the API serves carries only the masked value. The clear value leaves the
//! database only through [`reveal`], which writes a row to `secret_reveals`.
//!
//! Nothing here is reachable from MCP, search, handoff, ask or the exports.

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use postgres::GenericClient;
use regex::Regex;
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::sync::OnceLock;

/// One secret found in a text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Found {
    pub kind: &'static str,
    pub value: String,
}

/// The kinds, in the order [`find`] tries them. A value matched by an
/// earlier kind is not reported again by a later one.
pub const KINDS: [&str; 10] = [
    "private_key",
    "anthropic_key",
    "openai_key",
    "github_token",
    "aws_key_id",
    "google_key",
    "slack_token",
    "stripe_key",
    "jwt",
    "bearer_token",
];

/// Patterns with a clear shape only. Mirrors pii.rs, without the
/// KEY=VALUE, e-mail and path rules: those are not secrets here.
/// The second field is the capture group that holds the value.
fn patterns() -> &'static [(&'static str, Regex, usize)] {
    static P: OnceLock<Vec<(&'static str, Regex, usize)>> = OnceLock::new();
    P.get_or_init(|| {
        let raw: [(&str, &str, usize); 11] = [
            (
                "private_key",
                r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----",
                0,
            ),
            ("anthropic_key", r"sk-ant-[a-zA-Z0-9_\-]{40,}", 0),
            ("openai_key", r"sk-(?:proj|svcacct)-[a-zA-Z0-9_\-]{20,}", 0),
            ("openai_key", r"\bsk-[a-zA-Z0-9]{40,}\b", 0),
            ("github_token", r"\bgh[pousr]_[A-Za-z0-9]{30,}\b", 0),
            ("aws_key_id", r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", 0),
            ("google_key", r"\bAIza[0-9A-Za-z_\-]{35}\b", 0),
            ("slack_token", r"\bxox[baprs]-[A-Za-z0-9\-]{20,}\b", 0),
            (
                "stripe_key",
                r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{24,}\b",
                0,
            ),
            (
                "jwt",
                r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b",
                0,
            ),
            (
                "bearer_token",
                r"(?i)Authorization\s*:\s*Bearer\s+([A-Za-z0-9\.\-_]{10,})",
                1,
            ),
        ];
        raw.into_iter()
            .map(|(k, p, g)| (k, Regex::new(p).expect("valid secret regex"), g))
            .collect()
    })
}

/// A cheap PostgreSQL pre-filter (POSIX regex) for messages that may hold
/// a secret. [`find`] decides. Every pattern above has a prefix here.
const PREFILTER: &str = "sk-ant-|sk-proj-|sk-svcacct-|sk-[A-Za-z0-9]{40}|gh[pousr]_[A-Za-z0-9]{30}\
|A[KS]IA[A-Z0-9]{16}|AIza|xox[baprs]-|[spr]k_(live|test)_|eyJ[A-Za-z0-9_-]{10}|PRIVATE KEY-----\
|[Aa][Uu][Tt][Hh][Oo][Rr][Ii][Zz][Aa][Tt][Ii][Oo][Nn]";

/// Every secret with a clear pattern in `text`, each value once.
/// Test placeholders (see pii.rs) are dropped.
pub fn find(text: &str) -> Vec<Found> {
    let mut seen: HashSet<String> = HashSet::new();
    let mut out = Vec::new();
    for (kind, re, group) in patterns() {
        for caps in re.captures_iter(text) {
            let Some(m) = caps.get(*group) else { continue };
            let value = m.as_str();
            if crate::pii::is_test_placeholder(value) {
                continue;
            }
            if seen.insert(value.to_string()) {
                out.push(Found {
                    kind,
                    value: value.to_string(),
                });
            }
        }
    }
    out
}

/// sha256 of the value, hex. The unique key of a secret.
pub fn fingerprint(value: &str) -> String {
    format!("{:x}", Sha256::digest(value.as_bytes()))
}

/// The value as the list shows it: a private key without its body, a short
/// value fully hidden, else the first and last four characters.
pub fn mask(kind: &str, value: &str) -> String {
    if kind == "private_key" {
        return "-----BEGIN … PRIVATE KEY-----".to_string();
    }
    let chars: Vec<char> = value.chars().collect();
    if chars.len() < 12 {
        return "••••".to_string();
    }
    let head: String = chars[..4].iter().collect();
    let tail: String = chars[chars.len() - 4..].iter().collect();
    format!("{head}•••{tail}")
}

/// What one scan did.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct ScanSummary {
    pub messages_scanned: usize,
    pub configs_scanned: usize,
    pub found: usize,
    pub new_secrets: usize,
    pub new_occurrences: usize,
}

pub fn summary_line(s: &ScanSummary) -> String {
    format!(
        "scan-secrets: messages={} configs={} found={} new={} occurrences+={}",
        s.messages_scanned, s.configs_scanned, s.found, s.new_secrets, s.new_occurrences
    )
}

/// Where a secret was seen.
struct Source<'a> {
    source_type: &'static str,
    reference: String,
    seen_at: Option<DateTime<Utc>>,
    message_id: Option<i64>,
    conversation_id: Option<i64>,
    project: Option<&'a str>,
    tool: Option<&'a str>,
    config_path: Option<&'a str>,
}

/// A configuration file to scan: path, tool, project.
pub struct ConfigFile {
    pub path: std::path::PathBuf,
    pub tool: String,
    pub project: Option<String>,
}

/// Messages per database round trip.
const WINDOW: i64 = 20_000;
/// Configuration files above this size are skipped.
const MAX_CONFIG_BYTES: u64 = 1024 * 1024;

/// Scan messages and the AI configuration files into `secrets`.
/// `full = false` reads only messages newer than the last scan.
/// One transaction. Times come from the source, never from the clock.
pub fn scan(client: &mut postgres::Client, full: bool) -> Result<ScanSummary> {
    let configs: Vec<ConfigFile> = crate::assistants::inventory(Some(client))
        .into_iter()
        .map(|e| ConfigFile {
            path: e.path,
            tool: e.tool,
            project: e.project,
        })
        .collect();
    let mut tx = client.transaction().context("begin secret scan")?;
    let summary = scan_in(&mut tx, &configs, full)?;
    tx.commit().context("commit secret scan")?;
    Ok(summary)
}

/// The scan inside a caller's transaction, with the configuration files
/// given. Tests pass none.
pub fn scan_in<C: GenericClient>(
    c: &mut C,
    configs: &[ConfigFile],
    full: bool,
) -> Result<ScanSummary> {
    let mut s = ScanSummary::default();
    let since: i64 = if full {
        0
    } else {
        c.query_opt(
            "SELECT last_message_id FROM secret_scan_state WHERE id = 1",
            &[],
        )?
        .map(|r| r.get(0))
        .unwrap_or(0)
    };
    let row = c.query_one(
        "SELECT count(*), coalesce(max(id), $1) FROM messages WHERE id > $1",
        &[&since],
    )?;
    let (count, upto): (i64, i64) = (row.get(0), row.get(1));
    s.messages_scanned = usize::try_from(count).unwrap_or(0);

    let mut lo = since;
    while lo < upto {
        let hi = (lo + WINDOW).min(upto);
        let rows = c.query(
            "SELECT m.id, m.conversation_id, m.created_at, \
                    coalesce(m.content, '') || E'\\n' || coalesce(m.tool_calls::text, ''), \
                    c.project_name, c.source_tool \
             FROM messages m JOIN conversations c ON c.id = m.conversation_id \
             WHERE m.id > $1 AND m.id <= $2 \
               AND (m.content ~ $3 OR m.tool_calls::text ~ $3) \
             ORDER BY m.id",
            &[&lo, &hi, &PREFILTER],
        )?;
        for r in rows {
            let id: i64 = r.get(0);
            let conversation_id: i64 = r.get(1);
            let created_at: DateTime<Utc> = r.get(2);
            let text: String = r.get(3);
            let project: Option<String> = r.get(4);
            let tool: Option<String> = r.get(5);
            let src = Source {
                source_type: "message",
                reference: id.to_string(),
                seen_at: Some(created_at),
                message_id: Some(id),
                conversation_id: Some(conversation_id),
                project: project.as_deref(),
                tool: tool.as_deref(),
                config_path: None,
            };
            for f in find(&text) {
                record(c, &f, &src, &mut s)?;
            }
        }
        lo = hi;
    }

    for cfg in configs {
        let Ok(meta) = std::fs::metadata(&cfg.path) else {
            continue;
        };
        if !meta.is_file() || meta.len() > MAX_CONFIG_BYTES {
            continue;
        }
        let Ok(bytes) = std::fs::read(&cfg.path) else {
            continue;
        };
        s.configs_scanned += 1;
        let text = String::from_utf8_lossy(&bytes);
        let path = cfg.path.to_string_lossy();
        let src = Source {
            source_type: "config",
            reference: path.to_string(),
            seen_at: meta.modified().ok().map(DateTime::<Utc>::from),
            message_id: None,
            conversation_id: None,
            project: cfg.project.as_deref(),
            tool: Some(cfg.tool.as_str()),
            config_path: Some(path.as_ref()),
        };
        for f in find(&text) {
            record(c, &f, &src, &mut s)?;
        }
    }

    c.execute(
        "UPDATE secrets s SET occurrences = o.n \
         FROM (SELECT secret_id, count(*)::int AS n FROM secret_occurrences GROUP BY secret_id) o \
         WHERE o.secret_id = s.id AND s.occurrences <> o.n",
        &[],
    )?;
    c.execute(
        "INSERT INTO secret_scan_state (id, last_message_id) VALUES (1, $1) \
         ON CONFLICT (id) DO UPDATE SET last_message_id = EXCLUDED.last_message_id",
        &[&upto],
    )?;
    Ok(s)
}

/// The upsert of one finding. The source fields switch to the new sighting
/// only when it is earlier than the stored first sighting. All SET
/// expressions see the old row, so the order of the columns does not matter.
fn upsert_sql() -> &'static str {
    static SQL: OnceLock<String> = OnceLock::new();
    SQL.get_or_init(|| {
        let earlier = "(EXCLUDED.first_seen_at < secrets.first_seen_at \
                       OR (secrets.first_seen_at IS NULL AND EXCLUDED.first_seen_at IS NOT NULL))";
        let follow = |col: &str| {
            format!("{col} = CASE WHEN {earlier} THEN EXCLUDED.{col} ELSE secrets.{col} END")
        };
        let cols = [
            "source_type",
            "message_id",
            "conversation_id",
            "project",
            "tool",
            "config_path",
        ];
        let sets: Vec<String> = cols.into_iter().map(follow).collect();
        format!(
            "INSERT INTO secrets (kind, value, fingerprint, masked, first_seen_at, last_seen_at, \
                                  source_type, message_id, conversation_id, project, tool, config_path) \
             VALUES ($1, $2, $3, $4, $5, $5, $6, $7, $8, $9, $10, $11) \
             ON CONFLICT (fingerprint) DO UPDATE SET {}, \
               first_seen_at = LEAST(secrets.first_seen_at, EXCLUDED.first_seen_at), \
               last_seen_at = GREATEST(secrets.last_seen_at, EXCLUDED.last_seen_at) \
             RETURNING id, (xmax = 0)",
            sets.join(", ")
        )
    })
}

/// Upsert one finding. The source fields follow the earliest sighting.
fn record<C: GenericClient>(
    c: &mut C,
    f: &Found,
    src: &Source<'_>,
    s: &mut ScanSummary,
) -> Result<()> {
    s.found += 1;
    let row = c.query_one(
        upsert_sql(),
        &[
            &f.kind,
            &f.value,
            &fingerprint(&f.value),
            &mask(f.kind, &f.value),
            &src.seen_at,
            &src.source_type,
            &src.message_id,
            &src.conversation_id,
            &src.project,
            &src.tool,
            &src.config_path,
        ],
    )?;
    let id: i64 = row.get(0);
    if row.get::<_, bool>(1) {
        s.new_secrets += 1;
    }
    let n = c.execute(
        "INSERT INTO secret_occurrences (secret_id, source_type, ref, seen_at) \
         VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
        &[&id, &src.source_type, &src.reference, &src.seen_at],
    )?;
    s.new_occurrences += usize::try_from(n).unwrap_or(0);
    Ok(())
}

/// One row of the list. Carries no clear value.
#[derive(Debug, Clone, serde::Serialize)]
pub struct SecretRow {
    pub id: i64,
    pub kind: String,
    pub masked: String,
    pub first_seen_at: Option<DateTime<Utc>>,
    pub last_seen_at: Option<DateTime<Utc>>,
    pub occurrences: i32,
    pub source_type: String,
    pub message_id: Option<i64>,
    pub conversation_id: Option<i64>,
    pub project: Option<String>,
    pub tool: Option<String>,
    pub config_path: Option<String>,
}

/// Filters for [`list`].
#[derive(Debug, Default, Clone)]
pub struct ListQuery<'a> {
    pub kind: Option<&'a str>,
    pub project: Option<&'a str>,
    /// Searched in project, tool, config path and kind, never in the value.
    pub q: Option<&'a str>,
    pub order_desc: bool,
    pub limit: i64,
    pub offset: i64,
}

/// The secrets, masked, newest first by default, and the total matching.
pub fn list<C: GenericClient>(c: &mut C, q: &ListQuery<'_>) -> Result<(Vec<SecretRow>, i64)> {
    let pattern =
        q.q.map(|s| format!("%{}%", s.replace('%', "\\%").replace('_', "\\_")));
    let filter = "($1::text IS NULL OR kind = $1) \
                  AND ($2::text IS NULL OR project = $2) \
                  AND ($3::text IS NULL OR project ILIKE $3 OR tool ILIKE $3 \
                       OR config_path ILIKE $3 OR kind ILIKE $3)";
    let total: i64 = c
        .query_one(
            &format!("SELECT count(*) FROM secrets WHERE {filter}"),
            &[&q.kind, &q.project, &pattern],
        )?
        .get(0);
    let dir = if q.order_desc { "DESC" } else { "ASC" };
    let rows = c.query(
        &format!(
            "SELECT id, kind, masked, first_seen_at, last_seen_at, occurrences, source_type, \
                    message_id, conversation_id, project, tool, config_path \
             FROM secrets WHERE {filter} \
             ORDER BY first_seen_at {dir} NULLS LAST, id {dir} LIMIT $4 OFFSET $5"
        ),
        &[&q.kind, &q.project, &pattern, &q.limit, &q.offset],
    )?;
    let out = rows
        .iter()
        .map(|r| SecretRow {
            id: r.get(0),
            kind: r.get(1),
            masked: r.get(2),
            first_seen_at: r.get(3),
            last_seen_at: r.get(4),
            occurrences: r.get(5),
            source_type: r.get(6),
            message_id: r.get(7),
            conversation_id: r.get(8),
            project: r.get(9),
            tool: r.get(10),
            config_path: r.get(11),
        })
        .collect();
    Ok((out, total))
}

/// The clear value of one secret, or `None` for an unknown id. Every reveal
/// writes a row to `secret_reveals`.
pub fn reveal<C: GenericClient>(c: &mut C, id: i64) -> Result<Option<String>> {
    let Some(row) = c.query_opt("SELECT value FROM secrets WHERE id = $1", &[&id])? else {
        return Ok(None);
    };
    c.execute("INSERT INTO secret_reveals (secret_id) VALUES ($1)", &[&id])?;
    Ok(Some(row.get(0)))
}

/// Number of secrets per kind, by kind.
pub fn kinds<C: GenericClient>(c: &mut C) -> Result<Vec<(String, i64)>> {
    Ok(c.query(
        "SELECT kind, count(*) FROM secrets GROUP BY kind ORDER BY kind",
        &[],
    )?
    .iter()
    .map(|r| (r.get(0), r.get(1)))
    .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    // Test values are built at run time, never written as a literal, so
    // GitHub push protection never sees a secret-shaped string here.
    fn body(n: usize) -> String {
        "Ab1Cd2Ef3".repeat(n)
    }

    fn sample(kind: &str) -> String {
        match kind {
            "private_key" => format!(
                "-----BEGIN {k} PRIVATE KEY-----\n{b}\n-----END {k} PRIVATE KEY-----",
                k = "RSA",
                b = body(8)
            ),
            "anthropic_key" => format!("sk-{}-{}", "ant", body(5)),
            "openai_key" => format!("sk-{}-{}", "proj", body(3)),
            "github_token" => format!("gh{}_{}", "p", body(4)),
            "aws_key_id" => format!("{}{}", "AKIA", "QWER7TYU8IOP9ASD"),
            "google_key" => format!("{}{}", "AIza", &body(4)[..35]),
            "slack_token" => format!("xox{}-{}", "b", body(3)),
            "stripe_key" => format!("sk_{}_{}", "live", body(3)),
            "jwt" => format!("ey{}.{}.{}", "JhbGciOiJIUzI1", body(2), body(2)),
            "bearer_token" => body(3),
            _ => unreachable!(),
        }
    }

    #[test]
    fn every_kind_is_found_once() {
        for kind in KINDS {
            let value = sample(kind);
            let text = if kind == "bearer_token" {
                format!("curl -H 'Authorization: Bearer {value}' https://api")
            } else {
                format!("before {value} after {value}")
            };
            let found = find(&text);
            assert_eq!(found.len(), 1, "{kind}: one finding");
            assert_eq!(found[0].kind, kind);
            assert_eq!(found[0].value, value, "{kind}: the value itself");
        }
    }

    #[test]
    fn bearer_value_has_no_prefix() {
        let token = body(3);
        let found = find(&format!("authorization:   bearer {token}"));
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].value, token);
        assert!(!found[0].value.to_lowercase().contains("bearer"));
    }

    #[test]
    fn placeholder_is_dropped() {
        let fake = format!("gh{}_{}", "p", "x".repeat(36));
        assert!(find(&fake).is_empty());
        let dummy = format!("sk-{}-{}{}", "ant", "dummy", body(5));
        assert!(find(&dummy).is_empty());
    }

    #[test]
    fn email_and_password_are_not_secrets() {
        assert!(find("write to anna.berg@kupermann.com").is_empty());
        assert!(find("PASSWORD=Sup3rGeheim2026").is_empty());
        assert!(find("/Users/anna/project").is_empty());
    }

    #[test]
    fn mask_keeps_four_and_four() {
        let v = sample("github_token");
        let m = mask("github_token", &v);
        assert!(m.starts_with(&v[..4]));
        assert!(m.ends_with(&v[v.len() - 4..]));
        assert!(m.contains("•••"));
        assert!(!m.contains(&v[4..v.len() - 4]));
        assert_eq!(mask("bearer_token", "short1234"), "••••");
    }

    #[test]
    fn private_key_mask_has_no_body() {
        let v = sample("private_key");
        let m = mask("private_key", &v);
        assert_eq!(m, "-----BEGIN … PRIVATE KEY-----");
        assert!(!m.contains(&body(8)));
    }

    #[test]
    fn fingerprint_is_stable_sha256() {
        let v = sample("aws_key_id");
        assert_eq!(fingerprint(&v), fingerprint(&v));
        assert_eq!(fingerprint(&v).len(), 64);
        assert_ne!(fingerprint(&v), fingerprint(&sample("jwt")));
    }

    #[test]
    fn summary_line_names_every_count() {
        let s = ScanSummary {
            messages_scanned: 5,
            configs_scanned: 2,
            found: 3,
            new_secrets: 1,
            new_occurrences: 2,
        };
        assert_eq!(
            summary_line(&s),
            "scan-secrets: messages=5 configs=2 found=3 new=1 occurrences+=2"
        );
    }

    #[test]
    fn prefilter_covers_every_kind() {
        let re = Regex::new(PREFILTER).expect("prefilter is a valid regex");
        for kind in KINDS {
            let value = sample(kind);
            let text = if kind == "bearer_token" {
                format!("Authorization: Bearer {value}")
            } else {
                value
            };
            assert!(re.is_match(&text), "{kind} passes the pre-filter");
        }
    }

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

    fn conversation<C: GenericClient>(c: &mut C) -> i64 {
        c.query_one(
            "INSERT INTO conversations (session_id, project_path, started_at, source_tool) \
             VALUES (gen_random_uuid(), '/work/m24-test', '2020-01-01T00:00:00Z', 'claude_code') \
             RETURNING id",
            &[],
        )
        .expect("conversation")
        .get(0)
    }

    fn message<C: GenericClient>(c: &mut C, conv: i64, text: &str, at: &str) -> i64 {
        c.query_one(
            "INSERT INTO messages (conversation_id, role, content, created_at) \
             VALUES ($1, 'user', $2, $3::text::timestamptz) RETURNING id",
            &[&conv, &text, &at],
        )
        .expect("message")
        .get(0)
    }

    #[test]
    fn scan_keeps_one_secret_with_two_sightings() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping scan_keeps_one_secret_with_two_sightings: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("tx");
        let value = sample("github_token");
        let conv = conversation(&mut tx);
        let later = message(
            &mut tx,
            conv,
            &format!("later {value}"),
            "2021-06-01T10:00:00Z",
        );
        let earlier = message(
            &mut tx,
            conv,
            &format!("first {value}"),
            "2020-03-01T08:00:00Z",
        );
        let first = scan_in(&mut tx, &[], true).expect("first scan");
        let second = scan_in(&mut tx, &[], true).expect("second scan");
        assert!(first.new_secrets >= 1);
        assert_eq!(second.new_secrets, 0, "second run adds no secret");
        assert_eq!(second.new_occurrences, 0, "second run adds no sighting");
        let row = tx
            .query_one(
                "SELECT id, occurrences, first_seen_at, last_seen_at, message_id \
                 FROM secrets WHERE fingerprint = $1",
                &[&fingerprint(&value)],
            )
            .expect("one secret");
        let id: i64 = row.get(0);
        let occurrences: i32 = row.get(1);
        let first_seen: DateTime<Utc> = row.get(2);
        let last_seen: DateTime<Utc> = row.get(3);
        let message_id: i64 = row.get(4);
        assert_eq!(occurrences, 2);
        assert_eq!(first_seen.to_rfc3339(), "2020-03-01T08:00:00+00:00");
        assert_eq!(last_seen.to_rfc3339(), "2021-06-01T10:00:00+00:00");
        assert_eq!(message_id, earlier, "source follows the earliest sighting");
        assert_ne!(message_id, later);
        let n: i64 = tx
            .query_one(
                "SELECT count(*) FROM secret_occurrences WHERE secret_id = $1",
                &[&id],
            )
            .expect("count")
            .get(0);
        assert_eq!(n, 2);
        let secrets_n: i64 = tx
            .query_one(
                "SELECT count(*) FROM secrets WHERE fingerprint = $1",
                &[&fingerprint(&value)],
            )
            .expect("count")
            .get(0);
        assert_eq!(secrets_n, 1);
    }

    #[test]
    fn incremental_scan_reads_only_new_messages() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping incremental_scan_reads_only_new_messages: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("tx");
        let conv = conversation(&mut tx);
        message(&mut tx, conv, "nothing here", "2020-01-01T00:00:00Z");
        scan_in(&mut tx, &[], false).expect("catch up");
        let value = sample("slack_token");
        message(
            &mut tx,
            conv,
            &format!("new {value}"),
            "2020-01-02T00:00:00Z",
        );
        let s = scan_in(&mut tx, &[], false).expect("incremental");
        assert_eq!(s.messages_scanned, 1);
        assert_eq!(s.found, 1);
        let again = scan_in(&mut tx, &[], false).expect("nothing new");
        assert_eq!(again.messages_scanned, 0);
    }

    #[test]
    fn config_file_is_scanned_with_its_mtime() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping config_file_is_scanned_with_its_mtime: no PostgreSQL reachable");
            return;
        };
        let dir = std::env::temp_dir().join(format!("tl-m24-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("dir");
        let path = dir.join("settings.json");
        let value = sample("anthropic_key");
        std::fs::write(&path, format!("{{\"key\": \"{value}\"}}")).expect("write");
        let mtime = DateTime::<Utc>::from(std::fs::metadata(&path).unwrap().modified().unwrap());
        let mut tx = client.transaction().expect("tx");
        let cfg = ConfigFile {
            path: path.clone(),
            tool: "claude_code".into(),
            project: None,
        };
        let s = scan_in(&mut tx, std::slice::from_ref(&cfg), false).expect("scan");
        assert_eq!(s.configs_scanned, 1);
        let row = tx
            .query_one(
                "SELECT source_type, config_path, first_seen_at FROM secrets WHERE fingerprint = $1",
                &[&fingerprint(&value)],
            )
            .expect("found");
        let source: String = row.get(0);
        let config_path: String = row.get(1);
        let seen: DateTime<Utc> = row.get(2);
        assert_eq!(source, "config");
        assert_eq!(config_path, path.to_string_lossy());
        assert_eq!(seen.timestamp(), mtime.timestamp());
        drop(tx);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn list_has_no_clear_value_and_reveal_logs() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping list_has_no_clear_value_and_reveal_logs: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("tx");
        let value = sample("stripe_key");
        let conv = conversation(&mut tx);
        message(
            &mut tx,
            conv,
            &format!("pay {value}"),
            "2020-05-05T05:05:05Z",
        );
        scan_in(&mut tx, &[], true).expect("scan");
        let (rows, total) = list(
            &mut tx,
            &ListQuery {
                kind: Some("stripe_key"),
                order_desc: true,
                limit: 50,
                ..Default::default()
            },
        )
        .expect("list");
        assert!(total >= 1);
        let json = serde_json::to_string(&rows).expect("json");
        assert!(!json.contains(&value), "the list never carries the value");
        let id = rows
            .iter()
            .find(|r| r.masked == mask("stripe_key", &value))
            .expect("our secret")
            .id;
        let before: i64 = tx
            .query_one(
                "SELECT count(*) FROM secret_reveals WHERE secret_id = $1",
                &[&id],
            )
            .unwrap()
            .get(0);
        assert_eq!(reveal(&mut tx, id).expect("reveal"), Some(value));
        let after: i64 = tx
            .query_one(
                "SELECT count(*) FROM secret_reveals WHERE secret_id = $1",
                &[&id],
            )
            .unwrap()
            .get(0);
        assert_eq!(after, before + 1);
        assert_eq!(reveal(&mut tx, -1).expect("unknown"), None);
        let kinds = kinds(&mut tx).expect("kinds");
        assert!(kinds.iter().any(|(k, n)| k == "stripe_key" && *n >= 1));
    }
}
