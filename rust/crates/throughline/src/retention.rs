//! Retention and archiving — deletion as a deliberate, auditable act.
//!
//! Default is dry-run: the report shows what a policy would hit and touches
//! nothing. `--apply` requires `--archive-dir`: every affected conversation is
//! exported as Markdown plus `evidence-manifest.json` first (same shape as
//! `export-markdown --evidence`), the notes extracted from them go into
//! `memory-chunks.json`, then rows are deleted in one transaction.
//!
//! Windows shorter than [`MIN_RETENTION_DAYS`] are refused unless
//! `--allow-short-retention` is passed explicitly (EU AI Act Art. 19 works with
//! a six-month mindset for keeping logs). The measure is
//! `COALESCE(ended_at, started_at)` of the conversation. Active
//! `memory_chunks` without a conversation link are never deleted.

use anyhow::{bail, Context, Result};
use chrono::{DateTime, Utc};
use postgres::GenericClient;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// EU AI Act Art. 19 six-month mindset. Below this, refuse without override.
pub const MIN_RETENTION_DAYS: i64 = 180;

/// One conversation the policy would hit.
#[derive(Debug, Clone)]
pub struct TargetConversation {
    pub id: i64,
    pub session_id: String,
    pub project_name: String,
    pub started_at: DateTime<Utc>,
    pub ended_at: Option<DateTime<Utc>>,
    pub messages: i64,
    pub memory_chunks: i64,
}

impl TargetConversation {
    /// Measure for age: `ended_at`, falling back to `started_at`.
    pub fn measured_at(&self) -> DateTime<Utc> {
        self.ended_at.unwrap_or(self.started_at)
    }
}

/// Result columns that only an applied run fills in.
#[derive(Debug, Clone, Default)]
pub struct ApplyOutcome {
    pub archived: bool,
    pub archive_dir: Option<PathBuf>,
    pub conversations_deleted: i64,
    pub messages_deleted: i64,
    pub memory_chunks_deleted: i64,
    pub embeddings_deleted: i64,
}

/// Full report shared by dry-run and apply (apply adds the result columns).
#[derive(Debug, Clone)]
pub struct RetentionReport {
    pub older_than_days: i64,
    pub project: Option<String>,
    pub short_retention_override: bool,
    pub dry_run: bool,
    pub targets: Vec<TargetConversation>,
    /// `Some` only after `--apply`.
    pub outcome: Option<ApplyOutcome>,
}

/// Refuse windows under [`MIN_RETENTION_DAYS`] without an explicit override.
///
/// 179 days is rejected, 180 is allowed, the override opens the short path.
pub fn check_retention_window(older_than_days: i64, allow_short_retention: bool) -> Result<()> {
    if older_than_days < MIN_RETENTION_DAYS && !allow_short_retention {
        bail!(
            "refusing retention --older-than {older_than_days}: shorter than {MIN_RETENTION_DAYS} days. \
EU AI Act Art. 19 works with a six-month mindset for keeping logs; \
pass --allow-short-retention to override deliberately."
        );
    }
    if older_than_days < 0 {
        bail!("retention --older-than must be >= 0, got {older_than_days}");
    }
    Ok(())
}

/// `--apply` without `--archive-dir` is not a legal way to delete.
/// Archive first, then delete — there is no delete-without-archive path.
pub fn check_apply_archive(apply: bool, archive_dir: Option<&Path>) -> Result<()> {
    if apply && archive_dir.is_none() {
        bail!(
            "retention --apply requires --archive-dir <DIR>: \
archived conversations are written first (export-markdown --evidence shape), \
then rows are deleted. There is no delete-without-archive path."
        );
    }
    Ok(())
}

/// Pure age/project filter. `cutoff` is exclusive: `measured_at < cutoff`.
///
/// A conversation is hit when `ended_at` (fallback `started_at`) is older than
/// the cutoff and the optional project filter matches exactly.
pub fn select_targets(
    candidates: &[TargetConversation],
    cutoff: DateTime<Utc>,
    project: Option<&str>,
) -> Vec<TargetConversation> {
    candidates
        .iter()
        .filter(|c| {
            if c.measured_at() >= cutoff {
                return false;
            }
            match project {
                Some(p) => c.project_name == p,
                None => true,
            }
        })
        .cloned()
        .collect()
}

/// One report line. Dry-run and apply share the base columns; apply appends
/// `archived` and `deleted`.
pub fn render_report(report: &RetentionReport) -> String {
    let mut out = String::new();
    let mode = if report.dry_run { "dry-run" } else { "apply" };
    let project = report.project.as_deref().unwrap_or("-");
    let override_note = if report.short_retention_override {
        "yes"
    } else {
        "no"
    };
    out.push_str(&format!(
        "retention {mode} older-than={}d project={project} short_retention_override={override_note}\n",
        report.older_than_days
    ));
    out.push_str("project | conversation | session | ended_at | messages | memory_chunks");
    if !report.dry_run {
        out.push_str(" | archived | deleted");
    }
    out.push('\n');
    if report.targets.is_empty() {
        out.push_str("(no matching conversations)\n");
    }
    for t in &report.targets {
        let ended = t
            .ended_at
            .map(|d| d.to_rfc3339())
            .unwrap_or_else(|| "-".into());
        out.push_str(&format!(
            "{} | {} | {} | {ended} | {} | {}",
            t.project_name, t.id, t.session_id, t.messages, t.memory_chunks
        ));
        if !report.dry_run {
            let outcome = report.outcome.as_ref();
            let archived = match outcome {
                Some(o) if o.archived => "yes",
                Some(_) => "no",
                None => "-",
            };
            let deleted = match outcome {
                Some(o) => format!(
                    "conv={} msg={} chunk={}",
                    o.conversations_deleted, o.messages_deleted, o.memory_chunks_deleted
                ),
                None => "-".into(),
            };
            out.push_str(&format!(" | {archived} | {deleted}"));
        }
        out.push('\n');
    }

    // Per-project rollup: conversations, messages, memory_chunks (source_id link).
    let mut by_project: BTreeMap<&str, (i64, i64, i64)> = BTreeMap::new();
    for t in &report.targets {
        let e = by_project
            .entry(t.project_name.as_str())
            .or_insert((0, 0, 0));
        e.0 += 1;
        e.1 += t.messages;
        e.2 += t.memory_chunks;
    }
    for (proj, (convs, msgs, chunks)) in &by_project {
        out.push_str(&format!(
            "project {proj}: conversations={convs} messages={msgs} memory_chunks={chunks}\n"
        ));
    }

    let (tc, tm, tk) = report.targets.iter().fold((0i64, 0i64, 0i64), |acc, t| {
        (acc.0 + 1, acc.1 + t.messages, acc.2 + t.memory_chunks)
    });
    out.push_str(&format!(
        "totals: conversations={tc} messages={tm} memory_chunks={tk}\n"
    ));
    if let Some(o) = &report.outcome {
        out.push_str(&format!(
            "result: archived={} conversations_deleted={} messages_deleted={} memory_chunks_deleted={} embeddings_deleted={} archive_dir={}\n",
            o.archived,
            o.conversations_deleted,
            o.messages_deleted,
            o.memory_chunks_deleted,
            o.embeddings_deleted,
            o.archive_dir
                .as_ref()
                .map(|p| p.display().to_string())
                .unwrap_or_else(|| "-".into())
        ));
    }
    out
}

/// Load conversations older than `older_than_days` (measure `ended_at`, fallback
/// `started_at`), optionally restricted to one project. Counts `messages` and
/// `memory_chunks` with `source_type='conversation' AND source_id` link.
pub fn load_targets<C: GenericClient>(
    client: &mut C,
    older_than_days: i64,
    project: Option<&str>,
) -> Result<Vec<TargetConversation>> {
    let days = i32::try_from(older_than_days).unwrap_or(i32::MAX);
    let rows = client.query(
        r#"
        SELECT c.id,
               c.session_id::text,
               COALESCE(c.project_name, 'unknown'),
               c.started_at,
               c.ended_at,
               (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id),
               (SELECT COUNT(*) FROM memory_chunks mc
                 WHERE mc.source_type = 'conversation' AND mc.source_id = c.id)
        FROM conversations c
        WHERE COALESCE(c.ended_at, c.started_at) < now() - make_interval(days => $1)
          AND ($2::text IS NULL OR c.project_name = $2)
        ORDER BY COALESCE(c.ended_at, c.started_at) ASC, c.id ASC
        "#,
        &[&days, &project],
    )?;
    Ok(rows
        .into_iter()
        .map(|r| TargetConversation {
            id: r.get(0),
            session_id: r.get(1),
            project_name: r.get(2),
            started_at: r.get(3),
            ended_at: r.get(4),
            messages: r.get(5),
            memory_chunks: r.get(6),
        })
        .collect())
}

/// Write the archive for the affected conversations into `archive_dir` using the
/// existing `export-markdown --evidence` shape (Markdown per session plus
/// `evidence-manifest.json`).
pub fn archive_targets<C: GenericClient>(
    client: &mut C,
    archive_dir: &Path,
    targets: &[TargetConversation],
) -> Result<()> {
    let ids: Vec<i64> = targets.iter().map(|t| t.id).collect();
    let summary = crate::export_markdown::export_markdown(
        client,
        archive_dir,
        None,
        true,  // include generated conversations: they are archived too
        false, // no redact: the archive is the full record
        true,  // evidence-manifest.json
        Some(&ids),
    )
    .context("archive export-markdown --evidence")?;
    if summary.sessions != targets.len() {
        bail!(
            "archive wrote {} session(s) but {} conversation(s) were targeted",
            summary.sessions,
            targets.len()
        );
    }
    // Notes extracted from these conversations are deleted with them, so
    // their full rows go into the archive as well.
    let notes: Vec<serde_json::Value> = client
        .query(
            "SELECT row_to_json(mc) FROM memory_chunks mc
             WHERE source_type = 'conversation' AND source_id = ANY($1)
             ORDER BY id",
            &[&ids],
        )
        .context("read notes for the archive")?
        .into_iter()
        .map(|r| r.get(0))
        .collect();
    let path = archive_dir.join("memory-chunks.json");
    std::fs::write(&path, serde_json::to_string_pretty(&notes)? + "\n")
        .with_context(|| format!("write {}", path.display()))?;
    Ok(())
}

/// Delete the selected conversations and everything that hangs off them.
///
/// FK-safe order inside one transaction:
/// 1. embeddings of the messages and of the linked memory_chunks,
/// 2. entity_mentions pointing at those messages / chunks / conversations,
/// 3. clear `memory_chunks.superseded_by` references into the deleted set,
/// 4. linked `memory_chunks` (`source_type='conversation'`),
/// 5. conversations (messages cascade),
/// 6. one `memory_reflections` audit row (Zeitpunkt, Policy, Zähler, Archivpfad).
///
/// Active memory_chunks without a conversation link are never touched.
pub fn apply_retention<C: GenericClient>(
    client: &mut C,
    targets: &[TargetConversation],
    older_than_days: i64,
    project: Option<&str>,
    short_retention_override: bool,
    archive_dir: &Path,
) -> Result<ApplyOutcome> {
    let conv_ids: Vec<i64> = targets.iter().map(|t| t.id).collect();
    let mut outcome = ApplyOutcome {
        archived: true,
        archive_dir: Some(archive_dir.to_path_buf()),
        ..Default::default()
    };
    if conv_ids.is_empty() {
        // Still record the applied policy so the run is auditable.
        write_audit(
            client,
            &[],
            older_than_days,
            project,
            short_retention_override,
            archive_dir,
            &outcome,
        )?;
        return Ok(outcome);
    }

    let msg_ids: Vec<i64> = client
        .query(
            "SELECT id FROM messages WHERE conversation_id = ANY($1)",
            &[&conv_ids],
        )?
        .into_iter()
        .map(|r| r.get(0))
        .collect();

    let chunk_ids: Vec<i64> = client.query(
        "SELECT id FROM memory_chunks WHERE source_type = 'conversation' AND source_id = ANY($1)",
        &[&conv_ids],
    )?
    .into_iter()
    .map(|r| r.get(0))
    .collect();

    let mut tx = client
        .transaction()
        .context("begin retention transaction")?;

    // Optional companions on a core-only schema. A failed statement aborts the
    // surrounding transaction, so probe first (to_regclass) and skip.
    let has_embeddings: bool = tx
        .query_one("SELECT to_regclass('public.embeddings') IS NOT NULL", &[])
        .map(|r| r.get(0))
        .unwrap_or(false);
    let has_entity_mentions: bool = tx
        .query_one(
            "SELECT to_regclass('public.entity_mentions') IS NOT NULL",
            &[],
        )
        .map(|r| r.get(0))
        .unwrap_or(false);

    // 1. embeddings of deleted messages and of deleted memory_chunks
    if has_embeddings {
        let emb = tx.execute(
            r#"
            DELETE FROM embeddings
            WHERE (source_type = 'message' AND source_id = ANY($1))
               OR (source_type = 'memory_chunk' AND source_id = ANY($2))
            "#,
            &[&msg_ids, &chunk_ids],
        )?;
        outcome.embeddings_deleted = emb as i64;
    }

    // 2. entity mentions that point at the deleted rows (no FK, clean up anyway)
    if has_entity_mentions {
        tx.execute(
            r#"
            DELETE FROM entity_mentions
            WHERE (source_type = 'message' AND source_id = ANY($1))
               OR (source_type = 'memory_chunk' AND source_id = ANY($2))
               OR (source_type = 'conversation' AND source_id = ANY($3))
            "#,
            &[&msg_ids, &chunk_ids, &conv_ids],
        )?;
    }

    // 3. FK memory_chunks.superseded_by → memory_chunks(id) ON DELETE SET NULL
    //    (explicit so the delete below never trips the constraint)
    tx.execute(
        "UPDATE memory_chunks SET superseded_by = NULL WHERE superseded_by = ANY($1)",
        &[&chunk_ids],
    )?;

    // 4. linked memory_chunks only — never free-standing active chunks
    outcome.memory_chunks_deleted = tx.execute(
        "DELETE FROM memory_chunks WHERE id = ANY($1)",
        &[&chunk_ids],
    )? as i64;

    // 5. conversations; messages cascade via messages_conversation_id_fkey
    outcome.conversations_deleted =
        tx.execute("DELETE FROM conversations WHERE id = ANY($1)", &[&conv_ids])? as i64;
    outcome.messages_deleted = msg_ids.len() as i64;

    // 6. audit summary row: Zeitpunkt (default now), Policy, Zähler, Archivpfad
    write_audit(
        &mut tx,
        &chunk_ids,
        older_than_days,
        project,
        short_retention_override,
        archive_dir,
        &outcome,
    )?;

    tx.commit().context("commit retention transaction")?;
    Ok(outcome)
}

/// One summary line in `memory_reflections` (same table `audit` reads and
/// `forget` writes to). Chosen over `ingestion_log` because that table keys on
/// `(file_path, file_hash)` for source files and has no room for policy,
/// counters and archive path; `memory_reflections` already carries
/// `reflection_type`, `affected_chunks`, `action_taken`, `reasoning`.
///
/// The table is created on first use so a Rust-core-only install (no full
/// Python schema) can still record the audit line.
fn write_audit<C: GenericClient>(
    client: &mut C,
    chunk_ids: &[i64],
    older_than_days: i64,
    project: Option<&str>,
    short_retention_override: bool,
    archive_dir: &Path,
    outcome: &ApplyOutcome,
) -> Result<()> {
    client.batch_execute(
        r#"
        CREATE TABLE IF NOT EXISTS memory_reflections (
            id BIGSERIAL PRIMARY KEY,
            reflection_type TEXT NOT NULL,
            affected_chunks BIGINT[],
            action_taken TEXT,
            reasoning TEXT,
            confidence NUMERIC(3,2),
            created_at TIMESTAMPTZ DEFAULT now()
        )
        "#,
    )?;
    let project = project.unwrap_or("-");
    let action = format!(
        "applied: conversations={} messages={} memory_chunks={} embeddings={} archive_dir={}",
        outcome.conversations_deleted,
        outcome.messages_deleted,
        outcome.memory_chunks_deleted,
        outcome.embeddings_deleted,
        archive_dir.display()
    );
    let reasoning = format!(
        "policy older-than={older_than_days}d project={project} short_retention_override={}",
        if short_retention_override {
            "yes"
        } else {
            "no"
        }
    );
    client.execute(
        r#"
        INSERT INTO memory_reflections
            (reflection_type, affected_chunks, action_taken, reasoning, confidence)
        VALUES ('retention', $1, $2, $3, 1.0)
        "#,
        &[&chunk_ids.to_vec(), &action, &reasoning],
    )?;
    Ok(())
}

/// Entry point for the CLI: validate, load, optionally archive+delete, render.
pub fn run_retention<C: GenericClient>(
    client: &mut C,
    older_than_days: i64,
    project: Option<&str>,
    archive_dir: Option<&Path>,
    apply: bool,
    allow_short_retention: bool,
) -> Result<RetentionReport> {
    check_retention_window(older_than_days, allow_short_retention)?;
    check_apply_archive(apply, archive_dir)?;

    let targets = load_targets(client, older_than_days, project)?;
    let short_retention_override = allow_short_retention && older_than_days < MIN_RETENTION_DAYS;

    let mut report = RetentionReport {
        older_than_days,
        project: project.map(|p| p.to_string()),
        short_retention_override,
        dry_run: !apply,
        targets,
        outcome: None,
    };

    if !apply {
        return Ok(report);
    }

    let archive_dir = archive_dir
        .map(|p| p.to_path_buf())
        .context("apply requires archive_dir")?;
    archive_targets(client, &archive_dir, &report.targets)?;
    let outcome = apply_retention(
        client,
        &report.targets,
        older_than_days,
        project,
        short_retention_override,
        &archive_dir,
    )?;
    report.outcome = Some(outcome);
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Duration;

    fn cand(id: i64, project: &str, started: &str, ended: Option<&str>) -> TargetConversation {
        TargetConversation {
            id,
            session_id: format!("session-{id}"),
            project_name: project.into(),
            started_at: started.parse().unwrap(),
            ended_at: ended.map(|e| e.parse().unwrap()),
            messages: 2,
            memory_chunks: 1,
        }
    }

    // ---- K1: Fristprüfung -------------------------------------------------

    #[test]
    fn min_retention_rejects_179() {
        let err = check_retention_window(179, false).unwrap_err();
        let msg = format!("{err}");
        assert!(msg.contains("179"), "message names the window: {msg}");
        assert!(
            msg.contains("EU AI Act") && msg.contains("Art. 19"),
            "message names the six-month mindset: {msg}"
        );
        assert!(msg.contains("180"), "message names the threshold: {msg}");
    }

    #[test]
    fn min_retention_allows_180() {
        check_retention_window(180, false).expect("180 days must be allowed");
    }

    #[test]
    fn min_retention_override_allows_short() {
        check_retention_window(30, true).expect("override must allow short windows");
    }

    #[test]
    fn min_retention_still_rejects_negative() {
        assert!(check_retention_window(-1, true).is_err());
    }

    // ---- K1: Apply-ohne-Archiv-Abbruch -----------------------------------

    #[test]
    fn apply_without_archive_dir_aborts() {
        let err = check_apply_archive(true, None).unwrap_err();
        let msg = format!("{err}");
        assert!(
            msg.contains("--archive-dir"),
            "names the missing flag: {msg}"
        );
        assert!(msg.contains("apply") || msg.contains("--apply"), "{msg}");
    }

    #[test]
    fn apply_with_archive_dir_allowed() {
        check_apply_archive(true, Some(Path::new("/tmp/arch"))).expect("legal apply");
    }

    #[test]
    fn dry_run_without_archive_dir_allowed() {
        check_apply_archive(false, None).expect("dry-run needs no archive dir");
    }

    // ---- K1: Auswahl nach ended_at und Projekt ----------------------------

    #[test]
    fn select_targets_uses_ended_at_and_started_at_fallback() {
        let cutoff: DateTime<Utc> = "2026-01-01T00:00:00Z".parse().unwrap();
        let candidates = vec![
            // ended_at old → hit
            cand(1, "A", "2025-06-01T00:00:00Z", Some("2025-06-02T00:00:00Z")),
            // started_at old, ended_at recent → miss (ended_at wins)
            cand(2, "A", "2025-01-01T00:00:00Z", Some("2026-06-01T00:00:00Z")),
            // no ended_at, started_at old → hit (fallback)
            cand(3, "A", "2025-03-01T00:00:00Z", None),
            // no ended_at, started_at recent → miss
            cand(4, "A", "2026-06-01T00:00:00Z", None),
        ];
        let hits = select_targets(&candidates, cutoff, None);
        let ids: Vec<i64> = hits.iter().map(|t| t.id).collect();
        assert_eq!(
            ids,
            vec![1, 3],
            "ended_at first, started_at only as fallback"
        );
    }

    #[test]
    fn select_targets_filters_by_project() {
        let cutoff: DateTime<Utc> = "2026-01-01T00:00:00Z".parse().unwrap();
        let candidates = vec![
            cand(1, "Atlas-demo", "2025-01-01T00:00:00Z", None),
            cand(2, "Harbor-demo", "2025-01-01T00:00:00Z", None),
            cand(3, "Atlas-demo", "2025-02-01T00:00:00Z", None),
        ];
        let hits = select_targets(&candidates, cutoff, Some("Atlas-demo"));
        assert_eq!(hits.len(), 2);
        assert!(hits.iter().all(|t| t.project_name == "Atlas-demo"));
        let ids: Vec<i64> = hits.iter().map(|t| t.id).collect();
        assert_eq!(ids, vec![1, 3]);

        let none = select_targets(&candidates, cutoff, Some("Nope"));
        assert!(none.is_empty(), "unknown project matches nothing");
    }

    // ---- Reports: dry-run and apply share the base line shape -------------

    #[test]
    fn dry_run_report_has_no_result_columns() {
        let report = RetentionReport {
            older_than_days: 200,
            project: None,
            short_retention_override: false,
            dry_run: true,
            targets: vec![cand(
                42,
                "Atlas-demo",
                "2025-01-01T00:00:00Z",
                Some("2025-01-02T00:00:00Z"),
            )],
            outcome: None,
        };
        let text = render_report(&report);
        assert!(text.contains("retention dry-run older-than=200d"), "{text}");
        assert!(text.contains("short_retention_override=no"), "{text}");
        assert!(text.contains("Atlas-demo | 42 | session-42"), "{text}");
        assert!(text.contains("totals: conversations=1"), "{text}");
        assert!(
            !text.contains("| archived |"),
            "dry-run has no result columns"
        );
        assert!(!text.contains("result:"), "dry-run has no result line");
    }

    #[test]
    fn apply_report_adds_result_columns() {
        let report = RetentionReport {
            older_than_days: 200,
            project: Some("Atlas-demo".into()),
            short_retention_override: false,
            dry_run: false,
            targets: vec![cand(
                42,
                "Atlas-demo",
                "2025-01-01T00:00:00Z",
                Some("2025-01-02T00:00:00Z"),
            )],
            outcome: Some(ApplyOutcome {
                archived: true,
                archive_dir: Some(PathBuf::from("/tmp/arch")),
                conversations_deleted: 1,
                messages_deleted: 2,
                memory_chunks_deleted: 1,
                embeddings_deleted: 0,
            }),
        };
        let text = render_report(&report);
        assert!(text.contains("retention apply older-than=200d"), "{text}");
        assert!(text.contains("| archived | deleted"), "{text}");
        // Base columns still present and in the same order:
        assert!(
            text.contains("project | conversation | session | ended_at | messages | memory_chunks"),
            "{text}"
        );
        assert!(text.contains("Atlas-demo | 42 | session-42"), "{text}");
        assert!(text.contains("result: archived=true"), "{text}");
    }

    #[test]
    fn short_retention_override_is_noted_in_report() {
        let report = RetentionReport {
            older_than_days: 30,
            project: None,
            short_retention_override: true,
            dry_run: true,
            targets: vec![],
            outcome: None,
        };
        let text = render_report(&report);
        assert!(text.contains("short_retention_override=yes"), "{text}");
    }

    // ---- K1: Dry-Run verändert nichts (DB, skip ohne PostgreSQL) ----------

    /// Connect to the throwaway DB (`PG*` env). Skip when unreachable so
    /// offline `cargo test --lib` stays green.
    fn test_db() -> Option<postgres::Client> {
        crate::config::DbConfig::from_env().connect().ok()
    }

    #[test]
    fn dry_run_does_not_mutate_database() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping dry_run_does_not_mutate_database: no PostgreSQL reachable");
            return;
        };
        // One transaction that is never committed: seed, dry-run, compare, roll back.
        let mut tx = client.transaction().expect("begin test tx");
        tx.execute(
            r#"
            INSERT INTO conversations (session_id, project_path, started_at, ended_at, message_count)
            VALUES ('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee', '/fictional/m11-dryrun',
                    now() - interval '300 days', now() - interval '290 days', 1)
            "#,
            &[],
        )
        .expect("seed conversation");
        let before: i64 = tx
            .query_one(
                "SELECT (SELECT COUNT(*) FROM conversations) + (SELECT COUNT(*) FROM messages) + (SELECT COUNT(*) FROM memory_chunks)",
                &[],
            )
            .expect("count before")
            .get(0);

        let report =
            run_retention(&mut tx, 200, None, None, false, false).expect("dry-run must succeed");
        assert!(
            !report.targets.is_empty(),
            "seeded old conversation must be selected"
        );
        assert!(report.dry_run);
        assert!(report.outcome.is_none());

        let after: i64 = tx
            .query_one(
                "SELECT (SELECT COUNT(*) FROM conversations) + (SELECT COUNT(*) FROM messages) + (SELECT COUNT(*) FROM memory_chunks)",
                &[],
            )
            .expect("count after")
            .get(0);
        assert_eq!(before, after, "dry-run must not change any row count");
        // Drop tx → rollback, nothing left behind.
    }

    #[test]
    fn apply_archives_the_notes_it_deletes() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping apply_archives_the_notes_it_deletes: no PostgreSQL reachable");
            return;
        };
        // Never committed. The project filter keeps every other row out of reach.
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("ret-{tag}");
        let session = uuid::Uuid::new_v4();
        let conv_id: i64 = tx
            .query_one(
                &format!(
                    "INSERT INTO conversations (session_id, project_path, started_at, ended_at, message_count)
                     VALUES ('{session}', '/fictional/{project}',
                             now() - interval '300 days', now() - interval '290 days', 1)
                     RETURNING id"
                ),
                &[],
            )
            .expect("seed conversation")
            .get(0);
        tx.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at)
             VALUES ($1, 'user', 'hello', now() - interval '295 days')",
            &[&conv_id],
        )
        .expect("seed message");
        let note = format!("a decision only the note knows {tag}");
        tx.execute(
            "INSERT INTO memory_chunks (source_type, source_id, content, category, project_name, status)
             VALUES ('conversation', $1, $2, 'decision', $3, 'active')",
            &[&conv_id, &note, &project],
        )
        .expect("seed note");

        let archive = tempfile::tempdir().unwrap();
        let report = run_retention(
            &mut tx,
            200,
            Some(&project),
            Some(archive.path()),
            true,
            false,
        )
        .expect("apply");
        assert_eq!(report.outcome.as_ref().unwrap().memory_chunks_deleted, 1);

        let archived = walkdir::WalkDir::new(archive.path())
            .into_iter()
            .filter_map(|e| e.ok())
            .filter(|e| e.file_type().is_file())
            .any(|e| std::fs::read_to_string(e.path()).is_ok_and(|t| t.contains(&note)));
        assert!(archived, "deleted note is not in the archive");
    }

    #[test]
    fn select_targets_in_sql_matches_ended_at_measure() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping select_targets_in_sql_matches_ended_at_measure: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        // old ended_at → hit; recent ended_at despite old started_at → miss
        tx.execute(
            r#"
            INSERT INTO conversations (session_id, project_path, started_at, ended_at, message_count)
            VALUES ('bbbbbbbb-bbbb-4ccc-8ddd-eeeeeeeeeeee', '/fictional/m11-hit',
                    now() - interval '400 days', now() - interval '300 days', 0),
                   ('cccccccc-bbbb-4ccc-8ddd-eeeeeeeeeeee', '/fictional/m11-miss',
                    now() - interval '400 days', now() - interval '10 days', 0)
            "#,
            &[],
        )
        .expect("seed");
        let hits = load_targets(&mut tx, 200, None).expect("load");
        let ids: Vec<i64> = hits
            .iter()
            .filter(|t| {
                t.session_id.starts_with("bbbbbbbb") || t.session_id.starts_with("cccccccc")
            })
            .map(|t| t.id)
            .collect();
        let b_id: i64 = tx
            .query_one(
                "SELECT id FROM conversations WHERE session_id = 'bbbbbbbb-bbbb-4ccc-8ddd-eeeeeeeeeeee'",
                &[],
            )
            .unwrap()
            .get(0);
        let c_id: i64 = tx
            .query_one(
                "SELECT id FROM conversations WHERE session_id = 'cccccccc-bbbb-4ccc-8ddd-eeeeeeeeeeee'",
                &[],
            )
            .unwrap()
            .get(0);
        assert!(ids.contains(&b_id), "old ended_at must be hit: {ids:?}");
        assert!(
            !ids.contains(&c_id),
            "recent ended_at must be missed: {ids:?}"
        );

        // project filter
        let by_project = load_targets(&mut tx, 200, Some("m11-miss")).expect("load project");
        assert!(
            by_project.iter().all(|t| t.project_name == "m11-miss"),
            "project filter must restrict the set"
        );
    }

    #[test]
    fn apply_requires_archive_is_enforced_before_any_query() {
        // Pure gate: even with a live DB the apply path refuses without archive.
        let err = run_retention_hook();
        assert!(err.is_err());
    }

    fn run_retention_hook() -> Result<()> {
        // Simulate the CLI gate without a DB.
        check_retention_window(30, true)?;
        check_apply_archive(true, None)
    }

    #[test]
    fn cutoff_math_matches_sql_interval() {
        // `now() - make_interval(days => N)` is exclusive at the boundary.
        let now: DateTime<Utc> = Utc::now();
        let older = now - Duration::days(200);
        let younger = now - Duration::days(100);
        let candidates = vec![
            TargetConversation {
                id: 1,
                session_id: "old".into(),
                project_name: "P".into(),
                started_at: older,
                ended_at: Some(older),
                messages: 0,
                memory_chunks: 0,
            },
            TargetConversation {
                id: 2,
                session_id: "young".into(),
                project_name: "P".into(),
                started_at: younger,
                ended_at: Some(younger),
                messages: 0,
                memory_chunks: 0,
            },
        ];
        let cutoff = now - Duration::days(180);
        let hits = select_targets(&candidates, cutoff, None);
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].id, 1);
    }
}
