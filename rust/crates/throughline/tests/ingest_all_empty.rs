//! `ingest --source all` with every source list empty writes nothing.
//!
//! The real binary runs with a temporary, empty `HOME` and an environment
//! that holds only the database settings and `PATH`, so it finds no tool
//! folder, no agent memory and no assistant configuration of the machine it
//! runs on. A control run with one Markdown root then shows that the same
//! counts do move once a list is not empty, so the empty run cannot pass
//! because the variable was never read.
//!
//! This test has its own binary: it counts whole tables, and no other test
//! may write to them while it runs. Skipped when no PostgreSQL is reachable.

use postgres::NoTls;
use std::path::Path;
use std::process::{Command, Output};

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

/// Rows in the tables an ingest writes: `conversations`, `messages`,
/// `memory_chunks`, `ingestion_log` and `projects`.
fn counts(client: &mut postgres::Client) -> [i64; 5] {
    [
        "conversations",
        "messages",
        "memory_chunks",
        "ingestion_log",
        "projects",
    ]
    .map(|table| {
        client
            .query_one(&format!("SELECT count(*) FROM {table}"), &[])
            .expect("count rows")
            .get(0)
    })
}

/// `throughline ingest --source all` with `home` as `HOME`, the given
/// Markdown roots, and empty git and web lists.
fn ingest_all(home: &Path, markdown_roots: &str) -> Output {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_throughline"));
    cmd.args(["ingest", "--source", "all"]).env_clear();
    for key in [
        "PGHOST",
        "PGPORT",
        "PGUSER",
        "PGPASSWORD",
        "PGDATABASE",
        "USER",
        "USERNAME",
        "PATH",
    ] {
        if let Some(value) = std::env::var_os(key) {
            cmd.env(key, value);
        }
    }
    cmd.env("HOME", home)
        .env("THROUGHLINE_MARKDOWN_ROOTS", markdown_roots)
        .env("THROUGHLINE_GIT_ROOTS", "")
        .env("THROUGHLINE_WEB_URLS", "")
        .output()
        .expect("run ingest")
}

/// Removes what the control run wrote, also when an assertion fails.
struct Cleanup {
    root: String,
    file: String,
    project: String,
}

impl Drop for Cleanup {
    fn drop(&mut self) {
        let Some(mut client) = connect() else { return };
        let _ = client.execute(
            "DELETE FROM conversations WHERE source_tool = 'markdown' AND project_path = $1",
            &[&self.root],
        );
        let _ = client.execute(
            "DELETE FROM ingestion_log WHERE file_path = $1",
            &[&self.file],
        );
        let _ = client.execute("DELETE FROM projects WHERE name = $1", &[&self.project]);
    }
}

#[test]
fn ingest_all_with_empty_source_lists_changes_nothing() {
    let Some(mut client) = connect() else {
        eprintln!("skipping ingest_all_with_empty_source_lists_changes_nothing: no PostgreSQL");
        return;
    };
    let home = tempfile::tempdir().expect("temporary home");

    let before = counts(&mut client);
    let out = ingest_all(home.path(), "");
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(
        out.status.success(),
        "ingest failed: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    // `all` reached the Markdown adapter and the git runner, and fetched no
    // web page.
    assert!(stdout.contains("[markdown] files=0 "), "{stdout}");
    assert!(stdout.contains("[git] files=0 "), "{stdout}");
    assert!(!stdout.contains("[web]"), "{stdout}");
    assert_eq!(
        counts(&mut client),
        before,
        "conversations, messages, memory_chunks, ingestion_log, projects"
    );

    // Control: one root with one file, the same run otherwise.
    let project = format!("s6-notes-{}", uuid::Uuid::new_v4().simple());
    let root = home.path().join(&project);
    std::fs::create_dir(&root).expect("root");
    let file = root.join("harbour.md");
    std::fs::write(&file, "# Harbour\n\nThe tide table lives by the gate.\n").expect("file");
    let _cleanup = Cleanup {
        root: root.to_string_lossy().into_owned(),
        file: file.to_string_lossy().into_owned(),
        project,
    };
    let before = counts(&mut client);
    let out = ingest_all(home.path(), &root.to_string_lossy());
    assert!(
        out.status.success(),
        "control ingest failed: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    let after = counts(&mut client);
    let delta: Vec<i64> = after.iter().zip(before).map(|(a, b)| a - b).collect();
    assert_eq!(
        delta,
        [1, 1, 0, 1, 1],
        "the control root adds one conversation with one section, no note, \
         one ledger row and one project"
    );
}
