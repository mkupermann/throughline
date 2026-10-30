//! Memory lifecycle: which notes agents actually use.
//!
//! `record_use` counts one use per note when a note is handed to an agent or
//! to the CLI. The UI reads notes too, but that does not count.

use postgres::GenericClient;

/// Count one use for each note in `ids`. A repeated id counts once. Only
/// `access_count` and `last_accessed` change. Returns the rows updated.
pub fn record_use<C: GenericClient>(client: &mut C, ids: &[i64]) -> anyhow::Result<u64> {
    let mut ids = ids.to_vec();
    ids.sort_unstable();
    ids.dedup();
    if ids.is_empty() {
        return Ok(0);
    }
    Ok(client.execute(
        "UPDATE memory_chunks \
         SET access_count = COALESCE(access_count, 0) + 1, last_accessed = now() \
         WHERE id = ANY($1)",
        &[&ids],
    )?)
}

/// How strong a note is: it fades with the days since its last use and
/// holds longer the more it was used. Pinned notes and decisions fade four
/// times slower. Between 0.05 and 1.0.
///
/// `half_life = 30 * (1 + ln(1 + access_count))` days, times 4 if pinned or
/// a decision; `strength = 0.05 + 0.95 * 2^(-days / half_life)`.
pub fn strength(days_since_use: f64, access_count: i64, pinned_or_decision: bool) -> f64 {
    let mut half_life = 30.0 * (1.0 + (1.0 + access_count.max(0) as f64).ln());
    if pinned_or_decision {
        half_life *= 4.0;
    }
    let days = days_since_use.max(0.0);
    (0.05 + 0.95 * (-days / half_life).exp2()).clamp(0.05, 1.0)
}

/// A read path must not fail because of a counter: report and go on.
pub fn record_use_or_log<C: GenericClient>(client: &mut C, ids: &[i64]) {
    if let Err(e) = record_use(client, ids) {
        eprintln!("could not count note use: {e:#}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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

    fn seed_note<C: GenericClient>(client: &mut C, content: &str) -> i64 {
        let category_type =
            crate::extract_memory::category_column_type(client).expect("category_column_type");
        client
            .query_one(
                &format!(
                    "INSERT INTO memory_chunks (source_type, content, category, confidence, status)
                     VALUES ('manual', $1, 'decision'::text::{category_type}, 1.0, 'active')
                     RETURNING id"
                ),
                &[&content],
            )
            .expect("seed note")
            .get(0)
    }

    fn count<C: GenericClient>(client: &mut C, id: i64) -> (i32, bool) {
        let row = client
            .query_one(
                "SELECT COALESCE(access_count, 0), last_accessed IS NOT NULL \
                 FROM memory_chunks WHERE id = $1",
                &[&id],
            )
            .expect("row");
        (row.get(0), row.get(1))
    }

    #[test]
    fn strength_golden_values() {
        assert!((strength(0.0, 0, false) - 1.0).abs() < 1e-12);
        assert!((strength(30.0, 0, false) - 0.525).abs() < 1e-12);
        // 3 uses: half-life 30 * (1 + ln 4)
        let hl = 30.0 * (1.0 + 4.0_f64.ln());
        assert!((strength(hl, 3, false) - 0.525).abs() < 1e-12);
        // pinned: half-life 120
        assert!((strength(120.0, 0, true) - 0.525).abs() < 1e-12);
    }

    #[test]
    fn strength_halves_after_one_half_life() {
        let s = strength(30.0, 0, false);
        assert!((s - (0.05 + 0.95 * 0.5)).abs() < 1e-9, "{s}");
    }

    #[test]
    fn strength_reinforced_by_use() {
        assert!(strength(30.0, 10, false) > strength(30.0, 0, false));
        assert!(strength(30.0, 10, false) > strength(30.0, 1, false));
    }

    #[test]
    fn strength_floor() {
        assert!((strength(10_000.0, 0, false) - 0.05).abs() < 1e-9);
    }

    #[test]
    fn strength_pinned_decays_slower() {
        assert!(strength(60.0, 0, true) > strength(60.0, 0, false));
    }

    #[test]
    fn strength_is_capped() {
        assert_eq!(strength(-5.0, 0, false), 1.0);
        assert!(strength(0.0, 100, true) <= 1.0);
    }

    #[test]
    fn record_use_counts_each_id_once() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping record_use_counts_each_id_once: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let a = seed_note(&mut tx, &format!("use count note A {tag}"));
        let b = seed_note(&mut tx, &format!("use count note B {tag}"));
        let c = seed_note(&mut tx, &format!("use count note C {tag}"));
        assert_eq!(count(&mut tx, a), (0, false));
        assert_eq!(record_use(&mut tx, &[a, a, b]).expect("record"), 2);
        assert_eq!(count(&mut tx, a), (1, true));
        assert_eq!(count(&mut tx, b), (1, true));
        assert_eq!(count(&mut tx, c), (0, false));
        assert_eq!(record_use(&mut tx, &[a]).expect("record"), 1);
        assert_eq!(count(&mut tx, a).0, 2);
        assert_eq!(record_use(&mut tx, &[]).expect("empty"), 0);
        assert_eq!(count(&mut tx, a).0, 2);
    }

    #[test]
    fn record_use_leaves_other_columns() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping record_use_leaves_other_columns: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let content = format!("use count untouched {tag}");
        let a = seed_note(&mut tx, &content);
        tx.execute(
            "UPDATE memory_chunks SET updated_at = '2026-01-01T00:00:00Z' WHERE id = $1",
            &[&a],
        )
        .expect("pin updated_at");
        let before = tx
            .query_one(
                "SELECT updated_at, content, status, confidence::float8 \
                 FROM memory_chunks WHERE id = $1",
                &[&a],
            )
            .expect("before");
        let before: (chrono::DateTime<chrono::Utc>, String, String, f64) =
            (before.get(0), before.get(1), before.get(2), before.get(3));
        assert_eq!(record_use(&mut tx, &[a]).expect("record"), 1);
        let after = tx
            .query_one(
                "SELECT updated_at, content, status, confidence::float8 \
                 FROM memory_chunks WHERE id = $1",
                &[&a],
            )
            .expect("after");
        let after: (chrono::DateTime<chrono::Utc>, String, String, f64) =
            (after.get(0), after.get(1), after.get(2), after.get(3));
        assert_eq!(before, after);
        assert_eq!(after.1, content);
        assert_eq!(count(&mut tx, a), (1, true));
    }
}
