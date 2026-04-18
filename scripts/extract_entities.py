#!/usr/bin/env python3
"""
Entity-Extraction Pipeline via Claude Code CLI.
Analysiert Conversations und extrahiert Entities + Relationships als Knowledge Graph.
"""

import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import argparse
from typing import Any

import psycopg2
import psycopg2.extras

try:
    from throughline.pii import count_redactions, redact
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from throughline.pii import count_redactions, redact

DB_CONFIG: dict[str, Any] = {
    "dbname": os.environ.get("PGDATABASE", "claude_memory"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", "5432")),
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 5,
}

REDACT_PII: bool = os.environ.get("THROUGHLINE_REDACT_PII", "1") != "0"


def _connect() -> "psycopg2.extensions.connection":
    """Connect to PostgreSQL with a friendly error if the DB is unreachable."""
    try:
        return psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        sys.stderr.write(
            f"ERROR: Cannot connect to PostgreSQL at "
            f"{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}.\n"
            f"  Is it running? Try: docker compose up -d\n"
            f"  Or: brew services start postgresql@16\n"
            f"  Underlying error: {e}\n"
        )
        raise SystemExit(2) from e


def _resolve_claude_bin() -> str:
    env = os.environ.get("CLAUDE_BIN")
    if env:
        return env
    from shutil import which
    found = which("claude")
    return found or "claude"


CLAUDE_BIN = _resolve_claude_bin()
MODEL = "sonnet"
MAX_CONVERSATIONS_PER_RUN = 100
MIN_MESSAGES = 3
MAX_TRANSCRIPT_CHARS = 40000
SLEEP_BETWEEN_CALLS = 1.5
TIMEOUT_PER_CALL = 180

ALLOWED_ENTITY_TYPES = {"person", "project", "technology", "decision", "concept", "organization"}
ALLOWED_REL_TYPES = {"works_on", "uses", "decided", "blocks", "relates_to", "member_of",
                      "reports_to", "depends_on", "replaces"}

PROMPT_TEMPLATE = """Du analysierst ein Session-Transcript und extrahierst strukturierte Entitäten + Beziehungen als JSON.

Erlaubte Entity-Types: person, project, technology, decision, concept, organization
Erlaubte Relation-Types: works_on, uses, decided, blocks, relates_to, member_of, reports_to, depends_on, replaces

Regeln:
- Nur konkrete, identifizierbare Entitäten (keine generischen Begriffe wie "user", "code", "file")
- Personen: echte Namen (nicht "Assistant", "User")
- Projekte: konkrete benannte Projekte (z.B. "Project Alpha 2026", "claude-memory")
- Technologien: konkrete Tools/Libs/Versionen (z.B. "PostgreSQL 16", "pgvector", "Streamlit")
- Decisions: konkret getroffene Entscheidungen
- Relationships: nur wenn klar aus Transcript ableitbar
- confidence: 0.6-1.0 (höher bei eindeutigen Fakten)

Output: REINES JSON (keine Markdown-Fences, kein Erklärtext):
{
  "entities": [
    {"type": "person", "name": "Jane Doe", "attributes": {"role": "Migration Lead", "organization": "Acme Corp"}},
    {"type": "project", "name": "Project Alpha 2026", "attributes": {"status": "active", "release": "Summer Release Q2/2026"}},
    {"type": "technology", "name": "PostgreSQL 16", "attributes": {"version": "16"}}
  ],
  "relationships": [
    {"from": "Jane Doe", "to": "Project Alpha 2026", "type": "works_on", "confidence": 0.9},
    {"from": "Project Alpha 2026", "to": "PostgreSQL 16", "type": "uses", "confidence": 0.8}
  ]
}

Falls nichts Verwertbares: {"entities": [], "relationships": []}

Transcript:

{TRANSCRIPT}

Gib NUR das JSON-Objekt zurück, nichts anderes."""


def canonicalize(name: str) -> str:
    """Normalisiere Namen: lowercase, ohne Akzente, trim."""
    if not name:
        return ""
    # Unicode-Normalisierung + Akzente entfernen
    nfkd = unicodedata.normalize("NFKD", name)
    no_accent = "".join([c for c in nfkd if not unicodedata.combining(c)])
    # Whitespace-Collapsing, lowercase
    cleaned = re.sub(r"\s+", " ", no_accent).strip().lower()
    return cleaned


def build_transcript(messages: list) -> str:
    parts = []
    for m in messages:
        role = m[0]
        content = m[1] or ""
        if role == "tool_result":
            continue
        if len(content) > 2000:
            content = content[:2000] + "...[gekürzt]"
        parts.append(f"[{role.upper()}]\n{content}\n")
    transcript = "\n".join(parts)
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = transcript[-MAX_TRANSCRIPT_CHARS:]
    return transcript


def build_context_snippet(messages: list, entity_name: str, max_len: int = 300) -> str:
    """Baue einen kurzen Kontext-Auszug wo die Entity erwähnt wurde."""
    if not entity_name:
        return ""
    pattern = re.compile(re.escape(entity_name), re.IGNORECASE)
    for m in messages:
        content = m[1] or ""
        if pattern.search(content):
            match = pattern.search(content)
            start = max(0, match.start() - 80)
            end = min(len(content), match.end() + 150)
            snippet = content[start:end].strip()
            if len(snippet) > max_len:
                snippet = snippet[:max_len] + "..."
            return snippet
    return ""


def parse_json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return {"entities": [], "relationships": []}
    try:
        parsed = json.loads(text[start:end+1])
        if not isinstance(parsed, dict):
            return {"entities": [], "relationships": []}
        parsed.setdefault("entities", [])
        parsed.setdefault("relationships", [])
        return parsed
    except json.JSONDecodeError as e:
        print(f"    JSON parse error: {e}")
        return {"entities": [], "relationships": []}


def call_claude(prompt: str) -> str:
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt, "--model", MODEL],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_PER_CALL,
        )
        if result.returncode != 0:
            print(f"    Claude CLI error (exit {result.returncode}): {result.stderr[:200]}")
            return ""
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        print(f"    Claude CLI timeout ({TIMEOUT_PER_CALL}s)")
        return ""
    except Exception as e:
        print(f"    Claude CLI exception: {e}")
        return ""


def upsert_entity(cursor, entity_type: str, name: str, attributes: dict,
                   project_name: str | None, confidence: float) -> int | None:
    """Insert or update entity. Gibt entity_id zurück."""
    canonical = canonicalize(name)
    if not canonical or entity_type not in ALLOWED_ENTITY_TYPES:
        return None

    # Try insert, on conflict update (bump mention_count, merge attributes, update last_seen)
    cursor.execute("""
        INSERT INTO entities (entity_type, name, canonical_name, attributes, project_name, confidence)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (entity_type, canonical_name, project_name)
        DO UPDATE SET
            mention_count = entities.mention_count + 1,
            last_seen = now(),
            attributes = entities.attributes || EXCLUDED.attributes,
            confidence = GREATEST(entities.confidence, EXCLUDED.confidence)
        RETURNING id
    """, (entity_type, name, canonical, json.dumps(attributes or {}),
          project_name, confidence))
    row = cursor.fetchone()
    return row[0] if row else None


def insert_relationship(cursor, from_id: int, to_id: int, relation_type: str,
                         confidence: float, source_type: str, source_id: int,
                         attributes: dict) -> bool:
    if relation_type not in ALLOWED_REL_TYPES:
        return False
    if from_id == to_id:
        return False
    # Deduplizieren: gleiche Relation nicht doppelt pro Source einfügen
    cursor.execute("""
        SELECT id FROM relationships
        WHERE from_entity = %s AND to_entity = %s AND relation_type = %s
          AND source_type = %s AND source_id = %s
        LIMIT 1
    """, (from_id, to_id, relation_type, source_type, source_id))
    if cursor.fetchone():
        return False

    cursor.execute("""
        INSERT INTO relationships (from_entity, to_entity, relation_type, confidence,
                                     source_type, source_id, attributes)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (from_id, to_id, relation_type, confidence, source_type, source_id,
          json.dumps(attributes or {})))
    return True


def insert_mention(cursor, entity_id: int, source_type: str, source_id: int,
                    context: str) -> None:
    cursor.execute("""
        INSERT INTO entity_mentions (entity_id, source_type, source_id, context_snippet)
        VALUES (%s, %s, %s, %s)
    """, (entity_id, source_type, source_id, context))


def extract_for_conversation(cursor, conv_id: int, project_name: str | None) -> tuple:
    cursor.execute("""
        SELECT role::text, content
        FROM messages
        WHERE conversation_id = %s AND role IN ('user', 'assistant')
        ORDER BY created_at
    """, (conv_id,))
    rows = cursor.fetchall()
    if not rows:
        return (0, 0)

    transcript = build_transcript(rows)
    if len(transcript) < 200:
        return (0, 0)

    if REDACT_PII:
        redacted = redact(transcript)
        n = count_redactions(transcript, redacted)
        if n:
            print(f"    redacted {n} secret/PII match(es) before entity extraction")
        transcript = redacted

    prompt = PROMPT_TEMPLATE.replace("{TRANSCRIPT}", transcript)
    response = call_claude(prompt)
    if not response:
        return (0, 0)

    parsed = parse_json_response(response)
    entities = parsed.get("entities", []) or []
    relationships = parsed.get("relationships", []) or []

    # Entity-Name -> ID mapping (für Relationship-Resolution)
    name_to_id: dict[str, int] = {}
    entities_inserted = 0

    for ent in entities:
        try:
            etype = (ent.get("type") or "").strip().lower()
            name = (ent.get("name") or "").strip()
            attrs = ent.get("attributes") or {}
            conf = float(ent.get("confidence", 0.8))
            if not name or etype not in ALLOWED_ENTITY_TYPES:
                continue

            entity_id = upsert_entity(cursor, etype, name, attrs, project_name, conf)
            if entity_id:
                name_to_id[canonicalize(name)] = entity_id
                # Mention
                snippet = build_context_snippet(rows, name)
                insert_mention(cursor, entity_id, "conversation", conv_id, snippet)
                entities_inserted += 1
        except Exception as e:
            print(f"    Entity insert-Fehler: {e}")
            continue

    rels_inserted = 0
    for rel in relationships:
        try:
            from_name = (rel.get("from") or "").strip()
            to_name = (rel.get("to") or "").strip()
            rtype = (rel.get("type") or "").strip().lower()
            rconf = float(rel.get("confidence", 0.8))
            rattrs = rel.get("attributes") or {}

            from_id = name_to_id.get(canonicalize(from_name))
            to_id = name_to_id.get(canonicalize(to_name))

            # Fallback: Suche in DB falls nicht in dieser Session extrahiert
            if not from_id:
                cursor.execute("""
                    SELECT id FROM entities WHERE canonical_name = %s
                    ORDER BY (project_name = %s) DESC NULLS LAST, mention_count DESC LIMIT 1
                """, (canonicalize(from_name), project_name))
                r = cursor.fetchone()
                if r:
                    from_id = r[0]
            if not to_id:
                cursor.execute("""
                    SELECT id FROM entities WHERE canonical_name = %s
                    ORDER BY (project_name = %s) DESC NULLS LAST, mention_count DESC LIMIT 1
                """, (canonicalize(to_name), project_name))
                r = cursor.fetchone()
                if r:
                    to_id = r[0]

            if from_id and to_id and rtype in ALLOWED_REL_TYPES:
                if insert_relationship(cursor, from_id, to_id, rtype, rconf,
                                         "conversation", conv_id, rattrs):
                    rels_inserted += 1
        except Exception as e:
            print(f"    Relationship insert-Fehler: {e}")
            continue

    return (entities_inserted, rels_inserted)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=MAX_CONVERSATIONS_PER_RUN)
    ap.add_argument("--min-messages", type=int, default=MIN_MESSAGES)
    ap.add_argument("--conv-id", type=int, default=None, help="Nur diese Conversation verarbeiten")
    args = ap.parse_args()

    print("=" * 60)
    print("Claude Memory DB — Entity Extraction (Knowledge Graph)")
    print("=" * 60)

    from shutil import which
    if which(CLAUDE_BIN) is None and not os.path.isfile(CLAUDE_BIN):
        sys.stderr.write(
            "ERROR: Claude CLI not found.\n"
            "  Set $CLAUDE_BIN or install the Claude Code CLI:\n"
            "    https://docs.anthropic.com/en/docs/claude-code/setup\n"
        )
        raise SystemExit(2)

    conn = _connect()
    cursor = conn.cursor()

    if args.conv_id:
        cursor.execute("""
            SELECT id, project_name, message_count FROM conversations WHERE id = %s
        """, (args.conv_id,))
    else:
        cursor.execute("""
            SELECT c.id, c.project_name, c.message_count
            FROM conversations c
            WHERE NOT EXISTS (
                SELECT 1 FROM entity_mentions em
                WHERE em.source_type = 'conversation' AND em.source_id = c.id
            )
            AND c.message_count >= %s
            ORDER BY c.started_at DESC
            LIMIT %s
        """, (args.min_messages, args.limit))
    convs = cursor.fetchall()

    print(f"\n{len(convs)} Conversations zu analysieren\n")
    if not convs:
        print("Nichts zu tun.")
        return

    total_entities = 0
    total_rels = 0
    errors = 0
    start = time.time()

    for i, (conv_id, project_name, msg_count) in enumerate(convs, 1):
        elapsed = time.time() - start
        print(f"  [{i}/{len(convs)}] #{conv_id} ({project_name or '–'}, {msg_count} Msgs) [{elapsed:.0f}s elapsed]", end=" ", flush=True)
        try:
            ents, rels = extract_for_conversation(cursor, conv_id, project_name)
            conn.commit()
            total_entities += ents
            total_rels += rels
            print(f"→ {ents} Entities, {rels} Rels", flush=True)
        except (psycopg2.InterfaceError, psycopg2.OperationalError) as e:
            # Connection lost — reconnect and retry once
            print(f"⚠ Connection lost ({e}); reconnecting...", flush=True)
            try:
                conn.close()
            except Exception:
                pass
            conn = psycopg2.connect(**DB_CONFIG)
            cursor = conn.cursor()
            errors += 1
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            errors += 1
            print(f"✗ {e}", flush=True)
        time.sleep(SLEEP_BETWEEN_CALLS)

    print(f"\n{'=' * 60}")
    print(f"Analysiert: {len(convs)} | Entities: {total_entities} | Relationships: {total_rels} | Fehler: {errors}")
    print(f"{'=' * 60}")

    # Gesamt-Stats
    cursor.execute("SELECT count(*) FROM entities")
    total_e = cursor.fetchone()[0]
    cursor.execute("SELECT count(*) FROM relationships")
    total_r = cursor.fetchone()[0]
    cursor.execute("SELECT count(*) FROM entity_mentions")
    total_m = cursor.fetchone()[0]
    print(f"DB gesamt: {total_e} Entities | {total_r} Relationships | {total_m} Mentions")

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()
