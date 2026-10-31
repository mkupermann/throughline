//! Favorites: star conversations, messages, memory notes, projects and
//! imported AI configs.
//!
//! One row per starred item in `favorites`, keyed by `(kind, ref)` where
//! `ref` is the item identifier as text: the database id for conversations,
//! messages and memory, the project name for projects, the inventory hash
//! for imported configs.

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
    /// RFC 3339 creation timestamp.
    pub created_at: String,
}

/// Insert a favorite. A duplicate `(kind, ref)` is a no-op (idempotent).
/// The conflict target is the per-account key of migration 014; local mode
/// stars without an account (`user_id` NULL).
pub fn add<C: GenericClient>(c: &mut C, kind: FavKind, r: &str) -> anyhow::Result<()> {
    c.execute(
        "INSERT INTO favorites (kind, ref) VALUES ($1, $2) \
         ON CONFLICT (kind, ref, (COALESCE(user_id, 0))) DO NOTHING",
        &[&kind.as_str(), &r],
    )?;
    Ok(())
}

/// Delete a favorite. Returns true when a row was removed.
pub fn remove<C: GenericClient>(c: &mut C, kind: FavKind, r: &str) -> anyhow::Result<bool> {
    let n = c.execute(
        "DELETE FROM favorites WHERE kind = $1 AND ref = $2",
        &[&kind.as_str(), &r],
    )?;
    Ok(n > 0)
}

/// All favorites, newest first. `kind` filters on the item kind when given.
pub fn list<C: GenericClient>(c: &mut C, kind: Option<FavKind>) -> anyhow::Result<Vec<Favorite>> {
    let kind = kind.map(|k| k.as_str().to_string());
    let rows = c.query(
        "SELECT kind, ref, created_at FROM favorites \
         WHERE ($1::text IS NULL OR kind = $1) \
         ORDER BY created_at DESC",
        &[&kind],
    )?;
    Ok(rows
        .into_iter()
        .map(|r| Favorite {
            kind: r.get(0),
            ref_: r.get(1),
            created_at: r.get::<_, chrono::DateTime<chrono::Utc>>(2).to_rfc3339(),
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
        add(&mut tx, FavKind::Conversation, "987654321098").expect("first add");
        add(&mut tx, FavKind::Conversation, "987654321098").expect("second add");
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
        add(&mut tx, FavKind::Message, "987654321099").expect("seed favorite");
        assert!(remove(&mut tx, FavKind::Message, "987654321099").expect("first remove"));
        assert!(!remove(&mut tx, FavKind::Message, "987654321099").expect("second remove"));
        // Drop tx → rollback, nothing left behind.
    }

    #[test]
    fn list_filters_by_kind() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping list_filters_by_kind: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        add(&mut tx, FavKind::Memory, "987654321100").expect("memory favorite");
        add(&mut tx, FavKind::Project, "tl-fav-list-proj").expect("project favorite");
        let mems = list(&mut tx, Some(FavKind::Memory)).expect("memory list");
        assert!(
            mems.iter().any(|f| f.ref_ == "987654321100"),
            "memory entry listed"
        );
        assert!(
            !mems.iter().any(|f| f.ref_ == "tl-fav-list-proj"),
            "project hidden"
        );
        let all = list(&mut tx, None).expect("full list");
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
}
