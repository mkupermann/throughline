//! Shared secret for agents that reach Throughline over HTTP.
//!
//! `THROUGHLINE_AGENT_KEY` is sent as `Authorization: Bearer <key>`. Only the
//! SHA-256 digest of the key is kept, and the key value never appears in a
//! response, an error, a log line or `doctor` output.

use sha2::{Digest, Sha256};

pub const MIN_KEY_LEN: usize = 32;
const KEY_VAR: &str = "THROUGHLINE_AGENT_KEY";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum KeyState {
    Off,
    TooShort,
    /// SHA-256 of the trimmed key.
    Ready([u8; 32]),
}

#[derive(Debug, PartialEq, Eq)]
pub enum KeyCheck {
    Accepted,
    Missing,
    Wrong,
    Off,
    TooShort,
}

fn digest(value: &str) -> [u8; 32] {
    Sha256::digest(value.as_bytes()).into()
}

pub fn key_state_from(value: Option<&str>) -> KeyState {
    let key = value.map(str::trim).unwrap_or("");
    if key.is_empty() {
        KeyState::Off
    } else if key.chars().count() < MIN_KEY_LEN {
        KeyState::TooShort
    } else {
        KeyState::Ready(digest(key))
    }
}

pub fn key_state() -> KeyState {
    key_state_from(std::env::var(KEY_VAR).ok().as_deref())
}

/// Credentials of an `Authorization: Bearer <credentials>` value.
fn bearer_credentials(header: &str) -> Option<&str> {
    let header = header.trim_start();
    let end = header.find([' ', '\t'])?;
    let (scheme, rest) = header.split_at(end);
    if !scheme.eq_ignore_ascii_case("bearer") {
        return None;
    }
    let credentials = rest.trim();
    (!credentials.is_empty()).then_some(credentials)
}

/// Whether an `Authorization` value uses the Bearer scheme, with or without
/// credentials. Reads raw bytes, so a value that is not text still counts.
pub(crate) fn is_bearer_scheme(header: &[u8]) -> bool {
    let blank = |b: &u8| matches!(b, b' ' | b'\t');
    let start = header.iter().position(|b| !blank(b)).unwrap_or(header.len());
    let rest = &header[start..];
    let end = rest.iter().position(blank).unwrap_or(rest.len());
    rest[..end].eq_ignore_ascii_case(b"bearer")
}

pub fn check_bearer(state: &KeyState, authorization: Option<&str>) -> KeyCheck {
    let expected = match state {
        KeyState::Off => return KeyCheck::Off,
        KeyState::TooShort => return KeyCheck::TooShort,
        KeyState::Ready(d) => d,
    };
    let Some(credentials) = authorization.and_then(bearer_credentials) else {
        return KeyCheck::Missing;
    };
    let presented = digest(credentials);
    // Every byte is touched; the decision comes after the loop.
    let mut diff = 0u8;
    for (a, b) in presented.iter().zip(expected.iter()) {
        diff |= a ^ b;
    }
    if diff == 0 {
        KeyCheck::Accepted
    } else {
        KeyCheck::Wrong
    }
}

/// 64 hex characters from two random UUIDs.
pub fn generate() -> String {
    format!(
        "{}{}",
        uuid::Uuid::new_v4().simple(),
        uuid::Uuid::new_v4().simple()
    )
}

pub fn doctor_line(state: &KeyState) -> String {
    match state {
        KeyState::Ready(_) => "on".to_string(),
        KeyState::Off => format!("off: {KEY_VAR} is not set"),
        KeyState::TooShort => format!("off: {KEY_VAR} is shorter than {MIN_KEY_LEN} characters"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const KEY: &str = "0123456789abcdef0123456789abcdef";

    fn ready() -> KeyState {
        key_state_from(Some(KEY))
    }

    #[test]
    fn key_state_off_when_unset_or_blank() {
        assert_eq!(key_state_from(None), KeyState::Off);
        assert_eq!(key_state_from(Some("")), KeyState::Off);
        assert_eq!(key_state_from(Some("   \t")), KeyState::Off);
    }

    #[test]
    fn key_state_too_short_below_32() {
        assert_eq!(key_state_from(Some(&KEY[..31])), KeyState::TooShort);
        // the length counts after trim
        assert_eq!(
            key_state_from(Some(&format!("  {}  ", &KEY[..31]))),
            KeyState::TooShort
        );
        assert!(matches!(key_state_from(Some(KEY)), KeyState::Ready(_)));
        assert_eq!(ready(), key_state_from(Some(&format!(" {KEY}\n"))));
    }

    #[test]
    fn correct_key_is_accepted() {
        assert_eq!(
            check_bearer(&ready(), Some(&format!("Bearer {KEY}"))),
            KeyCheck::Accepted
        );
        assert_eq!(
            check_bearer(&ready(), Some(&format!("Bearer {KEY}  "))),
            KeyCheck::Accepted
        );
    }

    #[test]
    fn wrong_key_is_rejected() {
        let other = "f".repeat(32);
        assert_eq!(
            check_bearer(&ready(), Some(&format!("Bearer {other}"))),
            KeyCheck::Wrong
        );
        assert_eq!(
            check_bearer(&ready(), Some(&format!("Bearer {}", &KEY[..31]))),
            KeyCheck::Wrong
        );
        assert_eq!(
            check_bearer(&KeyState::Off, Some("Bearer x")),
            KeyCheck::Off
        );
        assert_eq!(
            check_bearer(&KeyState::TooShort, Some("Bearer x")),
            KeyCheck::TooShort
        );
    }

    #[test]
    fn missing_header_is_missing() {
        assert_eq!(check_bearer(&ready(), None), KeyCheck::Missing);
        assert_eq!(check_bearer(&KeyState::Off, None), KeyCheck::Off);
        assert_eq!(check_bearer(&KeyState::TooShort, None), KeyCheck::TooShort);
    }

    #[test]
    fn bearer_scheme_is_case_insensitive() {
        for h in [
            format!("bearer {KEY}"),
            format!("BEARER {KEY}"),
            format!("Bearer    {KEY}"),
            format!("Bearer\t{KEY}"),
        ] {
            assert_eq!(check_bearer(&ready(), Some(&h)), KeyCheck::Accepted, "{h}");
        }
    }

    #[test]
    fn non_bearer_scheme_is_missing() {
        for h in [
            format!("Basic {KEY}"),
            KEY.to_string(),
            "Bearer".to_string(),
            "Bearer   ".to_string(),
        ] {
            assert_eq!(check_bearer(&ready(), Some(&h)), KeyCheck::Missing, "{h}");
        }
    }

    #[test]
    fn bearer_scheme_is_told_apart_from_other_schemes() {
        for h in [
            format!("Bearer {KEY}"),
            "bearer x".to_string(),
            " \tBEARER\tx".to_string(),
            "Bearer".to_string(),
            "Bearer   ".to_string(),
        ] {
            assert!(is_bearer_scheme(h.as_bytes()), "{h}");
        }
        for h in [
            format!("Basic {KEY}"),
            KEY.to_string(),
            "Bearerx y".to_string(),
            "Token x".to_string(),
            String::new(),
        ] {
            assert!(!is_bearer_scheme(h.as_bytes()), "{h}");
        }
        assert!(is_bearer_scheme(b"Bearer \xff"), "a value that is not text");
    }

    #[test]
    fn generated_key_is_64_hex_and_unique() {
        let a = generate();
        let b = generate();
        assert_eq!(a.len(), 64);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, b);
        assert!(matches!(key_state_from(Some(&a)), KeyState::Ready(_)));
    }

    #[test]
    fn doctor_line_never_contains_the_key() {
        for s in [ready(), KeyState::Off, KeyState::TooShort] {
            assert!(!doctor_line(&s).contains(KEY));
        }
        assert_eq!(doctor_line(&ready()), "on");
        assert_eq!(
            doctor_line(&KeyState::Off),
            "off: THROUGHLINE_AGENT_KEY is not set"
        );
        assert_eq!(
            doctor_line(&KeyState::TooShort),
            "off: THROUGHLINE_AGENT_KEY is shorter than 32 characters"
        );
    }
}
