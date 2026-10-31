//! `throughline account`: team-mode accounts, their passwords and agent keys.
//!
//! `add`, `set_role`, `disable`, `set_password`, `new_agent_key` and
//! `revoke_agent_key` run in a transaction of their own (a savepoint when
//! the caller already has one); `enable` and `unlock` are single
//! statements. Passwords are hashed before any transaction starts, so
//! hashing never holds a database lock. Disabling an account ends its
//! sessions; its agent keys stay in place but stop working, because the
//! key check (`access::agent_key_actor`) requires an enabled account.

use std::path::Path;

use anyhow::{anyhow, bail, Context, Result};
use postgres::GenericClient;

use crate::access::{self, Role};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Account {
    pub username: String,
    pub display_name: String,
    pub role: Role,
    pub enabled: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AgentKeyEntry {
    pub id: i64,
    pub username: String,
    pub label: Option<String>,
}

/// `^[a-z0-9][a-z0-9._@-]{0,119}$`, the schema's CHECK. Upper case is
/// refused, not folded, so the name stored is the name typed.
pub fn validate_username(name: &str) -> Result<()> {
    let allowed = |c: char| c.is_ascii_lowercase() || c.is_ascii_digit();
    let mut chars = name.chars();
    let ok = name.len() <= 120
        && chars.next().is_some_and(allowed)
        && chars.all(|c| allowed(c) || matches!(c, '.' | '_' | '@' | '-'));
    if !ok {
        bail!(
            "account name {name:?} is not allowed: use 1 to 120 characters from \
             a-z, 0-9, `.`, `_`, `@` and `-` (lower case), starting with a letter or digit"
        );
    }
    Ok(())
}

/// A display name or key label: 1 to 200 characters, no control characters.
fn checked_text<'a>(what: &str, value: &'a str) -> Result<&'a str> {
    let count = value.chars().count();
    if !(1..=200).contains(&count) || value.chars().any(char::is_control) {
        bail!("the {what} needs 1 to 200 characters and no control characters");
    }
    Ok(value)
}

pub fn exists<C: GenericClient>(client: &mut C, username: &str) -> Result<bool> {
    Ok(client
        .query_opt(
            "SELECT 1 FROM access_users WHERE username = $1",
            &[&username],
        )
        .context("read accounts (run `throughline migrate` first)")?
        .is_some())
}

fn account_id<C: GenericClient>(client: &mut C, username: &str) -> Result<i64> {
    client
        .query_opt(
            "SELECT id FROM access_users WHERE username = $1",
            &[&username],
        )
        .context("read accounts (run `throughline migrate` first)")?
        .map(|row| row.get(0))
        .ok_or_else(|| anyhow!("no account named `{username}`"))
}

/// A database error without its DETAIL line, which can repeat the row
/// (and with it a password hash or key hash).
fn without_detail(what: &str, err: postgres::Error) -> anyhow::Error {
    match err.as_db_error() {
        Some(db) => anyhow!("{what}: {}", db.message()),
        None => anyhow!("{what}: {err}"),
    }
}

/// Creates an account and returns its id. The display name defaults to the
/// account name.
pub fn add<C: GenericClient>(
    client: &mut C,
    username: &str,
    display_name: Option<&str>,
    role: Role,
    password: &str,
) -> Result<i64> {
    validate_username(username)?;
    let display_name = match display_name {
        Some(text) => checked_text("display name", text)?,
        None => username,
    };
    let hash = access::hash_password(password)?;
    let mut tx = client.transaction()?;
    let row = tx
        .query_opt(
            "INSERT INTO access_users (username, display_name, password_hash, role) \
             VALUES ($1, $2, $3, $4) ON CONFLICT (username) DO NOTHING RETURNING id",
            &[&username, &display_name, &hash, &role.as_str()],
        )
        .map_err(|e| without_detail("store the account", e))?;
    let Some(row) = row else {
        bail!("account `{username}` already exists: `throughline account password` sets a new password");
    };
    tx.commit()?;
    Ok(row.get(0))
}

pub fn list<C: GenericClient>(client: &mut C) -> Result<Vec<Account>> {
    client
        .query(
            "SELECT username, display_name, role, enabled FROM access_users ORDER BY username",
            &[],
        )
        .context("read accounts (run `throughline migrate` first)")?
        .iter()
        .map(|row| {
            Ok(Account {
                username: row.get(0),
                display_name: row.get(1),
                role: Role::parse(row.get(2))?,
                enabled: row.get(3),
            })
        })
        .collect()
}

/// Locks every enabled admin and refuses when `id` is the only one. Two
/// concurrent demotions wait for each other here, so they cannot both
/// pass.
fn keep_an_admin<C: GenericClient>(client: &mut C, id: i64, username: &str) -> Result<()> {
    let admins: Vec<i64> = client
        .query(
            "SELECT id FROM access_users WHERE role = 'admin' AND enabled ORDER BY id FOR UPDATE",
            &[],
        )?
        .iter()
        .map(|row| row.get(0))
        .collect();
    if admins == [id] {
        bail!(
            "`{username}` is the last enabled admin, and the last enabled admin \
             cannot be demoted or disabled: make another account admin first"
        );
    }
    Ok(())
}

fn end_sessions<C: GenericClient>(client: &mut C, id: i64) -> Result<u64> {
    access::end_all_sessions(client, id)
}

/// Sets the role and ends the account's sessions; returns how many ended.
pub fn set_role<C: GenericClient>(client: &mut C, username: &str, role: Role) -> Result<u64> {
    let mut tx = client.transaction()?;
    let id = account_id(&mut tx, username)?;
    if role != Role::Admin {
        keep_an_admin(&mut tx, id, username)?;
    }
    tx.execute(
        "UPDATE access_users SET role = $2 WHERE id = $1",
        &[&id, &role.as_str()],
    )?;
    let ended = end_sessions(&mut tx, id)?;
    tx.commit()?;
    Ok(ended)
}

pub fn enable<C: GenericClient>(client: &mut C, username: &str) -> Result<()> {
    let updated = client.execute(
        "UPDATE access_users SET enabled = true WHERE username = $1",
        &[&username],
    )?;
    if updated == 0 {
        bail!("no account named `{username}`");
    }
    Ok(())
}

/// Disables the account and ends its sessions; returns how many ended.
pub fn disable<C: GenericClient>(client: &mut C, username: &str) -> Result<u64> {
    let mut tx = client.transaction()?;
    let id = account_id(&mut tx, username)?;
    keep_an_admin(&mut tx, id, username)?;
    tx.execute(
        "UPDATE access_users SET enabled = false WHERE id = $1",
        &[&id],
    )?;
    let ended = end_sessions(&mut tx, id)?;
    tx.commit()?;
    Ok(ended)
}

/// Clears the account's failed sign-ins; `false` when there were none.
pub fn unlock<C: GenericClient>(client: &mut C, username: &str) -> Result<bool> {
    account_id(client, username)?;
    let removed = client.execute(
        "DELETE FROM access_attempts WHERE key = $1",
        &[&access::attempt_key(username)],
    )?;
    Ok(removed > 0)
}

/// Sets a new password and ends the account's sessions; returns how many
/// ended.
pub fn set_password<C: GenericClient>(
    client: &mut C,
    username: &str,
    password: &str,
) -> Result<u64> {
    let hash = access::hash_password(password)?;
    let mut tx = client.transaction()?;
    let id = account_id(&mut tx, username)?;
    tx.execute(
        "UPDATE access_users SET password_hash = $2 WHERE id = $1",
        &[&id, &hash],
    )
    .map_err(|e| without_detail("store the password", e))?;
    let ended = end_sessions(&mut tx, id)?;
    tx.commit()?;
    Ok(ended)
}

/// A new agent key for the account: (id, key). Only the key's SHA-256 is
/// stored; the caller shows the key once.
pub fn new_agent_key<C: GenericClient>(
    client: &mut C,
    username: &str,
    label: Option<&str>,
) -> Result<(i64, String)> {
    let label = label.map(|l| checked_text("label", l)).transpose()?;
    let key = crate::agent_key::generate();
    let mut tx = client.transaction()?;
    let user = account_id(&mut tx, username)?;
    let id = tx
        .query_one(
            "INSERT INTO access_agent_keys (user_id, key_hash, label) \
             VALUES ($1, $2, $3) RETURNING id",
            &[&user, &access::token_hash(&key), &label],
        )
        .map_err(|e| without_detail("store the agent key", e))?
        .get(0);
    tx.commit()?;
    Ok((id, key))
}

pub fn revoke_agent_key<C: GenericClient>(client: &mut C, username: &str, id: i64) -> Result<()> {
    let mut tx = client.transaction()?;
    let user = account_id(&mut tx, username)?;
    let revoked = tx.execute(
        "UPDATE access_agent_keys SET revoked_at = now() \
         WHERE id = $1 AND user_id = $2 AND revoked_at IS NULL",
        &[&id, &user],
    )?;
    if revoked == 0 {
        bail!("account `{username}` has no active agent key {id}");
    }
    tx.commit()?;
    Ok(())
}

/// Keys not revoked, by account name and id.
pub fn active_agent_keys<C: GenericClient>(client: &mut C) -> Result<Vec<AgentKeyEntry>> {
    Ok(client
        .query(
            "SELECT k.id, u.username, k.label FROM access_agent_keys k \
             JOIN access_users u ON u.id = k.user_id \
             WHERE k.revoked_at IS NULL ORDER BY u.username, k.id",
            &[],
        )?
        .iter()
        .map(|row| AgentKeyEntry {
            id: row.get(0),
            username: row.get(1),
            label: row.get(2),
        })
        .collect())
}

/// The password from `--password-file`, or typed at the terminal (twice
/// when `confirm`).
pub fn read_password(file: Option<&Path>, confirm: bool) -> Result<String> {
    match file {
        Some(path) => read_password_file(path),
        None => read_password_typed(confirm),
    }
}

/// The file's text without trailing line breaks. On Unix the file must have
/// no group or other permission bits (0600, 0400 and 0700 are fine), as the
/// Python command required. The mode is read from the opened file, so the
/// file checked is the file read.
pub fn read_password_file(path: &Path) -> Result<String> {
    use std::io::Read;
    let context = || format!("read the password file {}", path.display());
    let mut file = std::fs::File::open(path).with_context(context)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = file.metadata().with_context(context)?.permissions().mode();
        if mode & 0o077 != 0 {
            bail!(
                "the password file {} must be readable only by its owner (chmod 600)",
                path.display()
            );
        }
    }
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes).with_context(context)?;
    let text =
        String::from_utf8(bytes).map_err(|_| anyhow!("the password file is not UTF-8 text"))?;
    Ok(text.trim_end_matches(['\r', '\n']).to_string())
}

fn read_password_typed(confirm: bool) -> Result<String> {
    let label = "Password (15 to 128 characters): ";
    match tty::read_hidden(label)? {
        Some(first) => {
            if confirm
                && tty::read_hidden("Repeat the password: ")?.as_deref() != Some(first.as_str())
            {
                bail!("the passwords do not match");
            }
            Ok(first)
        }
        None => {
            eprintln!(
                "No terminal: reading the password from standard input, not hidden \
                 (use --password-file in scripts)."
            );
            let mut line = String::new();
            std::io::stdin()
                .read_line(&mut line)
                .context("read the password from standard input")?;
            Ok(line.trim_end_matches(['\r', '\n']).to_string())
        }
    }
}

#[cfg(unix)]
mod tty {
    use std::fs::File;
    use std::io::{BufRead, BufReader, Write};
    use std::process::{Command, Stdio};

    use anyhow::{anyhow, bail, Context, Result};

    /// While it lives, the terminal neither shows typing nor turns Ctrl-C
    /// into a signal. Dropping it restores the state saved with `stty -g`,
    /// on errors too.
    struct Hidden {
        tty: File,
        saved: String,
    }

    impl Hidden {
        fn new(tty: &File) -> Result<Hidden> {
            let failed =
                || anyhow!("cannot hide the typed password (stty failed): use --password-file");
            let saved = stty(tty, &["-g"]).ok_or_else(failed)?;
            let hidden = Hidden {
                tty: tty.try_clone().context("open the terminal")?,
                saved: saved.trim().to_string(),
            };
            stty(&hidden.tty, &["-echo", "-isig"]).ok_or_else(failed)?;
            Ok(hidden)
        }
    }

    impl Drop for Hidden {
        fn drop(&mut self) {
            stty(&self.tty, &[self.saved.as_str()]);
        }
    }

    /// Runs `stty` on the terminal and returns its output, or `None` when
    /// it fails.
    fn stty(tty: &File, args: &[&str]) -> Option<String> {
        let output = Command::new("stty")
            .args(args)
            .stdin(tty.try_clone().ok()?)
            .stderr(Stdio::null())
            .output()
            .ok()?;
        output
            .status
            .success()
            .then(|| String::from_utf8_lossy(&output.stdout).into_owned())
    }

    /// One line typed at the controlling terminal without echo, or `None`
    /// when the process has no terminal. Echo goes off before the prompt
    /// appears, and Ctrl-C (a 0x03 byte in the line) cancels.
    pub fn read_hidden(prompt: &str) -> Result<Option<String>> {
        let Ok(mut tty) = File::options().read(true).write(true).open("/dev/tty") else {
            return Ok(None);
        };
        let hidden = Hidden::new(&tty)?;
        write!(tty, "{prompt}")?;
        tty.flush()?;
        let mut line = String::new();
        let read = BufReader::new(&tty).read_line(&mut line);
        writeln!(tty)?;
        drop(hidden);
        if read.context("read the password from the terminal")? == 0 {
            bail!("no password entered");
        }
        if line.contains('\x03') {
            bail!("password entry cancelled");
        }
        Ok(Some(line.trim_end_matches(['\r', '\n']).to_string()))
    }
}

#[cfg(not(unix))]
mod tty {
    /// No hidden input without a Unix terminal: the caller falls back.
    pub fn read_hidden(_prompt: &str) -> anyhow::Result<Option<String>> {
        Ok(None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::access;
    use std::time::Duration;

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

    const PASSWORD: &str = "correct horse battery staple 1";

    /// A test transaction that takes its lock on `access_users` first, so it
    /// cannot deadlock with the migration test, which locks that table and
    /// then upgrades the lock.
    fn begin(client: &mut postgres::Client) -> postgres::Transaction<'_> {
        let mut tx = client.transaction().expect("begin test tx");
        tx.batch_execute("LOCK TABLE access_users IN ROW EXCLUSIVE MODE")
            .expect("lock accounts");
        tx
    }

    /// A name no other test uses.
    fn fresh(prefix: &str) -> String {
        format!("{prefix}-{}", uuid::Uuid::new_v4().simple())
    }

    /// An account inserted directly, without hashing: (name, id).
    fn insert<C: GenericClient>(client: &mut C, role: &str, enabled: bool) -> (String, i64) {
        let name = fresh("t");
        let id = client
            .query_one(
                "INSERT INTO access_users (username, display_name, password_hash, role, enabled) \
                 VALUES ($1, 'Test', 'not-a-hash', $2, $3) RETURNING id",
                &[&name, &role, &enabled],
            )
            .expect("insert account")
            .get(0);
        (name, id)
    }

    fn open_session<C: GenericClient>(client: &mut C, user_id: i64) {
        client
            .execute(
                "INSERT INTO access_sessions (token_hash, user_id, csrf_token, expires_at) \
                 VALUES ($1, $2, 'csrf', now() + interval '1 day')",
                &[
                    &access::token_hash(&uuid::Uuid::new_v4().to_string()),
                    &user_id,
                ],
            )
            .expect("insert session");
    }

    fn sessions<C: GenericClient>(client: &mut C, user_id: i64) -> i64 {
        client
            .query_one(
                "SELECT count(*) FROM access_sessions WHERE user_id = $1",
                &[&user_id],
            )
            .expect("count sessions")
            .get(0)
    }

    /// Our accounts as `list` shows them.
    fn listed<C: GenericClient>(client: &mut C, names: &[&str]) -> Vec<Account> {
        list(client)
            .expect("list")
            .into_iter()
            .filter(|a| names.contains(&a.username.as_str()))
            .collect()
    }

    fn account(username: &str, display_name: &str, role: Role, enabled: bool) -> Account {
        Account {
            username: username.to_string(),
            display_name: display_name.to_string(),
            role,
            enabled,
        }
    }

    #[test]
    fn account_add_list_role_disable() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping account_add_list_role_disable: no PostgreSQL reachable");
            return;
        };
        let mut tx = begin(&mut client);
        let (ann, bob) = (fresh("ann"), fresh("bob"));
        assert!(!exists(&mut tx, &ann).unwrap());
        let ann_id = add(&mut tx, &ann, Some("Ann Example"), Role::Editor, PASSWORD).expect("add");
        add(&mut tx, &bob, None, Role::Viewer, PASSWORD).expect("add");
        assert!(exists(&mut tx, &ann).unwrap());
        let err = add(&mut tx, &ann, None, Role::Admin, PASSWORD)
            .expect_err("duplicate")
            .to_string();
        assert!(err.contains("already exists"), "{err}");

        let stored: String = tx
            .query_one(
                "SELECT password_hash FROM access_users WHERE id = $1",
                &[&ann_id],
            )
            .unwrap()
            .get(0);
        assert!(stored.starts_with("scrypt2$15,8,3$"), "{stored}");
        assert!(access::verify_password(PASSWORD, &stored));

        let names = [ann.as_str(), bob.as_str()];
        assert_eq!(
            listed(&mut tx, &names),
            vec![
                account(&ann, "Ann Example", Role::Editor, true),
                account(&bob, &bob, Role::Viewer, true),
            ]
        );

        set_role(&mut tx, &ann, Role::Admin).expect("promote");
        disable(&mut tx, &bob).expect("disable");
        assert_eq!(
            listed(&mut tx, &names),
            vec![
                account(&ann, "Ann Example", Role::Admin, true),
                account(&bob, &bob, Role::Viewer, false),
            ]
        );
        enable(&mut tx, &bob).expect("enable");
        assert!(listed(&mut tx, &names)[1].enabled);

        tx.execute(
            "INSERT INTO access_attempts (key, attempts) VALUES ($1, 10)",
            &[&access::attempt_key(&bob)],
        )
        .unwrap();
        assert!(unlock(&mut tx, &bob).expect("unlock"), "the row went");
        assert!(!unlock(&mut tx, &bob).expect("unlock"), "nothing left");

        let nobody = fresh("nobody");
        for err in [
            set_role(&mut tx, &nobody, Role::Viewer).map(|_| ()),
            enable(&mut tx, &nobody),
            disable(&mut tx, &nobody).map(|_| ()),
            unlock(&mut tx, &nobody).map(|_| ()),
            set_password(&mut tx, &nobody, PASSWORD).map(|_| ()),
            new_agent_key(&mut tx, &nobody, None).map(|_| ()),
            revoke_agent_key(&mut tx, &nobody, 1),
        ] {
            let err = err.expect_err("unknown account").to_string();
            assert!(err.contains("no account named"), "{err}");
        }

        for bad in ["", "two\nlines", "tab\there", &"x".repeat(201)] {
            let err = add(&mut tx, &fresh("cat"), Some(bad), Role::Viewer, PASSWORD)
                .expect_err("bad display name")
                .to_string();
            assert!(err.contains("display name"), "{err}");
        }
    }

    #[test]
    fn last_admin_cannot_be_demoted_or_disabled() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping last_admin_cannot_be_demoted_or_disabled: no PostgreSQL reachable");
            return;
        };
        // No admin from elsewhere counts: access_users is locked first (as
        // in the access tests) and other admins are switched off, inside
        // this transaction only.
        let mut tx = client.transaction().expect("begin test tx");
        tx.batch_execute(
            "LOCK TABLE access_users IN SHARE ROW EXCLUSIVE MODE; \
             UPDATE access_users SET enabled = false WHERE role = 'admin' AND enabled;",
        )
        .expect("only our admins");
        let (a, _) = insert(&mut tx, "admin", true);
        let refused = |result: Result<u64>| {
            let err = result.expect_err("last admin").to_string();
            assert!(err.contains("last enabled admin"), "{err}");
        };
        refused(set_role(&mut tx, &a, Role::Editor));
        refused(set_role(&mut tx, &a, Role::Viewer));
        refused(disable(&mut tx, &a));
        assert_eq!(
            listed(&mut tx, &[&a]),
            vec![account(&a, "Test", Role::Admin, true)]
        );
        set_role(&mut tx, &a, Role::Admin).expect("an admin stays admin");

        // A second admin lifts the rule, and then it is the last one.
        let (b, _) = insert(&mut tx, "admin", true);
        set_role(&mut tx, &a, Role::Editor).expect("b remains");
        refused(disable(&mut tx, &b));

        // A disabled admin does not count.
        let (c, _) = insert(&mut tx, "admin", true);
        disable(&mut tx, &c).expect("b remains");
        refused(set_role(&mut tx, &b, Role::Viewer));
        enable(&mut tx, &c).expect("enable");
        set_role(&mut tx, &b, Role::Viewer).expect("c remains");
        refused(disable(&mut tx, &c));
        // Non-admins are never held back.
        disable(&mut tx, &a).expect("an editor");
    }

    /// Drops a test schema on a fresh connection, also when the test fails.
    struct DropSchema(String);

    impl Drop for DropSchema {
        fn drop(&mut self) {
            if let Some(mut client) = test_db() {
                let _ = client.batch_execute(&format!("DROP SCHEMA IF EXISTS {} CASCADE", self.0));
            }
        }
    }

    #[test]
    fn last_admin_rule_holds_across_connections() {
        let (Some(mut setup), Some(mut a), Some(mut b)) = (test_db(), test_db(), test_db()) else {
            eprintln!(
                "skipping last_admin_rule_holds_across_connections: \
                 three PostgreSQL connections are needed"
            );
            return;
        };
        // A second connection only sees committed admins, so they live in a
        // schema of their own and never in the shared account tables.
        let schema = format!("test_admins_{}", uuid::Uuid::new_v4().simple());
        setup
            .batch_execute(&format!(
                "CREATE SCHEMA {schema}; \
                 CREATE TABLE {schema}.access_users (LIKE public.access_users INCLUDING ALL); \
                 CREATE TABLE {schema}.access_sessions (LIKE public.access_sessions INCLUDING ALL); \
                 INSERT INTO {schema}.access_users (username, display_name, password_hash, role) \
                     VALUES ('admin-1', 'One', 'not-a-hash', 'admin'), \
                            ('admin-2', 'Two', 'not-a-hash', 'admin');"
            ))
            .expect("test schema");
        let _cleanup = DropSchema(schema.clone());
        let session = format!("SET search_path TO {schema}; SET lock_timeout = '20s'");
        a.batch_execute(&session).expect("search path");
        b.batch_execute(&session).expect("search path");

        // A demotes admin-1 and keeps its transaction open.
        let mut tx = a.transaction().expect("begin");
        set_role(&mut tx, "admin-1", Role::Editor).expect("admin-2 remains");

        // B demotes admin-2 meanwhile: it has to wait for A, then refuse.
        let (done, outcome) = std::sync::mpsc::channel();
        let other = std::thread::spawn(move || {
            let result = set_role(&mut b, "admin-2", Role::Editor).map_err(|e| e.to_string());
            let _ = done.send(result);
        });
        assert!(
            outcome.recv_timeout(Duration::from_millis(500)).is_err(),
            "B must wait while A holds the admins"
        );
        tx.commit().expect("A commits");
        let err = outcome
            .recv_timeout(Duration::from_secs(15))
            .expect("B finishes once A has committed")
            .expect_err("admin-2 is the last admin now");
        assert!(err.contains("last enabled admin"), "{err}");
        other.join().expect("B's thread");

        let roles: Vec<(String, String)> = setup
            .query(
                &format!("SELECT username, role FROM {schema}.access_users ORDER BY username"),
                &[],
            )
            .expect("roles")
            .iter()
            .map(|row| (row.get(0), row.get(1)))
            .collect();
        assert_eq!(
            roles,
            [
                ("admin-1".to_string(), "editor".to_string()),
                ("admin-2".to_string(), "admin".to_string())
            ]
        );
    }

    #[test]
    fn disable_and_password_delete_sessions() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping disable_and_password_delete_sessions: no PostgreSQL reachable");
            return;
        };
        let mut tx = begin(&mut client);
        let (name, id) = insert(&mut tx, "editor", true);
        let (_, other) = insert(&mut tx, "editor", true);
        open_session(&mut tx, other);

        open_session(&mut tx, id);
        open_session(&mut tx, id);
        assert_eq!(disable(&mut tx, &name).expect("disable"), 2);
        assert_eq!(sessions(&mut tx, id), 0);
        enable(&mut tx, &name).expect("enable");

        open_session(&mut tx, id);
        open_session(&mut tx, id);
        let new = "correct horse battery staple 9";
        assert_eq!(set_password(&mut tx, &name, new).expect("password"), 2);
        assert_eq!(sessions(&mut tx, id), 0);
        let stored: String = tx
            .query_one(
                "SELECT password_hash FROM access_users WHERE id = $1",
                &[&id],
            )
            .unwrap()
            .get(0);
        assert!(access::verify_password(new, &stored));
        assert!(!access::verify_password(PASSWORD, &stored));

        open_session(&mut tx, id);
        assert_eq!(set_role(&mut tx, &name, Role::Viewer).expect("role"), 1);
        assert_eq!(sessions(&mut tx, id), 0);

        // A refused password changes nothing and ends nothing.
        open_session(&mut tx, id);
        let err = set_password(&mut tx, &name, "too short")
            .expect_err("short")
            .to_string();
        assert!(err.contains("15 to 128 characters"), "{err}");
        assert_eq!(sessions(&mut tx, id), 1);
        let unchanged: String = tx
            .query_one(
                "SELECT password_hash FROM access_users WHERE id = $1",
                &[&id],
            )
            .unwrap()
            .get(0);
        assert_eq!(unchanged, stored);
        // Enabling and unlocking keep sessions; other accounts keep theirs.
        enable(&mut tx, &name).expect("enable");
        unlock(&mut tx, &name).expect("unlock");
        assert_eq!(sessions(&mut tx, id), 1);
        assert_eq!(sessions(&mut tx, other), 1);
    }

    #[test]
    fn agent_key_is_stored_as_hash_only() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping agent_key_is_stored_as_hash_only: no PostgreSQL reachable");
            return;
        };
        let mut tx = begin(&mut client);
        let (name, id) = insert(&mut tx, "editor", true);
        let (first, key) = new_agent_key(&mut tx, &name, Some("laptop")).expect("key");
        assert_eq!(key.len(), 64);
        assert!(key.chars().all(|c| c.is_ascii_hexdigit()));
        let row = tx
            .query_one(
                "SELECT user_id, key_hash, label, revoked_at IS NULL FROM access_agent_keys \
                 WHERE id = $1",
                &[&first],
            )
            .unwrap();
        assert_eq!(row.get::<_, i64>(0), id);
        assert_eq!(row.get::<_, String>(1), access::token_hash(&key));
        assert_eq!(row.get::<_, Option<String>>(2).as_deref(), Some("laptop"));
        assert!(row.get::<_, bool>(3), "active");
        let anywhere: i64 = tx
            .query_one(
                "SELECT count(*) FROM access_agent_keys k WHERE strpos(k::text, $1) > 0",
                &[&key],
            )
            .unwrap()
            .get(0);
        assert_eq!(anywhere, 0, "the key itself is in no column");

        let (second, other_key) = new_agent_key(&mut tx, &name, None).expect("key");
        assert_ne!(key, other_key);
        let mine = |tx: &mut postgres::Transaction<'_>| -> Vec<AgentKeyEntry> {
            active_agent_keys(tx)
                .expect("keys")
                .into_iter()
                .filter(|k| k.username == name)
                .collect()
        };
        let entry = |id: i64, label: Option<&str>| AgentKeyEntry {
            id,
            username: name.clone(),
            label: label.map(str::to_string),
        };
        assert_eq!(
            mine(&mut tx),
            vec![entry(first, Some("laptop")), entry(second, None)]
        );

        // Revoking needs the owner's name; a revoked key cannot be revoked again.
        let (stranger, _) = insert(&mut tx, "editor", true);
        let err = revoke_agent_key(&mut tx, &stranger, first)
            .expect_err("not the owner")
            .to_string();
        assert!(err.contains("no active agent key"), "{err}");
        revoke_agent_key(&mut tx, &name, first).expect("revoke");
        let err = revoke_agent_key(&mut tx, &name, first)
            .expect_err("already revoked")
            .to_string();
        assert!(err.contains("no active agent key"), "{err}");
        assert_eq!(mine(&mut tx), vec![entry(second, None)]);
        let revoked: bool = tx
            .query_one(
                "SELECT revoked_at IS NOT NULL FROM access_agent_keys WHERE id = $1",
                &[&first],
            )
            .unwrap()
            .get(0);
        assert!(revoked, "the row stays, marked revoked");

        let err = new_agent_key(&mut tx, &name, Some("bad\nlabel"))
            .expect_err("control character")
            .to_string();
        assert!(err.contains("label"), "{err}");
    }

    #[test]
    fn username_pattern_is_enforced() {
        let valid = [
            "a".to_string(),
            "0".to_string(),
            "alice".to_string(),
            "9lives".to_string(),
            "a.b_c@d-e".to_string(),
            "x".repeat(120),
        ];
        let invalid = [
            String::new(),
            "Alice".to_string(),
            "ALICE".to_string(),
            "-a".to_string(),
            ".a".to_string(),
            "_a".to_string(),
            "@a".to_string(),
            "a b".to_string(),
            "a\n".to_string(),
            "jürgen".to_string(),
            "a/b".to_string(),
            "a+b".to_string(),
            "x".repeat(121),
        ];
        for name in &valid {
            validate_username(name).unwrap_or_else(|e| panic!("{name}: {e}"));
        }
        for name in &invalid {
            let err = validate_username(name).expect_err(name).to_string();
            assert!(err.contains("account name"), "{err}");
        }

        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping username_pattern_is_enforced (database part): no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = begin(&mut client);
        // The check agrees with the schema's CHECK constraint.
        for (name, ok) in valid
            .iter()
            .map(|n| (n, true))
            .chain(invalid.iter().map(|n| (n, false)))
        {
            let matches: bool = tx
                .query_one("SELECT $1 ~ '^[a-z0-9][a-z0-9._@-]{0,119}$'", &[name])
                .unwrap()
                .get(0);
            assert_eq!(matches, ok, "{name:?}");
        }
        // Upper case is refused, never folded to lower case.
        let lower = fresh("zed");
        let err = add(&mut tx, &lower.to_uppercase(), None, Role::Viewer, PASSWORD)
            .expect_err("upper case")
            .to_string();
        assert!(err.contains("account name"), "{err}");
        assert!(!exists(&mut tx, &lower).unwrap());
    }

    #[cfg(unix)]
    #[test]
    fn password_file_must_be_owner_only() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().expect("temp dir");
        let path = dir.path().join("password");
        std::fs::write(&path, format!("{PASSWORD}\r\n\n")).unwrap();
        for mode in [0o644, 0o640, 0o604, 0o660] {
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(mode)).unwrap();
            let err = read_password_file(&path).expect_err("too open").to_string();
            assert!(err.contains("chmod 600"), "{mode:o}: {err}");
            assert!(!err.contains(PASSWORD), "{err}");
        }
        for mode in [0o600, 0o400, 0o700] {
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(mode)).unwrap();
            assert_eq!(read_password_file(&path).expect("owner only"), PASSWORD);
        }
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
        std::fs::write(&path, b"\xff\xfe not text").unwrap();
        let err = read_password_file(&path)
            .expect_err("not UTF-8")
            .to_string();
        assert!(err.contains("UTF-8"), "{err}");
        assert!(read_password_file(&dir.path().join("missing")).is_err());
    }
}
