"""PII / secret redaction for text that is about to be sent to an LLM.

Heuristic, regex-based. Intended to run immediately before the transcript is
handed to Claude for memory extraction. Conservative by design — we prefer
false negatives (leaks) over false positives (destroying legitimate context),
because memory chunks with hollowed-out content are worse than memory chunks
with a contact email.

Patterns covered:
- Anthropic / OpenAI / GitHub / AWS / Google / Slack / Stripe API-key shapes
- Authorization header bearer tokens
- Explicit `password=`, `secret=`, `token=`, `api_key=` key/value assignments
- Private RSA/SSH key headers
- Email addresses
- Home-directory usernames in `/Users/<name>/` and `/home/<name>/`

Not covered (deliberately, to avoid noise on legitimate text):
- IP addresses
- Phone numbers
- Credit-card numbers
"""

from __future__ import annotations

import re

# Each entry: (compiled_pattern, replacement).
# Replacements are short tokens; the model sees *that something was here* but
# not what. Keeping them short minimises token cost.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # --- private keys (must run before email catch-all) ------------------
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"), "<REDACTED_PRIVATE_KEY>"),

    # --- API-key shapes ---------------------------------------------------
    # Anthropic
    (re.compile(r"sk-ant-[a-zA-Z0-9_\-]{20,}"), "<REDACTED_ANTHROPIC_KEY>"),
    # OpenAI (sk-proj-*, sk-svcacct-*, legacy sk-*)
    (re.compile(r"sk-(?:proj|svcacct)-[a-zA-Z0-9_\-]{20,}"), "<REDACTED_OPENAI_KEY>"),
    (re.compile(r"\bsk-[a-zA-Z0-9]{32,}\b"), "<REDACTED_OPENAI_KEY>"),
    # GitHub personal / app / install / refresh tokens
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "<REDACTED_GITHUB_TOKEN>"),
    # AWS access key id
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "<REDACTED_AWS_KEY_ID>"),
    # Google API key
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "<REDACTED_GOOGLE_KEY>"),
    # Slack bot / user / app tokens
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), "<REDACTED_SLACK_TOKEN>"),
    # Stripe live / test keys
    (re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{24,}\b"), "<REDACTED_STRIPE_KEY>"),
    # JWT-shaped tokens
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), "<REDACTED_JWT>"),

    # --- Authorization headers -------------------------------------------
    (re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9\.\-_]{10,}"), r"\1<REDACTED_BEARER>"),

    # --- Explicit password/secret/token/api_key assignments --------------
    # Matches `password = "abc"`, `SECRET='abc'`, `token=abc123` etc.
    (re.compile(r"""(?ix)
        \b(
            password | passwd | pwd |
            secret | api_?key | token
        )
        \s* [:=] \s*
        ['"]?
        ([A-Za-z0-9_\-\.\+/=]{6,})
        ['"]?
    """), r"\1=<REDACTED>"),

    # --- Email addresses --------------------------------------------------
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "<REDACTED_EMAIL>"),

    # --- Home-directory usernames ----------------------------------------
    (re.compile(r"/Users/([^/\s]+)"), "/Users/<user>"),
    (re.compile(r"/home/([^/\s]+)"), "/home/<user>"),
]


def redact(text: str) -> str:
    """Return a copy of *text* with known secret / PII shapes replaced.

    Safe to call with ``None`` or empty strings.
    """
    if not text:
        return text
    redacted = text
    for pattern, replacement in _PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def count_redactions(original: str, redacted: str) -> int:
    """Best-effort estimate of how many replacements happened.

    Counts occurrences of ``<REDACTED`` in the redacted text minus any that
    existed in the original. Useful for logging.
    """
    before = original.count("<REDACTED")
    after = redacted.count("<REDACTED")
    return max(0, after - before)
