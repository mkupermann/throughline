//! Team mode: roles, the actor behind a call, the auth mode and the
//! visibility filter for notes.
//!
//! `local` (the default) is the single-user behaviour: no login, every note
//! visible. `team` needs accounts. Notes are `shared` or `private` to their
//! owner, and server-wide jobs run as [`Actor::shared_only`].

use anyhow::{bail, Context, Result};
use postgres::GenericClient;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Role {
    Viewer,
    Editor,
    Admin,
}

impl Role {
    pub fn parse(value: &str) -> Result<Role> {
        match value.trim() {
            "viewer" => Ok(Role::Viewer),
            "editor" => Ok(Role::Editor),
            "admin" => Ok(Role::Admin),
            other => bail!("unknown role `{other}`: use viewer, editor or admin"),
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Role::Viewer => "viewer",
            Role::Editor => "editor",
            Role::Admin => "admin",
        }
    }
}

/// How the actor reached the server. `Job` marks a server-wide job (reflect,
/// entities, writeback, merging) that acts for no account.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Via {
    Local,
    Session,
    AgentKey,
    Job,
}

#[derive(Clone, Debug)]
pub struct Actor {
    pub user_id: Option<i64>,
    pub username: Option<String>,
    pub role: Role,
    pub via: Via,
}

impl Actor {
    /// Local mode: the one person at this machine, who sees every note.
    pub fn local() -> Actor {
        Actor {
            user_id: None,
            username: None,
            role: Role::Admin,
            via: Via::Local,
        }
    }

    /// Server-wide jobs in team mode: no account, shared notes only.
    pub fn shared_only() -> Actor {
        Actor {
            user_id: None,
            username: None,
            role: Role::Viewer,
            via: Via::Job,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AuthMode {
    Local,
    Team,
}

/// `THROUGHLINE_AUTH_MODE`. Unset or empty means `local`.
pub fn auth_mode_from(value: Option<&str>) -> Result<AuthMode> {
    match value.map(|v| v.trim().to_ascii_lowercase()).as_deref() {
        None | Some("") | Some("local") => Ok(AuthMode::Local),
        Some("team") => Ok(AuthMode::Team),
        Some(other) => bail!("THROUGHLINE_AUTH_MODE is `{other}`: use local or team"),
    }
}

/// Refuse to start when the mode does not fit the database or the URL.
/// `local` would show every account's private notes to anyone, so it needs
/// a database without accounts and private notes. `team` needs an enabled
/// admin and a public URL that keeps the session cookie off plain HTTP.
pub fn check_startup<C: GenericClient>(
    client: &mut C,
    mode: AuthMode,
    public_url: Option<&str>,
) -> Result<()> {
    match mode {
        AuthMode::Local => {
            let (accounts, private) = counts(client)?;
            if accounts > 0 || private > 0 {
                bail!(
                    "the database has {accounts} accounts and {private} private notes; \
                     start with THROUGHLINE_AUTH_MODE=team"
                );
            }
        }
        AuthMode::Team => {
            if !public_url.is_some_and(team_url_ok) {
                bail!(
                    "team mode needs THROUGHLINE_PUBLIC_URL with https \
                     (http only on localhost, 127.x.x.x or ::1)"
                );
            }
            let admins: i64 = client
                .query_one(
                    "SELECT count(*) FROM access_users WHERE role = 'admin' AND enabled",
                    &[],
                )
                .context(MIGRATE_FIRST)?
                .get(0);
            if admins == 0 {
                bail!(
                    "team mode needs an enabled admin: run `throughline account add <name> --role admin`"
                );
            }
        }
    }
    Ok(())
}

/// The `auth mode:` line of `doctor`.
pub fn doctor_line<C: GenericClient>(client: &mut C, mode: AuthMode) -> Result<String> {
    Ok(match mode {
        AuthMode::Local => "local".to_string(),
        AuthMode::Team => {
            let (accounts, private) = counts(client)?;
            format!("team ({accounts} accounts, {private} private notes)")
        }
    })
}

const MIGRATE_FIRST: &str = "read accounts (run `throughline migrate` first)";

/// Accounts (enabled or not) and private notes.
fn counts<C: GenericClient>(client: &mut C) -> Result<(i64, i64)> {
    let row = client
        .query_one(
            "SELECT (SELECT count(*) FROM access_users), \
                    (SELECT count(*) FROM memory_chunks WHERE visibility = 'private')",
            &[],
        )
        .context(MIGRATE_FIRST)?;
    Ok((row.get(0), row.get(1)))
}

/// `https`, or `http` on a loopback host (`localhost`, `127.0.0.0/8`, `::1`).
fn team_url_ok(url: &str) -> bool {
    let Some((scheme, rest)) = url.trim().split_once("://") else {
        return false;
    };
    let host = crate::server::host_of(rest.split(['/', '?', '#']).next().unwrap_or(""));
    if host.is_empty() {
        return false;
    }
    scheme.eq_ignore_ascii_case("https")
        || (scheme.eq_ignore_ascii_case("http")
            && (host == "localhost"
                || host
                    .parse::<std::net::IpAddr>()
                    .is_ok_and(|ip| ip.is_loopback())))
}

/// [`visibility_predicate_at`] with the owner bound as `$1`.
pub fn visibility_predicate(actor: &Actor, alias: &str) -> (String, Option<i64>) {
    visibility_predicate_at(actor, alias, 1)
}

/// SQL condition on the notes table `alias` that keeps what `actor` may see,
/// and the value the caller binds as `$n` when there is one. Local mode sees
/// everything, an account sees shared notes and its own, and jobs or anything
/// without an account see shared notes only.
pub fn visibility_predicate_at(actor: &Actor, alias: &str, n: usize) -> (String, Option<i64>) {
    match (actor.via, actor.user_id) {
        (Via::Local, _) => ("TRUE".to_string(), None),
        (Via::Session | Via::AgentKey, Some(id)) => (
            format!("({alias}.visibility = 'shared' OR {alias}.owner_id = ${n})"),
            Some(id),
        ),
        _ => (format!("({alias}.visibility = 'shared')"), None),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_db() -> Option<postgres::Client> {
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
        cfg.connect(postgres::NoTls).ok()
    }

    /// A test transaction that sees no accounts and no private notes.
    /// Repeatable read keeps rows other tests commit meanwhile out of the
    /// counts.
    fn without_accounts(client: &mut postgres::Client) -> postgres::Transaction<'_> {
        let mut tx = client
            .build_transaction()
            .isolation_level(postgres::IsolationLevel::RepeatableRead)
            .start()
            .expect("begin test tx");
        tx.batch_execute(
            "UPDATE memory_chunks SET owner_id = NULL, visibility = 'shared' \
                 WHERE owner_id IS NOT NULL OR visibility <> 'shared'; \
             DELETE FROM access_users;",
        )
        .expect("clear accounts");
        tx
    }

    fn add_user<C: GenericClient>(client: &mut C, role: &str, enabled: bool) -> i64 {
        client
            .query_one(
                "INSERT INTO access_users (username, display_name, password_hash, role, enabled) \
                 VALUES ($1, 'Test', 'not-a-hash', $2, $3) RETURNING id",
                &[&format!("t-{}", uuid::Uuid::new_v4()), &role, &enabled],
            )
            .expect("insert account")
            .get(0)
    }

    fn add_note<C: GenericClient>(client: &mut C, visibility: &str, owner: Option<i64>) -> i64 {
        let category_type = crate::extract_memory::category_column_type(client).expect("type");
        client
            .query_one(
                &format!(
                    "INSERT INTO memory_chunks (source_type, content, category, visibility, owner_id) \
                     VALUES ('manual', $1, 'insight'::text::{category_type}, $2, $3) RETURNING id"
                ),
                &[
                    &format!("team note {}", uuid::Uuid::new_v4()),
                    &visibility,
                    &owner,
                ],
            )
            .expect("insert note")
            .get(0)
    }

    #[test]
    fn auth_mode_parses_only_local_and_team() {
        for (value, mode) in [
            (None, AuthMode::Local),
            (Some(""), AuthMode::Local),
            (Some("local"), AuthMode::Local),
            (Some("team"), AuthMode::Team),
            (Some(" Team "), AuthMode::Team),
        ] {
            assert_eq!(auth_mode_from(value).unwrap(), mode, "{value:?}");
        }
        for value in ["teams", "off", "1", "admin"] {
            let err = auth_mode_from(Some(value)).unwrap_err().to_string();
            assert!(err.contains("THROUGHLINE_AUTH_MODE"), "{err}");
            assert!(err.contains("use local or team"), "{err}");
        }
        for role in [Role::Viewer, Role::Editor, Role::Admin] {
            assert_eq!(Role::parse(role.as_str()).unwrap(), role);
        }
        assert!(Role::parse("owner").is_err());
    }

    #[test]
    fn visibility_predicate_is_true_for_local_and_filters_for_users() {
        let user = |via, user_id| Actor {
            user_id,
            username: None,
            role: Role::Editor,
            via,
        };
        assert_eq!(
            visibility_predicate(&Actor::local(), "m"),
            ("TRUE".to_string(), None)
        );
        assert_eq!(
            visibility_predicate_at(&Actor::shared_only(), "m", 4),
            ("(m.visibility = 'shared')".to_string(), None)
        );
        assert_eq!(
            visibility_predicate_at(&user(Via::Session, Some(7)), "m", 3),
            (
                "(m.visibility = 'shared' OR m.owner_id = $3)".to_string(),
                Some(7)
            )
        );
        assert_eq!(
            visibility_predicate(&user(Via::AgentKey, Some(8)), "mc"),
            (
                "(mc.visibility = 'shared' OR mc.owner_id = $1)".to_string(),
                Some(8)
            )
        );
        // A session without an account sees shared notes, never everything.
        assert_eq!(
            visibility_predicate_at(&user(Via::Session, None), "m", 2),
            ("(m.visibility = 'shared')".to_string(), None)
        );

        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping visibility_predicate_is_true_for_local_and_filters_for_users: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let (a, b) = (
            add_user(&mut tx, "editor", true),
            add_user(&mut tx, "editor", true),
        );
        let notes = vec![
            add_note(&mut tx, "shared", None),
            add_note(&mut tx, "private", Some(a)),
            add_note(&mut tx, "private", Some(b)),
        ];
        let mut seen = |actor: &Actor| -> Vec<i64> {
            let (predicate, owner) = visibility_predicate_at(actor, "m", 2);
            let mut params: Vec<&(dyn postgres::types::ToSql + Sync)> = vec![&notes];
            if let Some(owner) = &owner {
                params.push(owner);
            }
            tx.query(
                &format!(
                    "SELECT m.id FROM memory_chunks m \
                     WHERE m.id = ANY($1) AND {predicate} ORDER BY m.id"
                ),
                &params,
            )
            .expect("filtered query")
            .iter()
            .map(|r| r.get(0))
            .collect()
        };
        assert_eq!(seen(&Actor::local()), notes);
        assert_eq!(seen(&Actor::shared_only()), vec![notes[0]]);
        assert_eq!(seen(&user(Via::Session, Some(a))), vec![notes[0], notes[1]]);
        assert_eq!(
            seen(&user(Via::AgentKey, Some(b))),
            vec![notes[0], notes[2]]
        );
    }

    #[test]
    fn startup_local_refuses_accounts_or_private_notes() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping startup_local_refuses_accounts_or_private_notes: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = without_accounts(&mut client);
        check_startup(&mut tx, AuthMode::Local, None).expect("nothing private: local starts");
        assert_eq!(doctor_line(&mut tx, AuthMode::Local).unwrap(), "local");

        let note = add_note(&mut tx, "private", None);
        let err = check_startup(&mut tx, AuthMode::Local, None)
            .unwrap_err()
            .to_string();
        assert!(err.contains("1 private notes"), "{err}");
        assert!(err.contains("THROUGHLINE_AUTH_MODE=team"), "{err}");
        tx.execute(
            "UPDATE memory_chunks SET visibility = 'shared' WHERE id = $1",
            &[&note],
        )
        .expect("share note");
        check_startup(&mut tx, AuthMode::Local, None).expect("shared notes are fine");

        // Even a disabled account stops local mode.
        add_user(&mut tx, "viewer", false);
        let err = check_startup(&mut tx, AuthMode::Local, None)
            .unwrap_err()
            .to_string();
        assert!(err.contains("1 accounts"), "{err}");
        assert!(err.contains("THROUGHLINE_AUTH_MODE=team"), "{err}");
    }

    #[test]
    fn startup_team_needs_admin_and_https() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping startup_team_needs_admin_and_https: no PostgreSQL reachable");
            return;
        };
        let mut tx = without_accounts(&mut client);
        let https = Some("https://memory.example.com");
        add_user(&mut tx, "editor", true);
        add_user(&mut tx, "admin", false);
        let err = check_startup(&mut tx, AuthMode::Team, https)
            .unwrap_err()
            .to_string();
        assert!(err.contains("throughline account add"), "{err}");
        assert!(err.contains("--role admin"), "{err}");

        let admin = add_user(&mut tx, "admin", true);
        check_startup(&mut tx, AuthMode::Team, https).expect("enabled admin and https");
        add_note(&mut tx, "private", Some(admin));
        assert_eq!(
            doctor_line(&mut tx, AuthMode::Team).unwrap(),
            "team (3 accounts, 1 private notes)"
        );

        for url in [
            "http://localhost:8787",
            "http://127.0.0.1:8787/",
            "http://127.4.5.6",
            "http://[::1]:8787",
            "HTTPS://Memory.Example.com/base",
        ] {
            check_startup(&mut tx, AuthMode::Team, Some(url))
                .unwrap_or_else(|e| panic!("{url}: {e}"));
        }
        for url in [
            None,
            Some(""),
            Some("memory.example.com"),
            Some("http://memory.example.com"),
            Some("http://localhost.example.com"),
            Some("http://192.168.1.5:8787"),
            Some("https://"),
            Some("ftp://localhost"),
        ] {
            let err = check_startup(&mut tx, AuthMode::Team, url)
                .expect_err("refused")
                .to_string();
            assert!(err.contains("THROUGHLINE_PUBLIC_URL"), "{url:?}: {err}");
        }
    }
}
