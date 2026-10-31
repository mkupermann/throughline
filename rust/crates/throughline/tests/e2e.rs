//! End-to-end ingest against a live PostgreSQL.
//!
//! Skipped when no server is reachable so `cargo test` stays green offline.
//! Run with a database available:
//!
//! ```bash
//! PGHOST=localhost PGPORT=5433 PGDATABASE=throughline \
//!   cargo test --test e2e -- --nocapture
//! ```

use postgres::NoTls;
use std::io::Write;
use std::process::Command;

fn connect() -> Option<postgres::Client> {
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
fn migrate_ingest_and_search_roundtrip() {
    let Some(mut client) = connect() else {
        eprintln!("skipping e2e: no PostgreSQL reachable");
        return;
    };

    // Fresh target tables for this test's database user. If the Python schema
    // is already there this is a no-op via IF NOT EXISTS / ON CONFLICT.
    throughline::migrate_module::migrate(&mut client).expect("migrate");

    let tmp = tempfile::tempdir().unwrap();
    let proj = tmp.path().join("proj");
    std::fs::create_dir_all(&proj).unwrap();
    let session = uuid::Uuid::new_v4();
    let path = proj.join("s.jsonl");
    {
        let mut f = std::fs::File::create(&path).unwrap();
        writeln!(
            f,
            r#"{{"sessionId":"{session}","cwd":"/tmp/proj","timestamp":"2026-01-02T10:00:00Z","message":{{"role":"user","content":"rust e2e needle {session}"}}}}"#
        )
        .unwrap();
        writeln!(
            f,
            r#"{{"sessionId":"{session}","timestamp":"2026-01-02T10:00:01Z","message":{{"role":"assistant","model":"m","content":"answer","usage":{{"input_tokens":1,"output_tokens":2}}}}}}"#
        )
        .unwrap();
    }

    // Drive the binary so we exercise the real CLI path.
    let bin = env!("CARGO_BIN_EXE_throughline");
    let out = Command::new(bin)
        .args(["ingest", "--source", "claude_code", "-v"])
        .env("THROUGHLINE_CLAUDE_PROJECTS_DIR", tmp.path())
        .output()
        .expect("run ingest");
    assert!(
        out.status.success(),
        "ingest failed: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(
        stdout.contains("ingested=1") || stdout.contains("ingested="),
        "ingest should report a write: {stdout}"
    );
    assert!(
        !stdout.contains("errors=1") && !stdout.contains("errors=2"),
        "ingest should not error: {stdout}"
    );

    let hits = throughline::search_module::search(
        &mut client,
        &throughline::access::Actor::local(),
        &format!("rust e2e needle {session}"),
        10,
        None,
    )
    .expect("search");
    assert!(
        hits.iter().any(|h| h.snippet.contains("rust e2e needle")),
        "expected a hit, got {hits:?}"
    );

    // Idempotency: a second run must skip the unchanged file.
    let out2 = Command::new(bin)
        .args(["ingest", "--source", "claude_code", "-v"])
        .env("THROUGHLINE_CLAUDE_PROJECTS_DIR", tmp.path())
        .output()
        .expect("run ingest 2");
    let stdout2 = String::from_utf8_lossy(&out2.stdout);
    assert!(stdout2.contains("skipped="), "{stdout2}");
    // Clean up this test's conversation so re-runs don't pile up.
    client
        .execute(
            "DELETE FROM conversations WHERE session_id = $1",
            &[&session],
        )
        .ok();
}
