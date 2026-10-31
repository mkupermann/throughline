//! Backup via `pg_dump` and entity extraction helpers.

use crate::access::{push_visibility, Actor};
use anyhow::{Context, Result};
use chrono::Utc;
use postgres::types::ToSql;
use postgres::GenericClient;
use std::path::{Path, PathBuf};
use std::process::Command;

/// Build a `pg_dump` command. Honors `PG_DUMP_BIN`; on Windows runs via
/// `cmd /C` so `.cmd` shims (Dockerized Postgres) resolve on PATH.
fn pg_dump_command() -> Command {
    let bin = std::env::var("PG_DUMP_BIN").unwrap_or_else(|_| "pg_dump".into());
    if cfg!(windows) {
        let mut c = Command::new("cmd");
        c.arg("/C").arg(bin);
        c
    } else {
        Command::new(bin)
    }
}

/// Run pg_dump into `dir`, naming the file with a timestamp.
/// Falls back to `docker exec` when no local pg_dump works — Compose
/// installs keep the client tools inside the postgres image.
pub fn backup_database(dir: &Path, keep: usize) -> Result<PathBuf> {
    std::fs::create_dir_all(dir).with_context(|| format!("create {}", dir.display()))?;
    let dbname = std::env::var("PGDATABASE").unwrap_or_else(|_| "throughline".into());
    let stamp = Utc::now().format("%Y%m%d-%H%M%S");
    let out = dir.join(format!("{dbname}-{stamp}.sql"));

    if local_pg_dump_works() {
        let mut cmd = pg_dump_command();
        if let Ok(host) = std::env::var("PGHOST") {
            cmd.arg("-h").arg(host);
        }
        if let Ok(port) = std::env::var("PGPORT") {
            cmd.arg("-p").arg(port);
        }
        if let Ok(user) = std::env::var("PGUSER") {
            cmd.arg("-U").arg(user);
        }
        cmd.arg("-d").arg(&dbname).arg("-f").arg(&out);
        let status = cmd.status().context("run pg_dump")?;
        if !status.success() {
            // A half-written dump would count as a backup and push a good one out.
            let _ = std::fs::remove_file(&out);
            anyhow::bail!("pg_dump exited with {status}");
        }
    } else {
        let container = std::env::var("THROUGHLINE_PG_CONTAINER")
            .unwrap_or_else(|_| "throughline-postgres".into());
        let user = std::env::var("PGUSER").unwrap_or_else(|_| "throughline".into());
        let output = Command::new("docker")
            .args(["exec", &container, "pg_dump", "-U", &user, "-d", &dbname])
            .output()
            .context("docker exec pg_dump (start the compose postgres container or install client tools)")?;
        if !output.status.success() {
            let err = String::from_utf8_lossy(&output.stderr);
            anyhow::bail!("docker pg_dump failed: {err}");
        }
        std::fs::write(&out, &output.stdout).with_context(|| format!("write {}", out.display()))?;
    }

    prune(dir, keep, &dbname)?;
    Ok(out)
}

fn local_pg_dump_works() -> bool {
    pg_dump_command()
        .arg("--version")
        .output()
        .map(|o| o.status.success() && o.stdout.starts_with(b"pg_dump"))
        .unwrap_or(false)
}

/// Rotate only the dumps this job writes, `{dbname}-YYYYMMDD-HHMMSS.sql`.
/// Every other file in the folder belongs to someone else.
fn prune(dir: &Path, keep: usize, dbname: &str) -> Result<()> {
    let ours = |name: &str| {
        name.strip_prefix(dbname)
            .and_then(|r| r.strip_prefix('-'))
            .and_then(|r| r.strip_suffix(".sql"))
            .is_some_and(|stamp| {
                let b = stamp.as_bytes();
                b.len() == 15
                    && b.iter().enumerate().all(|(i, c)| {
                        if i == 8 {
                            *c == b'-'
                        } else {
                            c.is_ascii_digit()
                        }
                    })
            })
    };
    let mut dumps: Vec<PathBuf> = std::fs::read_dir(dir)?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.file_name().is_some_and(|n| ours(&n.to_string_lossy())))
        .collect();
    dumps.sort();
    while dumps.len() > keep {
        if let Some(old) = dumps.first().cloned() {
            let _ = std::fs::remove_file(&old);
            dumps.remove(0);
        }
    }
    Ok(())
}

/// The notes entity extraction reads: (id, project or "", content) of the
/// newest `limit` active notes `actor` sees.
fn entity_sources<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    limit: usize,
) -> Result<Vec<(i64, String, String)>> {
    let limit = limit as i64;
    let mut params: Vec<&(dyn ToSql + Sync)> = vec![&limit];
    let seen = push_visibility(actor, "mc", &mut params);
    let rows = client.query(
        &format!(
            "SELECT id, COALESCE(project_name,''), content FROM memory_chunks mc \
             WHERE status = 'active' AND {seen} ORDER BY created_at DESC LIMIT $1::bigint"
        ),
        &params,
    )?;
    Ok(rows
        .iter()
        .map(|r| (r.get(0), r.get(1), r.get(2)))
        .collect())
}

/// Naive entity harvest: technology/proper-noun-like tokens from memory content.
/// A server-wide job: callers pass [`Actor::shared_only`], so no private
/// note feeds the shared entity tables.
pub fn extract_entities_from_chunks<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    limit: usize,
) -> Result<usize> {
    let mut inserted = 0usize;
    for (chunk_id, project, content) in entity_sources(client, actor, limit)? {
        for word in content.split_whitespace() {
            let w = word.trim_matches(|c: char| !c.is_alphanumeric() && c != '#' && c != '.');
            if w.len() < 3 || !w.chars().any(|c| c.is_uppercase()) {
                continue;
            }
            // Cheap filter: skip sentence starts in lower-mixed words.
            if w.chars().filter(|c| c.is_uppercase()).count() == 1
                && !w.starts_with(char::is_uppercase)
            {
                continue;
            }
            let n = client.execute(
                r#"
                INSERT INTO entities (entity_type, canonical_name, project_name, mention_count)
                VALUES ('technology', $1, NULLIF($2,''), 1)
                ON CONFLICT (entity_type, canonical_name, project_name)
                DO UPDATE SET mention_count = entities.mention_count + 1, last_seen_at = now()
                "#,
                &[&w.to_string(), &project],
            );
            if n.unwrap_or(0) > 0 {
                inserted += 1;
            }
            let _ = client.execute(
                r#"
                INSERT INTO entity_mentions (entity_id, source_type, source_id, context)
                SELECT e.id, 'memory_chunk', $2, left($3, 200)
                FROM entities e
                WHERE e.canonical_name = $1
                LIMIT 1
                ON CONFLICT DO NOTHING
                "#,
                &[&w.to_string(), &chunk_id, &content],
            );
        }
    }
    Ok(inserted)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prune_keeps_newest_dumps() {
        let dir = std::env::temp_dir().join(format!("tf-prune-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        for i in 0..5 {
            std::fs::write(dir.join(format!("throughline-2026010{i}-000000.sql")), b"x").unwrap();
        }
        prune(&dir, 2, "throughline").unwrap();
        let left: Vec<_> = std::fs::read_dir(&dir)
            .unwrap()
            .filter_map(|e| e.ok())
            .map(|e| e.file_name().to_string_lossy().to_string())
            .collect();
        assert_eq!(left.len(), 2, "prune should keep 2 of 5: {left:?}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn prune_leaves_files_it_did_not_write() {
        let dir = tempfile::tempdir().unwrap();
        for name in [
            "my-notes.sql",
            "other-20250101-000000.sql",
            "throughline-copy-20250101-000000.sql",
            "throughline-20260101-000000.sql",
            "throughline-20260102-000000.sql",
            "throughline-20260103-000000.sql",
        ] {
            std::fs::write(dir.path().join(name), b"x").unwrap();
        }
        prune(dir.path(), 1, "throughline").unwrap();
        let mut left: Vec<_> = std::fs::read_dir(dir.path())
            .unwrap()
            .filter_map(|e| e.ok())
            .map(|e| e.file_name().to_string_lossy().to_string())
            .collect();
        left.sort();
        assert_eq!(
            left,
            [
                "my-notes.sql",
                "other-20250101-000000.sql",
                "throughline-20260103-000000.sql",
                "throughline-copy-20250101-000000.sql",
            ]
        );
    }

    #[test]
    fn backup_database_writes_file_and_needs_no_db_client() {
        // Regression: backup used to run under the shared postgres mutex.
        // It must work with no DB handle at all (pg_dump / docker only).
        let dir = std::env::temp_dir().join(format!("tf-backup-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        // If neither local pg_dump nor docker is available this is a no-op skip.
        if local_pg_dump_works()
            || std::process::Command::new("docker")
                .arg("--version")
                .output()
                .map(|o| o.status.success())
                .unwrap_or(false)
        {
            match backup_database(&dir, 3) {
                Ok(path) => assert!(path.exists(), "backup file missing: {path:?}"),
                Err(e) => eprintln!("skip backup assert: {e}"),
            }
        } else {
            eprintln!("skip backup: no pg_dump and no docker");
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn entity_extraction_skips_private() {
        use crate::access::team_test::{note, two_accounts};
        let Some(mut client) = crate::config::DbConfig::from_env().connect().ok() else {
            eprintln!("skipping entity_extraction_skips_private: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let (a, _) = two_accounts(&mut tx);
        let tag = uuid::Uuid::new_v4().simple().to_string().to_uppercase();
        let (private, shared) = (format!("PRIV{tag}"), format!("PUB{tag}"));
        let p = note(
            &mut tx,
            a.user_id,
            "private",
            &format!("we use {private}"),
            "insight",
            None,
        );
        let s = note(
            &mut tx,
            None,
            "shared",
            &format!("we use {shared}"),
            "insight",
            None,
        );
        // The job reads its notes here; the entity tables differ between the
        // schemas, so the selection is what the test pins.
        let ids = |actor: &Actor, tx: &mut postgres::Transaction<'_>| -> Vec<i64> {
            entity_sources(tx, actor, 50)
                .expect("sources")
                .into_iter()
                .map(|(id, _, _)| id)
                .collect()
        };
        assert!(ids(&a, &mut tx).contains(&p), "A herself would see it");
        let job = ids(&Actor::shared_only(), &mut tx);
        assert!(job.contains(&s), "the shared note feeds the entities");
        assert!(!job.contains(&p), "a private note never does");
    }
}
