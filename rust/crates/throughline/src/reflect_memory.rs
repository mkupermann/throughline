//! Self-reflecting memory maintenance (dedup / contradictions / stale).

use crate::llm::{self, GenerationBackend};
use anyhow::Result;
use postgres::GenericClient;

#[derive(Debug, Default)]
pub struct ReflectSummary {
    pub mode: String,
    pub examined: usize,
    pub acted: usize,
    /// Groups that could not be merged (dedup); the run went on without them.
    pub failed: usize,
    pub dry_run: bool,
}

impl ReflectSummary {
    /// `acted=N failed=M`, for the operate message in the UI.
    pub fn counts(&self) -> String {
        format!("acted={} failed={}", self.acted, self.failed)
    }
}

/// Mark chunks expired / stale when their expiry has passed.
pub fn expire_stale<C: GenericClient>(client: &mut C, dry_run: bool) -> Result<ReflectSummary> {
    let mut summary = ReflectSummary {
        mode: "stale".into(),
        dry_run,
        ..Default::default()
    };
    if dry_run {
        let row = client.query_one(
            "SELECT COUNT(*) FROM memory_chunks WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at < now()",
            &[],
        )?;
        summary.examined = row.get::<_, i64>(0) as usize;
        return Ok(summary);
    }
    let n = client.execute(
        r#"
        UPDATE memory_chunks
        SET status = 'stale', updated_at = now()
        WHERE status = 'active'
          AND expires_at IS NOT NULL
          AND expires_at < now()
        "#,
        &[],
    )?;
    summary.examined = n as usize;
    summary.acted = n as usize;
    Ok(summary)
}

/// Collapse near-duplicate (category, project, content-prefix) groups into
/// one new `consolidation` note each. With `dry_run` only the groups are counted.
pub fn dedup<C: GenericClient>(client: &mut C, dry_run: bool) -> Result<ReflectSummary> {
    let mut summary = ReflectSummary {
        mode: "dedup".into(),
        dry_run,
        ..Default::default()
    };
    let rows = client.query(
        r#"
        SELECT project_name, category, left(content, 80) AS head,
               array_agg(id ORDER BY id), array_agg(content ORDER BY id)
        FROM memory_chunks
        WHERE status = 'active'
        GROUP BY project_name, category, left(content, 80)
        HAVING COUNT(*) > 1
        LIMIT 100
        "#,
        &[],
    )?;
    for row in rows {
        let ids: Vec<i64> = row.get(3);
        let contents: Vec<String> = row.get(4);
        summary.examined += ids.len();
        if ids.len() < 2 {
            continue;
        }
        if dry_run {
            summary.acted += 1;
            continue;
        }
        // One new note holds the longest text; the duplicates are closed like
        // a supersede. No existing note changes its content.
        let text = crate::lifecycle::longest_text(contents.iter().map(String::as_str));
        match crate::lifecycle::consolidate(client, &ids, text, None) {
            Ok(_) => summary.acted += 1,
            Err(e) => {
                eprintln!("dedup: group {ids:?} not merged: {e}");
                summary.failed += 1;
            }
        }
    }
    Ok(summary)
}

/// Prompt for the contradiction check — answer YES/NO only.
pub const CONTRADICTION_PROMPT: &str =
    "Does this memory reverse or contradict an earlier decision? \
Consider if it says the opposite of what was previously chosen. Answer YES or NO only.";

/// Flag possible contradictions via the generation backend (opt-in, limited).
pub fn contradictions<C: GenericClient>(
    client: &mut C,
    backend: &GenerationBackend,
    dry_run: bool,
    limit: usize,
) -> Result<ReflectSummary> {
    llm::ensure_local_or_allowed(backend)?;
    let mut summary = ReflectSummary {
        mode: "contradictions".into(),
        dry_run,
        ..Default::default()
    };
    let rows = client.query(
        r#"
        SELECT id, content, category FROM memory_chunks
        WHERE status = 'active' AND category IN ('decision','pattern','preference')
        ORDER BY created_at DESC
        LIMIT $1::bigint
        "#,
        &[&(limit as i64)],
    )?;
    summary.examined = rows.len();
    for row in rows {
        let id: i64 = row.get(0);
        let content: String = row.get(1);
        let prompt = format!("{CONTRADICTION_PROMPT}\n\n{content}");
        if let Ok(answer) = llm::complete(backend, None, &prompt) {
            if is_contradiction(&answer) {
                summary.acted += 1;
                if !dry_run {
                    client.execute(
                        "UPDATE memory_chunks SET confidence = GREATEST(confidence - 0.15, 0) WHERE id = $1",
                        &[&id],
                    )?;
                }
            }
        }
    }
    Ok(summary)
}

/// Check if a YES/NO answer indicates a contradiction.
pub fn is_contradiction(answer: &str) -> bool {
    let upper = answer.trim().to_uppercase();
    upper.starts_with("YES") || upper.starts_with("Y") && upper.len() <= 5
}

/// Run all maintenance modes.
pub fn run_all<C: GenericClient>(
    client: &mut C,
    backend: Option<&GenerationBackend>,
    dry_run: bool,
    limit: usize,
) -> Result<Vec<ReflectSummary>> {
    let mut out = vec![dedup(client, dry_run)?, expire_stale(client, dry_run)?];
    if let Some(b) = backend {
        out.push(contradictions(client, b, dry_run, limit)?);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counts_show_failed_groups() {
        let s = ReflectSummary {
            acted: 2,
            failed: 1,
            ..Default::default()
        };
        assert_eq!(s.counts(), "acted=2 failed=1");
    }

    #[test]
    fn is_contradiction_detects_yes() {
        assert!(is_contradiction("YES"));
        assert!(is_contradiction("yes"));
        assert!(is_contradiction("Yes, it reverses the decision."));
        assert!(is_contradiction("  YES  "));
    }

    #[test]
    fn is_contradiction_rejects_no() {
        assert!(!is_contradiction("NO"));
        assert!(!is_contradiction("no"));
        assert!(!is_contradiction("No, this is consistent."));
        assert!(!is_contradiction(""));
    }

    #[test]
    fn is_contradiction_handles_short_y() {
        assert!(is_contradiction("Y"));
    }

    /// Connect to the database (`PG*` env). Skip when unreachable so offline
    /// `cargo test --lib` stays green. Mirrors the server.rs test helper.
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

    #[test]
    fn dedup_closes_the_validity_of_the_merged_duplicates() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping dedup_closes_the_validity_of_the_merged_duplicates: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let category_type =
            crate::extract_memory::category_column_type(&mut tx).expect("category_column_type");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let head = format!("{tag} the project store runs on PostgreSQL with pgvector, always");
        let mut seed = |tail: &str, from: &str| -> i64 {
            let from: chrono::DateTime<chrono::Utc> = from.parse().expect("instant");
            tx.query_one(
                &format!(
                    "INSERT INTO memory_chunks (source_type, content, category, confidence, status, valid_from)
                     VALUES ('manual', $1, 'decision'::text::{category_type}, 1.0, 'active', $2) RETURNING id"
                ),
                &[&format!("{head} {tail}"), &from],
            )
            .expect("seed duplicate")
            .get(0)
        };
        let first = seed("(first)", "2026-01-01T00:00:00Z");
        let second = seed("(second)", "2026-02-01T00:00:00Z");
        dedup(&mut tx, false).expect("dedup");

        let new_note: Option<i64> = tx
            .query_opt(
                "SELECT id FROM memory_chunks WHERE source_type = 'consolidation'
                 AND merged_from = $1",
                &[&vec![first, second]],
            )
            .expect("new note")
            .map(|r| r.get(0));
        for id in [first, second] {
            let row = tx
                .query_one(
                    "SELECT status, superseded_by, valid_to FROM memory_chunks WHERE id = $1",
                    &[&id],
                )
                .expect("duplicate row");
            assert_eq!(row.get::<_, String>(0), "superseded");
            let new_id = new_note.expect("a consolidation note exists");
            assert_eq!(row.get::<_, Option<i64>>(1), Some(new_id));
            let valid_to = row.get::<_, Option<chrono::DateTime<chrono::Utc>>>(2);
            assert!(
                valid_to.is_some_and(|t| t <= chrono::Utc::now()),
                "a merged duplicate needs an end of validity, not in the future"
            );
        }
        let pattern = format!("{tag}%");
        let valid_now: Vec<i64> = tx
            .query(
                &format!(
                    "SELECT mc.id FROM memory_chunks mc WHERE {} AND mc.content LIKE $1 ORDER BY mc.id",
                    crate::temporal::as_of_predicate("$2", "$3")
                ),
                &[
                    &pattern,
                    &Some(chrono::Utc::now()),
                    &None::<chrono::DateTime<chrono::Utc>>,
                ],
            )
            .expect("as-of query")
            .iter()
            .map(|r| r.get(0))
            .collect();
        assert_eq!(valid_now.len(), 1, "only the new merged note is valid now");
        assert!(!valid_now.contains(&first) && !valid_now.contains(&second));
    }

    fn seed_dup<C: GenericClient>(client: &mut C, content: &str) -> i64 {
        let category_type =
            crate::extract_memory::category_column_type(client).expect("category_column_type");
        client
            .query_one(
                &format!(
                    "INSERT INTO memory_chunks (source_type, content, category, confidence, status)
                     VALUES ('manual', $1, 'decision'::text::{category_type}, 1.0, 'active') RETURNING id"
                ),
                &[&content],
            )
            .expect("seed duplicate")
            .get(0)
    }

    fn contents<C: GenericClient>(client: &mut C, pattern: &str) -> Vec<(i64, String, String)> {
        client
            .query(
                "SELECT id, content, source_type FROM memory_chunks WHERE content LIKE $1 ORDER BY id",
                &[&pattern],
            )
            .expect("contents")
            .iter()
            .map(|r| (r.get(0), r.get(1), r.get(2)))
            .collect()
    }

    #[test]
    fn reflect_dedup_never_rewrites_a_note() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping reflect_dedup_never_rewrites_a_note: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let head = format!("{tag} the team agreed to deploy only on Tuesdays and never on Fridays");
        let short = format!("{head} ok");
        let long = format!("{head} because the weekend on-call is thin");
        let a = seed_dup(&mut tx, &short);
        let b = seed_dup(&mut tx, &long);
        dedup(&mut tx, false).expect("dedup");

        let rows = contents(&mut tx, &format!("{tag}%"));
        for (id, content, _) in &rows {
            if *id == a {
                assert_eq!(content, &short, "an existing note was rewritten");
            }
            if *id == b {
                assert_eq!(content, &long, "an existing note was rewritten");
            }
        }
        let new: Vec<_> = rows.iter().filter(|r| r.2 == "consolidation").collect();
        assert_eq!(new.len(), 1, "{rows:?}");
        assert_eq!(
            new[0].1, long,
            "the new note holds the longest text exactly once"
        );
        assert_eq!(new[0].1.matches(&head).count(), 1);
        let merged_from: Vec<i64> = tx
            .query_one(
                "SELECT merged_from FROM memory_chunks WHERE id = $1",
                &[&new[0].0],
            )
            .expect("merged_from")
            .get(0);
        assert_eq!(merged_from, vec![a, b]);
    }

    #[test]
    fn consolidate_twice_does_not_nest() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping consolidate_twice_does_not_nest: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let head = format!("{tag} backups run nightly at two and are kept for fourteen days");
        seed_dup(&mut tx, &format!("{head} (a)"));
        seed_dup(&mut tx, &format!("{head} (b)"));
        dedup(&mut tx, false).expect("first dedup");
        let after_first = contents(&mut tx, &format!("{tag}%"));
        assert_eq!(after_first.len(), 3);
        dedup(&mut tx, false).expect("second dedup");
        let after_second = contents(&mut tx, &format!("{tag}%"));
        assert_eq!(
            after_second, after_first,
            "the second run changed or added notes"
        );
        for (_, content, _) in &after_second {
            assert_eq!(content.matches(&head).count(), 1, "text nested: {content}");
        }
    }

    #[test]
    fn a_failing_group_does_not_stop_the_others() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping a_failing_group_does_not_stop_the_others: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let bad_head = format!("{tag} bad group the release train leaves every second Thursday");
        let good_head = format!("{tag} good group alerts go to the on-call channel first");
        let bad_a = seed_dup(&mut tx, &format!("{bad_head} (a)"));
        let bad_b = seed_dup(&mut tx, &format!("{bad_head} (b)"));
        let good_a = seed_dup(&mut tx, &format!("{good_head} (a)"));
        let good_b = seed_dup(&mut tx, &format!("{good_head} (b)"));
        // still 'active' for the group query, but already replaced: consolidate refuses it
        tx.execute(
            "UPDATE memory_chunks SET superseded_by = $2 WHERE id = $1",
            &[&bad_b, &bad_a],
        )
        .expect("mark replaced");
        let summary = dedup(&mut tx, false).expect("dedup goes on");
        assert_eq!(summary.failed, 1, "{summary:?}");
        let status = |tx: &mut postgres::Transaction, id: i64| -> String {
            tx.query_one("SELECT status FROM memory_chunks WHERE id = $1", &[&id])
                .expect("status")
                .get(0)
        };
        assert_eq!(status(&mut tx, bad_a), "active");
        assert_eq!(status(&mut tx, good_a), "superseded");
        assert_eq!(status(&mut tx, good_b), "superseded");
        let merged: i64 = tx
            .query_one(
                "SELECT count(*) FROM memory_chunks WHERE source_type = 'consolidation'
                 AND content LIKE $1",
                &[&format!("{tag}%")],
            )
            .expect("count")
            .get(0);
        assert_eq!(merged, 1, "only the good group was merged");
    }
}
