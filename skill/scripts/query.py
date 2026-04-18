#!/usr/bin/env python3
"""Claude Memory — query CLI for the memory DB."""

import sys
import os
import psycopg2
from psycopg2.extras import RealDictCursor

DB = {
    "dbname": os.environ.get("PGDATABASE", "claude_memory"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", "5432")),
}


def get_conn():
    return psycopg2.connect(**DB)


def cmd_search(term: str):
    """Suche in memory_chunks + messages. Trackt access_count/last_accessed."""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        print(f"\n=== Memory Chunks zu '{term}' ===")
        cur.execute("""
            SELECT id, category::text, content, confidence, project_name, tags
            FROM memory_chunks
            WHERE (content ILIKE %s OR %s = ANY(tags) OR project_name ILIKE %s)
              AND COALESCE(status, 'active') = 'active'
            ORDER BY confidence DESC, created_at DESC
            LIMIT 20
        """, (f"%{term}%", term, f"%{term}%"))
        rows = cur.fetchall()
        seen_ids = [r["id"] for r in rows]
        for r in rows:
            print(f"\n[{r['category']}] Conf: {r['confidence']} | Projekt: {r['project_name'] or '-'}")
            if r['tags']:
                print(f"  Tags: {', '.join(r['tags'])}")
            print(f"  {r['content']}")
        # Access-Tracking: access_count++ und last_accessed = now() fuer gelesene Chunks
        if seen_ids:
            try:
                cur.execute("""
                    UPDATE memory_chunks
                    SET access_count = COALESCE(access_count, 0) + 1,
                        last_accessed = now()
                    WHERE id = ANY(%s)
                """, (seen_ids,))
                conn.commit()
            except Exception as e:
                # Spalten evtl. noch nicht migriert — ignorieren
                conn.rollback()

        print(f"\n=== Conversations mit '{term}' ===")
        cur.execute("""
            SELECT DISTINCT c.id, c.project_name, c.started_at
            FROM conversations c
            JOIN messages m ON m.conversation_id = c.id
            WHERE m.content ILIKE %s
            ORDER BY c.started_at DESC
            LIMIT 10
        """, (f"%{term}%",))
        for r in cur.fetchall():
            print(f"  #{r['id']} | {r['project_name'] or '-'} | {r['started_at']}")


def cmd_project(name: str):
    """Alle Memory-Chunks eines Projekts."""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        print(f"\n=== Projekt: {name} ===")
        cur.execute("""
            SELECT category::text, content, confidence, tags
            FROM memory_chunks
            WHERE project_name ILIKE %s
            ORDER BY category, confidence DESC
        """, (f"%{name}%",))
        current_cat = None
        for r in cur.fetchall():
            if r['category'] != current_cat:
                current_cat = r['category']
                print(f"\n--- {current_cat.upper()} ---")
            print(f"  [{r['confidence']}] {r['content']}")


def cmd_contact(name: str):
    """Kontakt-Einträge."""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        print(f"\n=== Kontakt: {name} ===")
        cur.execute("""
            SELECT content, confidence, project_name, tags
            FROM memory_chunks
            WHERE category = 'contact' AND content ILIKE %s
            ORDER BY confidence DESC
        """, (f"%{name}%",))
        for r in cur.fetchall():
            print(f"\n  [{r['confidence']}] {r['content']}")
            if r['project_name']:
                print(f"  Projekt: {r['project_name']}")


def cmd_decisions():
    """Alle Entscheidungen chronologisch."""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        print("\n=== Alle Entscheidungen ===")
        cur.execute("""
            SELECT content, project_name, created_at, tags
            FROM memory_chunks
            WHERE category = 'decision'
            ORDER BY created_at DESC
            LIMIT 50
        """)
        for r in cur.fetchall():
            print(f"\n  {r['created_at'].strftime('%Y-%m-%d')} | {r['project_name'] or '-'}")
            print(f"  {r['content']}")


def cmd_stats():
    """Statistiken."""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                (SELECT count(*) FROM conversations) AS conv,
                (SELECT count(*) FROM messages) AS msg,
                (SELECT count(*) FROM memory_chunks) AS mem,
                (SELECT count(*) FROM skills) AS sk,
                (SELECT count(*) FROM projects) AS pr,
                (SELECT count(*) FROM prompts) AS pt
        """)
        r = cur.fetchone()
        print(f"\n=== Claude Memory Stats ===")
        print(f"  Conversations: {r['conv']}")
        print(f"  Messages:      {r['msg']}")
        print(f"  Memory Chunks: {r['mem']}")
        print(f"  Skills:        {r['sk']}")
        print(f"  Projects:      {r['pr']}")
        print(f"  Prompts:       {r['pt']}")

        cur.execute("SELECT category::text, count(*) FROM memory_chunks GROUP BY category ORDER BY count DESC")
        print(f"\n  Memory nach Kategorie:")
        for row in cur.fetchall():
            print(f"    {row['category']:20} {row['count']}")


def usage():
    print("""
Claude Memory — Query CLI

Usage:
  query.py search <term>      Volltextsuche in Memory + Conversations
  query.py project <name>     Alle Erkenntnisse zu einem Projekt
  query.py contact <name>     Kontakt-Einträge
  query.py decisions          Alle Entscheidungen chronologisch
  query.py stats              DB-Statistiken
""")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        usage(); sys.exit(1)
    cmd = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    if cmd == "search" and arg: cmd_search(arg)
    elif cmd == "project" and arg: cmd_project(arg)
    elif cmd == "contact" and arg: cmd_contact(arg)
    elif cmd == "decisions": cmd_decisions()
    elif cmd == "stats": cmd_stats()
    else: usage()
