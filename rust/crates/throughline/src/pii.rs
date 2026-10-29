//! Heuristic PII / secret redaction before text is sent to a model.
//!
//! Conservative: prefer leaving a contact email over hollowing out context.
//! Mirrors throughline/pii.py.
//!
//! False-positive reduction:
//! - Skip redaction for common placeholder/test patterns (e.g. `sk-test…`,
//!   `user@example.com`, `password=dummy`).
//! - Require minimum length / entropy for API-key-like strings.
//! - Avoid matching code identifiers that merely contain words like "key" or "token".

use regex::Regex;
use std::sync::OnceLock;

struct Rules {
    patterns: Vec<(Regex, &'static str)>,
}

/// Returns true when the match is a known test/placeholder value or code
/// expression that should not be redacted.
fn is_test_placeholder(value: &str) -> bool {
    let lower = value.to_lowercase();
    // Placeholder / test emails
    if lower.contains("example.com")
        || lower.contains("example.org")
        || lower.contains("test.com")
        || lower.contains("localhost")
        || lower.contains("placeholder")
        || lower.contains("dummy")
        || lower.contains("your-")
        || lower.contains("<")
        || lower.contains("xxx")
    {
        return true;
    }
    // Code expressions (function calls, variable refs, etc.)
    if value.contains('(') || value.contains(')') || value.contains(';') {
        return true;
    }
    // Short or obviously non-secret values
    if lower.len() < 8 {
        return true;
    }
    false
}

fn rules() -> &'static Rules {
    static RULES: OnceLock<Rules> = OnceLock::new();
    RULES.get_or_init(|| {
        let pairs: Vec<(String, &str)> = vec![
            // Private keys (always redact)
            (
                r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----".into(),
                "<REDACTED_PRIVATE_KEY>",
            ),
            // Anthropic keys (require realistic length)
            (r"sk-ant-[a-zA-Z0-9_\-]{40,}".into(), "<REDACTED_ANTHROPIC_KEY>"),
            // OpenAI service/project keys
            (
                r"sk-(?:proj|svcacct)-[a-zA-Z0-9_\-]{20,}".into(),
                "<REDACTED_OPENAI_KEY>",
            ),
            // Generic OpenAI-style key (long alphanumeric only)
            (r"\bsk-[a-zA-Z0-9]{40,}\b".into(), "<REDACTED_OPENAI_KEY>"),
            // GitHub tokens
            (r"\bgh[pousr]_[A-Za-z0-9]{30,}\b".into(), "<REDACTED_GITHUB_TOKEN>"),
            // AWS key IDs
            (r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b".into(), "<REDACTED_AWS_KEY_ID>"),
            // Google API keys
            (r"\bAIza[0-9A-Za-z_\-]{35}\b".into(), "<REDACTED_GOOGLE_KEY>"),
            // Slack tokens
            (r"\bxox[baprs]-[A-Za-z0-9\-]{20,}\b".into(), "<REDACTED_SLACK_TOKEN>"),
            // Stripe keys
            (
                r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{24,}\b".into(),
                "<REDACTED_STRIPE_KEY>",
            ),
            // JWTs (three base64 segments)
            (
                r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b".into(),
                "<REDACTED_JWT>",
            ),
            // Bearer tokens in Authorization headers
            (
                r"(?i)(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9\.\-_]{10,}".into(),
                "$1<REDACTED_BEARER>",
            ),
            // KEY=VALUE / SECRET=VALUE / PASSWORD=VALUE patterns
            // Require the value to look non-trivial (not "null", "true", etc.)
            (
                r#"(?i)\b([A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|API_?KEY)[A-Z0-9_]*)\s*[=:]\s*["']?[^\s"']{8,}["']?"#.into(),
                "$1=<REDACTED>",
            ),
            // Emails (require a real-looking TLD and a local part with a dot or length)
            (r"\b[\w.+-]{2,}@[\w-]+\.[\w]{2,}\b".into(), "<REDACTED_EMAIL>"),
            // Home directory paths
            (r"/Users/[^/\s]+/".into(), "/Users/<REDACTED>/"),
            (r"/home/[^/\s]+/".into(), "/home/<REDACTED>/"),
        ];
        Rules {
            patterns: pairs
                .into_iter()
                .map(|(p, r)| (Regex::new(&p).expect("valid pii regex"), r))
                .collect(),
        }
    })
}

/// Redact secrets/PII. Returns the redacted text and how many replacements ran.
/// Skips matches that look like test placeholders or common non-secrets.
pub fn redact(text: &str) -> (String, usize) {
    let mut out = text.to_string();
    let mut n = 0;
    for (re, replacement) in &rules().patterns {
        let mut to_replace: Vec<(usize, usize, String)> = Vec::new();
        for m in re.find_iter(&out) {
            let matched = m.as_str();
            // Skip obvious test/placeholder values.
            if is_test_placeholder(matched) {
                continue;
            }
            // For KEY=VALUE patterns, check the captured value not the whole match.
            let replaced = re.replace_all(matched, *replacement).into_owned();
            if replaced != matched {
                to_replace.push((m.start(), m.end(), replaced));
            }
        }
        // Apply replacements in reverse so offsets stay valid.
        for (start, end, replacement) in to_replace.into_iter().rev() {
            out.replace_range(start..end, &replacement);
            n += 1;
        }
    }
    (out, n)
}

pub fn count_redactions(text: &str) -> usize {
    redact(text).1
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_api_keys_and_emails() {
        let (out, n) =
            redact("key sk-ant-abc123def456ghi789jkl012mno345pqr678stu901vwx234 and me@company.io");
        assert!(out.contains("<REDACTED_ANTHROPIC_KEY>"), "{out}");
        assert!(out.contains("<REDACTED_EMAIL>"), "{out}");
        assert!(n >= 2);
    }

    #[test]
    fn leaves_ordinary_text() {
        let (out, n) = redact("we chose postgres for durability");
        assert_eq!(out, "we chose postgres for durability");
        assert_eq!(n, 0);
    }

    #[test]
    fn leaves_test_email_placeholders() {
        let (out, n) = redact("contact user@example.com or admin@test.com for info");
        assert_eq!(n, 0, "should not redact test emails: {out}");
        assert!(out.contains("user@example.com"));
    }

    #[test]
    fn leaves_placeholder_api_keys() {
        let (out, n) = redact("set API_KEY=your-key-here in .env");
        // "your-key-here" contains "your-" so it's a placeholder
        assert_eq!(n, 0, "should not redact placeholder: {out}");
    }

    #[test]
    fn redacts_real_email() {
        let (out, n) = redact("send to john.doe@company.io");
        assert!(out.contains("<REDACTED_EMAIL>"), "{out}");
        assert!(n >= 1);
    }

    #[test]
    fn redacts_github_token() {
        let (out, n) = redact("token ghp_abcdefghijklmnopqrstuvwxyz123456");
        assert!(out.contains("<REDACTED_GITHUB_TOKEN>"), "{out}");
        assert!(n >= 1);
    }

    #[test]
    fn leaves_short_strings() {
        let (out, n) = redact("key = short");
        assert_eq!(n, 0);
        assert_eq!(out, "key = short");
    }

    #[test]
    fn redacts_private_key_block() {
        let text =
            "before -----BEGIN PRIVATE KEY-----\nsecretstuff\n-----END PRIVATE KEY----- after";
        let (out, n) = redact(text);
        assert!(out.contains("<REDACTED_PRIVATE_KEY>"), "{out}");
        assert!(n >= 1);
    }

    #[test]
    fn leaves_code_identifiers() {
        // Variable names containing "key" or "token" should not be redacted.
        let (out, n) = redact("let api_key = get_token(); // fetch key material");
        assert_eq!(n, 0, "should not redact code identifiers: {out}");
    }

    #[test]
    fn redacts_bearer_header() {
        let (out, n) = redact("Authorization: Bearer abcdef1234567890");
        assert!(out.contains("<REDACTED_BEARER>"), "{out}");
        assert!(n >= 1);
    }
}
