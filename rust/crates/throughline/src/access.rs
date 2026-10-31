//! Team mode: roles, the actor behind a call, the auth mode and the
//! visibility filter for notes.
//!
//! `local` (the default) is the single-user behaviour: no login, every note
//! visible. `team` needs accounts. Notes are `shared` or `private` to their
//! owner, and server-wide jobs run as [`Actor::shared_only`].

use anyhow::{bail, Context, Result};
use postgres::GenericClient;
use sha2::{Digest, Sha256};

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

/// `THROUGHLINE_AUTH_MODE`: exactly `local` or `team`. Unset or empty
/// (an unset variable passed through by Compose) means `local`.
pub fn auth_mode_from(value: Option<&str>) -> Result<AuthMode> {
    match value {
        None | Some("") | Some("local") => Ok(AuthMode::Local),
        Some("team") => Ok(AuthMode::Team),
        Some(other) => bail!("THROUGHLINE_AUTH_MODE is `{other}`: use local or team"),
    }
}

/// Refuse to start when the mode does not fit the database or the URL.
/// `local` would show every account's private notes to anyone, so it needs
/// a database without accounts and private notes. `team` needs an enabled
/// admin and a public URL that keeps the session cookie off plain HTTP.
/// Both modes refuse a database that migration 014 has not reached.
pub fn check_startup<C: GenericClient>(
    client: &mut C,
    mode: AuthMode,
    public_url: Option<&str>,
) -> Result<()> {
    let (accounts, private) = counts(client)?;
    match mode {
        AuthMode::Local => {
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
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    // `http://localhost@evil.example.com` goes to evil.example.com.
    let authority = authority
        .rsplit_once('@')
        .map_or(authority, |(_, host)| host);
    let host = crate::server::host_of(authority);
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

/// [`visibility_predicate_at`] with the owner bound as `$1`, for a query
/// whose only parameter is the owner. Anything else uses [`push_visibility`].
pub fn visibility_predicate(actor: &Actor, alias: &str) -> (String, Option<i64>) {
    visibility_predicate_at(actor, alias, 1)
}

/// The visibility condition on the notes table `alias` for a query whose
/// parameters are collected in `params`: binds the owner as the next
/// parameter when the actor has one and returns the SQL fragment. This is
/// the form every note query uses.
pub fn push_visibility<'a>(
    actor: &'a Actor,
    alias: &str,
    params: &mut Vec<&'a (dyn postgres::types::ToSql + Sync)>,
) -> String {
    let (sql, owner) = visibility_predicate_at(actor, alias, params.len() + 1);
    if let (Some(_), Some(id)) = (owner, actor.user_id.as_ref()) {
        params.push(id);
    }
    sql
}

/// SQL condition on the notes table `alias` that keeps what `actor` may see,
/// and the value the caller binds as `$n` when there is one. Local mode sees
/// everything, an account sees shared notes and its own, and jobs or anything
/// without an account see shared notes only. Callers must not number `$n`
/// themselves: note queries (Task 4 on) go through [`push_visibility`].
pub(crate) fn visibility_predicate_at(
    actor: &Actor,
    alias: &str,
    n: usize,
) -> (String, Option<i64>) {
    match (actor.via, actor.user_id) {
        (Via::Local, _) => ("TRUE".to_string(), None),
        (Via::Session | Via::AgentKey, Some(id)) => (
            format!("({alias}.visibility = 'shared' OR {alias}.owner_id = ${n})"),
            Some(id),
        ),
        _ => (format!("({alias}.visibility = 'shared')"), None),
    }
}

/// A well-formed hash no password matches, for accounts that do not exist:
/// verifying against it costs the same as against a real one.
pub const DUMMY_HASH: &str = "scrypt2$15$00000000000000000000000000000000$\
    0000000000000000000000000000000000000000000000000000000000000000\
    0000000000000000000000000000000000000000000000000000000000000000";

/// scrypt cost of new hashes: N = 2^15, r = 8, p = 1 (32 MiB). The cost is
/// part of the stored string, so it can rise later. Python-era hashes
/// (`scrypt$salt$digest`) used N = 2^17 (128 MiB).
const HASH_LOG_N: u8 = 15;
const LEGACY_LOG_N: u8 = 17;

/// Passwords have 15 to 128 characters, as in the Python era.
fn password_length_ok(password: &str) -> bool {
    (15..=128).contains(&password.chars().count())
}

/// `scrypt2$15$<salt hex 32>$<digest hex 128>` with a fresh random salt.
/// Refuses a password outside 15..=128 characters. Slow on purpose: never
/// call it while holding a database lock.
pub fn hash_password(password: &str) -> Result<String> {
    if !password_length_ok(password) {
        bail!("use a password with 15 to 128 characters");
    }
    let salt = random_salt();
    let digest = derive(password, &salt, HASH_LOG_N)?;
    Ok(format!(
        "scrypt2${HASH_LOG_N}${}${}",
        to_hex(&salt),
        to_hex(&digest)
    ))
}

/// Whether `password` matches `encoded` (`scrypt2$<15..=17>$…` or the
/// Python-era `scrypt$<salt>$<digest>`). Every call with a password of
/// valid length runs exactly one scrypt: an unknown or malformed hash is
/// checked against [`DUMMY_HASH`] and fails, so the time spent does not
/// tell a broken or missing account from a wrong password. The digest is
/// compared over all 64 bytes before deciding.
pub fn verify_password(password: &str, encoded: &str) -> bool {
    if !password_length_ok(password) {
        return false;
    }
    let (known, (log_n, salt, expected)) = match parse_hash(encoded) {
        Some(parts) => (true, parts),
        None => (false, DUMMY_PARTS),
    };
    let Ok(actual) = derive(password, &salt, log_n) else {
        return false;
    };
    let mut diff = 0u8;
    for (a, b) in actual.iter().zip(expected.iter()) {
        diff |= a ^ b;
    }
    known && diff == 0
}

/// [`DUMMY_HASH`] parsed.
const DUMMY_PARTS: (u8, [u8; 16], [u8; 64]) = (HASH_LOG_N, [0; 16], [0; 64]);

/// (log_n, salt, digest) of a stored hash, or `None` for anything else.
/// The cost is capped at 2^17 so a stored value cannot ask for more memory
/// than the Python-era hashes did.
fn parse_hash(encoded: &str) -> Option<(u8, [u8; 16], [u8; 64])> {
    let parts: Vec<&str> = encoded.split('$').collect();
    let (log_n, salt, digest) = match parts.as_slice() {
        ["scrypt2", log_n, salt, digest] => {
            let log_n = match *log_n {
                "15" => 15,
                "16" => 16,
                "17" => 17,
                _ => return None,
            };
            (log_n, salt, digest)
        }
        ["scrypt", salt, digest] => (LEGACY_LOG_N, salt, digest),
        _ => return None,
    };
    Some((log_n, from_hex(salt)?, from_hex(digest)?))
}

fn derive(password: &str, salt: &[u8], log_n: u8) -> Result<[u8; 64]> {
    #[cfg(test)]
    DERIVATIONS.with(|d| d.borrow_mut().push(log_n));
    let params = scrypt::Params::new(log_n, 8, 1, 64)
        .map_err(|_| anyhow::anyhow!("invalid scrypt parameters"))?;
    let mut out = [0u8; 64];
    scrypt::scrypt(password.as_bytes(), salt, &params, &mut out)
        .map_err(|_| anyhow::anyhow!("invalid scrypt output length"))?;
    Ok(out)
}

/// 16 bytes from two random (v4) UUIDs: SHA-256 over their 244 random bits,
/// cut to 128. `uuid` already draws from the operating system.
fn random_salt() -> [u8; 16] {
    let mut hasher = Sha256::new();
    hasher.update(uuid::Uuid::new_v4().as_bytes());
    hasher.update(uuid::Uuid::new_v4().as_bytes());
    let digest = hasher.finalize();
    let mut salt = [0u8; 16];
    salt.copy_from_slice(&digest[..16]);
    salt
}

fn to_hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Exactly `2 * N` hex digits (either case), nothing else.
fn from_hex<const N: usize>(text: &str) -> Option<[u8; N]> {
    let text = text.as_bytes();
    if text.len() != 2 * N {
        return None;
    }
    let mut out = [0u8; N];
    for (byte, pair) in out.iter_mut().zip(text.chunks_exact(2)) {
        let high = char::from(pair[0]).to_digit(16)?;
        let low = char::from(pair[1]).to_digit(16)?;
        *byte = (high * 16 + low) as u8;
    }
    Some(out)
}

/// SHA-256 hex of a token, agent key or attempt key: what the database keeps.
pub fn token_hash(value: &str) -> String {
    format!("{:x}", Sha256::digest(value.as_bytes()))
}

/// `access_attempts.key` of an account's failed sign-ins: the hash of
/// `account:<name>`, as the Python era wrote it, so the table holds no
/// account names.
pub fn attempt_key(username: &str) -> String {
    token_hash(&format!("account:{username}"))
}

#[cfg(test)]
thread_local! {
    /// `log_n` of every scrypt run on this thread, for the timing-shape test.
    static DERIVATIONS: std::cell::RefCell<Vec<u8>> = const { std::cell::RefCell::new(Vec::new()) };
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
    /// counts. `access_users` is locked first, as in the migration test, so
    /// the two cannot deadlock.
    fn without_accounts(client: &mut postgres::Client) -> postgres::Transaction<'_> {
        let mut tx = client
            .build_transaction()
            .isolation_level(postgres::IsolationLevel::RepeatableRead)
            .start()
            .expect("begin test tx");
        tx.batch_execute(
            "LOCK TABLE access_users IN SHARE ROW EXCLUSIVE MODE; \
             UPDATE memory_chunks SET owner_id = NULL, visibility = 'shared' \
                 WHERE owner_id IS NOT NULL OR visibility <> 'shared'; \
             DELETE FROM favorites WHERE user_id IS NOT NULL; \
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

    // Python-era hashes as `throughline/api/access.py` wrote them, made with
    //   docker run --rm python:3.12-slim python3 -c 'import hashlib; ...
    //   d = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**17,
    //       r=8, p=1, maxmem=256 * 1024**2, dklen=64)
    //   print(f"scrypt${salt}${d.hex()}")'
    // Test values, not secrets.
    const LEGACY: [(&str, &str); 3] = [
        (
            "correct horse battery staple 1",
            "scrypt$00112233445566778899aabbccddeeff$b7a11a1da4c37f8ce0fd857b975d4ea6658d9d7c907b441932f25c5551380b9563dfcf56f1636c169255c63d0be2e7367eb570e2aba9adb75d900072aecd00aa",
        ),
        (
            "Grüße aus Köln, Passwort Nummer 2",
            "scrypt$0f1e2d3c4b5a69788796a5b4c3d2e1f0$2e789cb52c291f9efc9ad3d75c2328060f0be51e56738b3505d7d81921cb77bd903414ca9f5a6825039493f442314b701ac67ab99c4367a80ad9faf59bba0b07",
        ),
        (
            "correct horse battery staple 3 with $ signs",
            "scrypt$deadbeefcafebabe0123456789abcdef$e8e30a26bf0ec8180d023f982fdd6ebc9b661ff9ad054bfd31c16ad530c301e586c246465e50e8b4fc5909239ee1311465e2b12a76f4f2f0d8466c64bc555ca6",
        ),
    ];

    // The same Python call with n=2**15, written as `scrypt2$15$...`: pins
    // the parameters of new hashes. Test value, not a secret.
    const NEW_FORMAT: (&str, &str) = (
        "correct horse battery staple 4",
        "scrypt2$15$a1b2c3d4e5f60718293a4b5c6d7e8f90$a29c0d25639b5209b251b8d6d58f97db08fc868b70416068a19d959738e40be64714ff9f81f8d5bc754df12cb1f90ba5a28986687d50af0342aca332dd43258c",
    );

    /// Runs `f` and returns the `log_n` of each scrypt run it made.
    fn derivations(f: impl FnOnce()) -> Vec<u8> {
        DERIVATIONS.with(|d| d.borrow_mut().clear());
        f();
        DERIVATIONS.with(|d| d.borrow().clone())
    }

    #[test]
    fn new_hashes_use_scrypt2_and_verify() {
        let password = "correct horse battery staple 1";
        let first = hash_password(password).expect("hash");
        let second = hash_password(password).expect("hash");
        assert_ne!(first, second, "every hash gets its own salt");
        for encoded in [&first, &second] {
            let parts: Vec<&str> = encoded.split('$').collect();
            assert_eq!(parts.len(), 4, "{encoded}");
            assert_eq!((parts[0], parts[1]), ("scrypt2", "15"));
            assert_eq!((parts[2].len(), parts[3].len()), (32, 128));
            assert!(parts[2..].iter().all(|p| p
                .chars()
                .all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c))));
            assert!(verify_password(password, encoded));
            assert!(!verify_password("correct horse battery staple 2", encoded));
        }
        let (password, encoded) = NEW_FORMAT;
        assert!(verify_password(password, encoded), "Python n=2**15 vector");
        assert!(!verify_password("correct horse battery staple 5", encoded));
        // An upper-case digest is the same digest.
        assert!(verify_password(
            password,
            &encoded.to_uppercase().replace("SCRYPT2", "scrypt2")
        ));
    }

    #[test]
    fn legacy_python_hashes_verify() {
        for (password, encoded) in LEGACY {
            assert_eq!(
                derivations(|| assert!(verify_password(password, encoded), "{password}")),
                vec![17],
                "legacy hashes use n=2**17"
            );
        }
        assert!(!verify_password(
            "correct horse battery staple 2",
            LEGACY[0].1
        ));
    }

    #[test]
    fn unknown_format_is_rejected_in_constant_shape() {
        let password = LEGACY[0].0;
        let (salt, digest) = LEGACY[0].1["scrypt$".len()..]
            .split_once('$')
            .expect("legacy vector");
        let unknown = [
            String::new(),
            "plain text".to_string(),
            format!("bcrypt${salt}${digest}"),
            format!("SCRYPT${salt}${digest}"),
            format!("scrypt${salt}"),
            format!("scrypt${salt}${digest}$"),
            format!("scrypt${}${digest}", &salt[2..]),
            format!("scrypt${salt}${}", &digest[2..]),
            format!("scrypt${salt}${digest}00"),
            format!("scrypt$zz{}${digest}", &salt[2..]),
            format!("scrypt${salt}$+f{}", &digest[2..]),
            format!("scrypt2${salt}${digest}"),
            format!("scrypt2$14${salt}${digest}"),
            format!("scrypt2$18${salt}${digest}"),
            format!("scrypt2$30${salt}${digest}"),
            format!("scrypt2$015${salt}${digest}"),
            format!("scrypt2$+15${salt}${digest}"),
            format!("scrypt2$15${salt}${digest}$"),
            format!("scrypt2$15$ {}${digest}", &salt[1..]),
        ];
        for encoded in &unknown {
            assert_eq!(
                derivations(|| assert!(!verify_password(password, encoded), "{encoded}")),
                vec![15],
                "one run at the dummy cost for {encoded:?}"
            );
        }
        // The dummy itself is a valid hash that nothing matches.
        assert_eq!(
            derivations(|| assert!(!verify_password(password, DUMMY_HASH))),
            vec![15]
        );
        assert_eq!(
            derivations(|| assert!(verify_password(NEW_FORMAT.0, NEW_FORMAT.1))),
            vec![15]
        );
    }

    #[test]
    fn password_length_is_enforced() {
        for password in [
            String::new(),
            "q".to_string(),
            "x".repeat(14),
            "ä".repeat(14),
            "x".repeat(129),
            "ä".repeat(129),
        ] {
            let err = hash_password(&password).expect_err("refused").to_string();
            assert!(err.contains("15 to 128 characters"), "{err}");
            if !password.is_empty() {
                assert!(
                    !err.contains(&password),
                    "the error leaves the password out"
                );
            }
            assert!(
                derivations(|| assert!(!verify_password(&password, DUMMY_HASH))).is_empty(),
                "no hashing for a password of {} characters",
                password.chars().count()
            );
        }
        // Characters, not bytes: 15 umlauts are 30 bytes and fine.
        for password in ["ä".repeat(15), "x".repeat(128)] {
            let encoded = hash_password(&password).expect("in range");
            assert!(verify_password(&password, &encoded));
        }
    }

    #[test]
    fn dummy_hash_and_token_hash_have_the_stored_shape() {
        assert_eq!(DUMMY_HASH.len(), "scrypt2$15$".len() + 32 + 1 + 128);
        assert!(DUMMY_HASH.starts_with("scrypt2$15$"));
        assert_eq!(
            token_hash("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        // Python: token_hash("account:" + username).
        assert_eq!(attempt_key("alice"), token_hash("account:alice"));
    }

    #[test]
    fn auth_mode_parses_only_local_and_team() {
        for (value, mode) in [
            (None, AuthMode::Local),
            (Some(""), AuthMode::Local),
            (Some("local"), AuthMode::Local),
            (Some("team"), AuthMode::Team),
        ] {
            assert_eq!(auth_mode_from(value).unwrap(), mode, "{value:?}");
        }
        for value in [
            "teams", "off", "1", "admin", "Team", "TEAM", " team", "local ",
        ] {
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
            let mut params: Vec<&(dyn postgres::types::ToSql + Sync)> = vec![&notes];
            let predicate = push_visibility(actor, "m", &mut params);
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
    fn push_visibility_binds_the_owner_at_the_next_position() {
        let (a, b) = (1_i64, "x");
        let user = Actor {
            user_id: Some(42),
            username: None,
            role: Role::Viewer,
            via: Via::Session,
        };
        for (actor, fragment, pushed) in [
            (Actor::local(), "TRUE".to_string(), 0),
            (
                Actor::shared_only(),
                "(n.visibility = 'shared')".to_string(),
                0,
            ),
            (
                user,
                "(n.visibility = 'shared' OR n.owner_id = $3)".to_string(),
                1,
            ),
        ] {
            let mut params: Vec<&(dyn postgres::types::ToSql + Sync)> = vec![&a, &b];
            assert_eq!(push_visibility(&actor, "n", &mut params), fragment);
            assert_eq!(params.len(), 2 + pushed, "{fragment}");
        }
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

        add_note(&mut tx, "shared", None);
        check_startup(&mut tx, AuthMode::Local, None).expect("shared notes are fine");

        // Even a disabled account stops local mode.
        let user = add_user(&mut tx, "viewer", false);
        let err = check_startup(&mut tx, AuthMode::Local, None)
            .unwrap_err()
            .to_string();
        assert!(err.contains("1 accounts and 0 private notes"), "{err}");
        assert!(err.contains("THROUGHLINE_AUTH_MODE=team"), "{err}");

        // A private note always has an owner, so it comes with an account.
        add_note(&mut tx, "private", Some(user));
        let err = check_startup(&mut tx, AuthMode::Local, None)
            .unwrap_err()
            .to_string();
        assert!(err.contains("1 accounts and 1 private notes"), "{err}");
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
            Some("http://localhost:8787@evil.example.com"),
            Some("http://[::1]@evil.example.com"),
        ] {
            let err = check_startup(&mut tx, AuthMode::Team, url)
                .expect_err("refused")
                .to_string();
            assert!(err.contains("THROUGHLINE_PUBLIC_URL"), "{url:?}: {err}");
        }

        // Before migration 014 notes have no visibility: refused in both
        // modes. A temporary table shadows memory_chunks for this session.
        tx.batch_execute("CREATE TEMP TABLE memory_chunks (id bigint) ON COMMIT DROP")
            .expect("shadow notes");
        for mode in [AuthMode::Local, AuthMode::Team] {
            // A savepoint each: the failed query of one mode must not abort
            // the transaction the other mode is checked in.
            let mut savepoint = tx.transaction().expect("savepoint");
            let err = check_startup(&mut savepoint, mode, https)
                .expect_err("refused before 014")
                .to_string();
            assert!(err.contains("throughline migrate"), "{mode:?}: {err}");
        }
    }
}
