"""Tests for the PII / secret redaction helpers."""

from __future__ import annotations

from throughline.pii import count_redactions, redact


def test_empty_and_none_are_safe() -> None:
    assert redact("") == ""
    assert redact(None) is None  # type: ignore[arg-type]


def test_anthropic_api_key() -> None:
    text = "Use sk-ant-api03-abcDEF123456789012345XYZ_abc as the key"
    out = redact(text)
    assert "sk-ant-api03" not in out
    assert "<REDACTED_ANTHROPIC_KEY>" in out


def test_openai_api_key_legacy() -> None:
    text = "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz0123456789ABCD"
    out = redact(text)
    assert "sk-abcdef" not in out
    assert "<REDACTED_OPENAI_KEY>" in out or "<REDACTED>" in out


def test_openai_api_key_project() -> None:
    text = "token: sk-proj-abcdefghijklmnopqrstuvwx1234567890"
    out = redact(text)
    assert "sk-proj-abc" not in out


def test_github_personal_access_token() -> None:
    text = "The token is ghp_1234567890abcdefghijklmnopqrstuvwxyz and it leaked"
    out = redact(text)
    assert "ghp_1234567890" not in out
    assert "<REDACTED_GITHUB_TOKEN>" in out


def test_aws_access_key_id() -> None:
    text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
    out = redact(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in out


def test_bearer_token_in_authorization_header() -> None:
    text = "curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature'"
    out = redact(text)
    assert "eyJhbGciOiJIUzI1NiJ9" not in out
    assert "<REDACTED" in out


def test_password_assignment_single_quoted() -> None:
    text = "password='hunter2xyz'"
    out = redact(text)
    assert "hunter2xyz" not in out
    assert "password=<REDACTED>" in out


def test_api_key_assignment_environment_style() -> None:
    text = "API_KEY=abcd1234efgh5678ijkl9012"
    out = redact(text)
    assert "abcd1234efgh" not in out


def test_private_ssh_key_block() -> None:
    text = (
        "Here is the key:\n"
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAAB\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
        "Please store it somewhere safe."
    )
    out = redact(text)
    assert "BEGIN OPENSSH PRIVATE KEY" not in out
    assert "<REDACTED_PRIVATE_KEY>" in out


def test_email_is_redacted() -> None:
    text = "Reach me at jane.doe@example.com about this"
    out = redact(text)
    assert "jane.doe@example.com" not in out
    assert "<REDACTED_EMAIL>" in out


def test_macos_username_in_path() -> None:
    text = "open /Users/alice/Documents/secret.txt in Finder"
    out = redact(text)
    assert "/Users/alice" not in out
    assert "/Users/<user>" in out


def test_linux_home_username_in_path() -> None:
    text = "the script lives at /home/bob/bin/deploy.sh"
    out = redact(text)
    assert "/home/bob" not in out
    assert "/home/<user>" in out


def test_does_not_destroy_legitimate_text() -> None:
    # Normal prose, package names, short identifiers — should all survive.
    text = "I used pgvector 0.8 with HNSW indexes. Decision: stick with it."
    out = redact(text)
    assert out == text


def test_does_not_redact_short_sk_prefixed_words() -> None:
    # "sk-" followed by too few chars or whitespace should not trigger.
    # e.g. "skin", "skull", "sk-" as a hyphenation artifact.
    text = "skin-deep and sk-something short"
    out = redact(text)
    assert out == text


def test_multiple_redactions_in_one_string() -> None:
    text = (
        "email: alice@example.com, key: sk-ant-api03-" + "x" * 40 +
        ", path: /Users/alice/work"
    )
    out = redact(text)
    assert "alice@example.com" not in out
    assert "sk-ant-api03" not in out
    assert "/Users/alice" not in out


def test_count_redactions() -> None:
    text = "email: alice@example.com and bob@example.org"
    out = redact(text)
    assert count_redactions(text, out) == 2


def test_idempotent() -> None:
    # Running redact twice should not change anything after the first pass.
    text = "contact: admin@example.com key: ghp_" + "a" * 40
    once = redact(text)
    twice = redact(once)
    assert once == twice
