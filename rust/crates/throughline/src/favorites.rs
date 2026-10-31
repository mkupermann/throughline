//! Favorites: star conversations, messages, memory notes, projects and
//! imported AI configs.
//!
//! One row per starred item in `favorites`, keyed by `(kind, ref)` where
//! `ref` is the item identifier as text: the database id for conversations,
//! messages and memory, the project name for projects, the inventory hash
//! for imported configs.
//!
//! Stars belong to the account that set them (`user_id`); local mode has no
//! accounts and keeps every star with `user_id` NULL. A star on a memory
//! note is only listed while the note is visible to its owner. An account's
//! star has no time (`created_at` NULL): team mode keeps no activity times
//! per account. Local stars keep theirs.

use crate::access::{push_visibility, Actor, Via};
use postgres::types::ToSql;
use postgres::GenericClient;
use std::str::FromStr;

/// What kind of item a favorite points at.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FavKind {
    Conversation,
    Message,
    Memory,
    Project,
    Config,
}

impl FavKind {
    /// The `kind` value stored in `favorites` and used in the API.
    pub fn as_str(&self) -> &'static str {
        match self {
            FavKind::Conversation => "conversation",
            FavKind::Message => "message",
            FavKind::Memory => "memory",
            FavKind::Project => "project",
            FavKind::Config => "config",
        }
    }
}

impl FromStr for FavKind {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "conversation" => Ok(FavKind::Conversation),
            "message" => Ok(FavKind::Message),
            "memory" => Ok(FavKind::Memory),
            "project" => Ok(FavKind::Project),
            "config" => Ok(FavKind::Config),
            other => Err(format!("unknown kind '{other}'")),
        }
    }
}

/// Validate a favorite reference for `kind`. Empty is always an error.
/// Conversation, message and memory refs are numeric i64 ids, config refs
/// are 16 lowercase hex characters (inventory hash), project refs are a
/// non-empty name of at most 200 characters.
pub fn validate_ref(kind: FavKind, r: &str) -> Result<(), String> {
    if r.is_empty() {
        return Err("ref must not be empty".into());
    }
    match kind {
        FavKind::Conversation | FavKind::Message | FavKind::Memory => {
            if r.chars().all(|c| c.is_ascii_digit()) && r.parse::<i64>().is_ok() {
                Ok(())
            } else {
                Err(format!("ref '{r}' must be a numeric id"))
            }
        }
        FavKind::Config => {
            if r.len() == 16 && r.chars().all(|c| matches!(c, '0'..='9' | 'a'..='f')) {
                Ok(())
            } else {
                Err(format!("ref '{r}' must be 16 lowercase hex characters"))
            }
        }
        FavKind::Project => {
            if r.chars().count() <= 200 {
                Ok(())
            } else {
                Err(format!("ref '{r}' must be at most 200 characters"))
            }
        }
    }
}

/// One favorites row for JSON output.
#[derive(Debug, Clone, serde::Serialize)]
pub struct Favorite {
    pub kind: String,
    #[serde(rename = "ref")]
    pub ref_: String,
    /// RFC 3339 creation timestamp; none for an account's star.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub created_at: Option<String>,
}

/// The `created_at` of a new star of the account bound as `$n`: now for
/// local mode (no account), NULL for an account.
pub fn star_time_sql(n: usize) -> String {
    format!("CASE WHEN ${n}::bigint IS NULL THEN now() END")
}

/// The condition on the favorites alias `alias` that keeps the stars of
/// `actor`: the account's own rows, or the rows without an account (local
/// mode, jobs). Binds the account as the next parameter of `params`.
pub fn push_mine<'a>(
    actor: &'a Actor,
    alias: &str,
    params: &mut Vec<&'a (dyn ToSql + Sync)>,
) -> String {
    params.push(&actor.user_id);
    format!(
        "{alias}.user_id IS NOT DISTINCT FROM ${}::bigint",
        params.len()
    )
}

/// Insert a favorite of `actor`. A duplicate is a no-op (idempotent); the
/// conflict target is the per-account key of migration 014. With an
/// account, a memory note it does not see (missing or another account's
/// private note) is not starred: `Ok(false)`, the caller's "not found".
/// Local mode stores the star as before.
pub fn add<C: GenericClient>(
    c: &mut C,
    actor: &Actor,
    kind: FavKind,
    r: &str,
) -> anyhow::Result<bool> {
    if kind == FavKind::Memory && actor.via != Via::Local {
        let id: i64 = r.parse()?;
        let mut params: Vec<&(dyn ToSql + Sync)> = vec![&id];
        let seen = push_visibility(actor, "mc", &mut params);
        let sql = format!("SELECT 1 FROM memory_chunks mc WHERE mc.id = $1 AND {seen}");
        if c.query_opt(&sql, &params)?.is_none() {
            return Ok(false);
        }
    }
    c.execute(
        &format!(
            "INSERT INTO favorites (kind, ref, user_id, created_at) VALUES ($1, $2, $3, {}) \
             ON CONFLICT (kind, ref, (COALESCE(user_id, 0))) DO NOTHING",
            star_time_sql(3)
        ),
        &[&kind.as_str(), &r, &actor.user_id],
    )?;
    Ok(true)
}

/// Delete a favorite of `actor`; other accounts' stars stay. Returns true
/// when a row was removed.
pub fn remove<C: GenericClient>(
    c: &mut C,
    actor: &Actor,
    kind: FavKind,
    r: &str,
) -> anyhow::Result<bool> {
    let kind = kind.as_str();
    let mut params: Vec<&(dyn ToSql + Sync)> = vec![&kind, &r];
    let mine = push_mine(actor, "f", &mut params);
    let n = c.execute(
        &format!("DELETE FROM favorites f WHERE f.kind = $1 AND f.ref = $2 AND {mine}"),
        &params,
    )?;
    Ok(n > 0)
}

/// The favorites of `actor`, newest first: by time in local mode, by id for
/// an account's stars, which have no time. `kind` filters on the item kind
/// when given. A memory star is listed only while `actor` sees the note.
pub fn list<C: GenericClient>(
    c: &mut C,
    actor: &Actor,
    kind: Option<FavKind>,
) -> anyhow::Result<Vec<Favorite>> {
    let kind = kind.map(|k| k.as_str().to_string());
    let mut params: Vec<&(dyn ToSql + Sync)> = vec![&kind];
    let mine = push_mine(actor, "f", &mut params);
    let seen = push_visibility(actor, "mc", &mut params);
    // The CASE keeps project names and config hashes away from the cast.
    let rows = c.query(
        &format!(
            "SELECT f.kind, f.ref, f.created_at FROM favorites f \
             LEFT JOIN memory_chunks mc ON mc.id = CASE \
                 WHEN f.kind = 'memory' AND f.ref ~ '^[0-9]{{1,18}}$' THEN f.ref::bigint END \
             WHERE ($1::text IS NULL OR f.kind = $1) AND {mine} \
               AND (f.kind <> 'memory' OR {seen}) \
             ORDER BY f.created_at DESC NULLS LAST, f.id DESC"
        ),
        &params,
    )?;
    Ok(rows
        .into_iter()
        .map(|r| Favorite {
            kind: r.get(0),
            ref_: r.get(1),
            created_at: r
                .get::<_, Option<chrono::DateTime<chrono::Utc>>>(2)
                .map(|at| at.to_rfc3339()),
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_kind_accepts_all_five() {
        for (raw, want) in [
            ("conversation", FavKind::Conversation),
            ("message", FavKind::Message),
            ("memory", FavKind::Memory),
            ("project", FavKind::Project),
            ("config", FavKind::Config),
        ] {
            assert_eq!(raw.parse::<FavKind>().expect("known kind"), want);
        }
    }

    #[test]
    fn parse_kind_rejects_unknown() {
        for raw in ["", "nonsense", "Conversation", "conv"] {
            assert!(raw.parse::<FavKind>().is_err(), "'{raw}' must be rejected");
        }
    }

    #[test]
    fn validate_ref_numeric_ids_only() {
        for kind in [FavKind::Conversation, FavKind::Message, FavKind::Memory] {
            validate_ref(kind, "12345").expect("digits ok");
            assert!(validate_ref(kind, "").is_err(), "{kind:?}: empty");
            assert!(validate_ref(kind, "12a").is_err(), "{kind:?}: letters");
            assert!(validate_ref(kind, "-5").is_err(), "{kind:?}: sign");
        }
    }

    #[test]
    fn validate_ref_config_is_16_lowercase_hex() {
        validate_ref(FavKind::Config, "deadbeefdeadbeef").expect("16 hex ok");
        assert!(validate_ref(FavKind::Config, "").is_err(), "empty");
        assert!(
            validate_ref(FavKind::Config, "deadbeef").is_err(),
            "too short"
        );
        assert!(
            validate_ref(FavKind::Config, "DEADBEEFDEADBEEF").is_err(),
            "uppercase"
        );
    }

    #[test]
    fn validate_ref_project_nonempty_and_bounded() {
        validate_ref(FavKind::Project, "my-project").expect("name ok");
        assert!(validate_ref(FavKind::Project, "").is_err(), "empty");
        let long = "x".repeat(201);
        assert!(validate_ref(FavKind::Project, &long).is_err(), "too long");
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
    fn add_twice_yields_single_row() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping add_twice_yields_single_row: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let local = Actor::local();
        add(&mut tx, &local, FavKind::Conversation, "987654321098").expect("first add");
        add(&mut tx, &local, FavKind::Conversation, "987654321098").expect("second add");
        let n: i64 = tx
            .query_one(
                "SELECT COUNT(*) FROM favorites \
                 WHERE kind = 'conversation' AND ref = '987654321098'",
                &[],
            )
            .expect("count")
            .get(0);
        assert_eq!(n, 1, "idempotent add must leave exactly one row");
        // Drop tx → rollback, nothing left behind.
    }

    #[test]
    fn remove_reports_true_then_false() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping remove_reports_true_then_false: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let local = Actor::local();
        add(&mut tx, &local, FavKind::Message, "987654321099").expect("seed favorite");
        assert!(remove(&mut tx, &local, FavKind::Message, "987654321099").expect("first remove"));
        assert!(!remove(&mut tx, &local, FavKind::Message, "987654321099").expect("second remove"));
        // Drop tx → rollback, nothing left behind.
    }

    #[test]
    fn list_filters_by_kind() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping list_filters_by_kind: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let local = Actor::local();
        add(&mut tx, &local, FavKind::Memory, "987654321100").expect("memory favorite");
        add(&mut tx, &local, FavKind::Project, "tl-fav-list-proj").expect("project favorite");
        let mems = list(&mut tx, &local, Some(FavKind::Memory)).expect("memory list");
        assert!(
            mems.iter().any(|f| f.ref_ == "987654321100"),
            "memory entry listed"
        );
        assert!(
            !mems.iter().any(|f| f.ref_ == "tl-fav-list-proj"),
            "project hidden"
        );
        let all = list(&mut tx, &local, None).expect("full list");
        assert!(
            all.iter().any(|f| f.ref_ == "987654321100"),
            "memory in full list"
        );
        assert!(
            all.iter().any(|f| f.ref_ == "tl-fav-list-proj"),
            "project in full list"
        );
        // Drop tx → rollback, nothing left behind.
    }

    #[test]
    fn favorites_are_per_account_and_hide_private() {
        use crate::access::team_test::{note, two_accounts};
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping favorites_are_per_account_and_hide_private: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let (a, b) = two_accounts(&mut tx);
        let private = note(
            &mut tx,
            a.user_id,
            "private",
            "A's private note",
            "insight",
            None,
        );
        let shared = note(&mut tx, None, "shared", "a shared note", "insight", None);
        let (private, shared) = (private.to_string(), shared.to_string());
        // Sorted: the stars of one transaction share their created_at.
        let memory = |tx: &mut postgres::Transaction<'_>, actor: &Actor| -> Vec<String> {
            let mut refs: Vec<String> = list(tx, actor, Some(FavKind::Memory))
                .expect("list")
                .into_iter()
                .map(|f| f.ref_)
                .collect();
            refs.sort_by_key(|r| r.parse::<i64>().unwrap_or(0));
            refs
        };
        // A stars her private note and a shared one.
        assert!(add(&mut tx, &a, FavKind::Memory, &private).expect("star"));
        assert!(add(&mut tx, &a, FavKind::Memory, &shared).expect("star"));
        assert_eq!(memory(&mut tx, &a), vec![private.clone(), shared.clone()]);
        // B sees neither star, and cannot star A's private note.
        assert!(memory(&mut tx, &b).is_empty());
        assert!(!add(&mut tx, &b, FavKind::Memory, &private).expect("refused"));
        // A missing note looks the same as a private one.
        assert!(!add(&mut tx, &b, FavKind::Memory, "987654321177").expect("refused"));
        // B's own star on the shared note is B's; it changes nothing for A.
        assert!(add(&mut tx, &b, FavKind::Memory, &shared).expect("star"));
        assert_eq!(memory(&mut tx, &b), vec![shared.clone()]);
        assert_eq!(memory(&mut tx, &a).len(), 2);
        // B's unstar removes B's row only.
        assert!(remove(&mut tx, &b, FavKind::Memory, &shared).expect("unstar"));
        assert!(!remove(&mut tx, &b, FavKind::Memory, &private).expect("not B's"));
        assert_eq!(memory(&mut tx, &a), vec![private.clone(), shared.clone()]);
        assert!(memory(&mut tx, &b).is_empty());
        let rows: i64 = tx
            .query_one(
                "SELECT count(*) FROM favorites WHERE user_id = $1",
                &[&a.user_id],
            )
            .expect("count")
            .get(0);
        assert_eq!(rows, 2);
    }
}
