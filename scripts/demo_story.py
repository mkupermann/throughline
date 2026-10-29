"""Fictional, source-linked project journey added to the full demo corpus."""

from datetime import datetime, timedelta
from uuid import uuid4

from psycopg2.extras import Json

from throughline.api.routers.story import Checkpoint
from throughline.queries.story import checkpoint


def seed_project_story(conn):
    with conn.cursor() as cur:
        sessions = [
            (
                "2026-08-10T09:00:00Z",
                "claude_code",
                "Define the question and evaluation set",
                "We want to improve document search without losing source attribution.",
                "Use 120 fictional documents. Compare keyword search against retrieval with reranking. Keep the test queries fixed.",
            ),
            (
                "2026-08-12T11:00:00Z",
                "codex",
                "First comparison: reranking looks promising",
                "Run the initial benchmark with the fixed questions.",
                "Reranking improves top-5 retrieval on this sample. This is provisional: the long-document subset is not yet evaluated.",
            ),
            (
                "2026-08-14T08:30:00Z",
                "vibe",
                "Counterexample: long documents lose their sources",
                "Check the long-document subset and repeat the evaluation.",
                "The earlier result does not hold for long documents. The chunk boundary removes the evidence span. Retain keyword search as the baseline.",
            ),
            (
                "2026-09-07T09:15:00Z",
                "codex",
                "Resume: preserve evidence spans before rerunning",
                "Where did we stop? What needs to happen next?",
                "Keep the keyword baseline. First repair the chunk boundaries; then rerun the same 120-document evaluation. Open question: what overlap preserves the complete evidence span?",
            ),
        ]
        ids = []
        messages = []
        for ts, tool, title, question, answer in sessions:
            start = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            cur.execute(
                """INSERT INTO conversations (session_id,project_path,started_at,ended_at,source_tool,model,summary,message_count)
              VALUES (%s,%s,%s,%s,%s,%s,%s,2) RETURNING id""",
                (
                    str(uuid4()),
                    "/fictional/Atlas (demo)",
                    start,
                    start + timedelta(minutes=8),
                    tool,
                    "example-model",
                    title,
                ),
            )
            cid = cur.fetchone()[0]
            ids.append(cid)
            for role, content in [("user", question), ("assistant", answer)]:
                cur.execute(
                    "INSERT INTO messages (conversation_id,uuid,role,content,created_at,model) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                    (
                        cid,
                        str(uuid4()),
                        role,
                        content,
                        start + timedelta(minutes=3 if role == "assistant" else 0),
                        "example-model" if role == "assistant" else None,
                    ),
                )
                mid = cur.fetchone()[0]
            messages.append(mid)
        cur.execute(
            "INSERT INTO projects (name,description) VALUES ('Atlas (demo)','Fictional example. No private conversation data.')"
        )
        cur.execute(
            "INSERT INTO memory_chunks(source_type,source_id,content,category,status) VALUES ('conversation',%s,'Reranking is promising on the initial sample. Long-document coverage is still unknown.','insight','superseded') RETURNING id",
            (ids[1],),
        )
        old = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO memory_chunks(source_type,source_id,content,category) VALUES ('conversation',%s,'Keep keyword search as the baseline until evidence spans are preserved.','decision') RETURNING id",
            (ids[2],),
        )
        new = cur.fetchone()[0]
        cur.execute("UPDATE memory_chunks SET superseded_by=%s WHERE id=%s", (new, old))
    conn.commit()
    for kind, text, i in [
        ("goal", "Improve document search while keeping every result traceable to its evidence.", 0),
        ("status", "Reranking looks promising in the first comparison; long documents are still untested.", 1),
        ("status", "The long-document test overturned the initial result. Keyword search remains the baseline.", 2),
        (
            "blocker",
            "Chunk boundaries cut off evidence spans in long documents. The required overlap is still unresolved.",
            3,
        ),
        ("next", "Repair chunk boundaries, then rerun the same evaluation on all 120 documents.", 3),
    ]:
        result = checkpoint(
            conn, "Atlas (demo)", Checkpoint(kind=kind, content=text, conversation_id=ids[i], message_id=messages[i])
        )
        with conn.cursor() as cur:
            cur.execute("UPDATE project_checkpoints SET created_at=%s WHERE id=%s", (sessions[i][0], result["id"]))
        conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO messages (conversation_id, uuid, role, content, created_at, content_blocks)
          VALUES (%s,%s,'tool_result','Fictional evaluation: 18 of 40 long-document queries lost the source span. Keyword baseline retained.',
          '2026-08-14T08:34:17Z',%s)""",
            (
                ids[2],
                str(uuid4()),
                Json([{"type": "output_file", "path": "docs/demo/atlas/evaluation.md", "filename": "evaluation.md"}]),
            ),
        )
        cur.execute("UPDATE conversations SET message_count=3 WHERE id=%s", (ids[2],))
    conn.commit()
