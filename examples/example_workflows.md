# Example Workflows

Step-by-step guides for the most common ways people use Claude Memory.
Each workflow can be completed in under two minutes once the system is running.

---

## 1. Starting a new project session — "catch me up"

**Situation:** You haven't worked on `acme-web` in two weeks and are about to
pick up a feature. You want all relevant decisions, contacts and past gotchas
before you type a single line of code.

### Steps

1. Open a Claude Code session in the project directory.

   ```
   cd ~/projects/acme-web
   claude
   ```

2. Invoke the context-loader skill.

   ```
   load context
   ```

   The skill infers the project name from the working directory and returns a
   structured summary: decisions, team contacts, known patterns and any open
   error solutions.

3. Read the summary, ask follow-up questions if needed.

   ```
   What was the rationale for using httpOnly cookies instead of localStorage?
   ```

4. Start coding. Claude now has the context loaded and will reference it
   throughout the session.

**What happens in the background:** The skill queries `memory_chunks`,
`projects` and `entities` filtered to `acme-web`, formats the results and
injects them into the conversation context.

---

## 2. Finding past decisions about a technology

**Situation:** You are evaluating whether to introduce Redis caching into
`fintech-api`. You want to know if this was discussed before, and what was
decided.

### Steps

1. Search memory from Claude Code.

   ```
   What decisions have we made about caching in fintech-api?
   ```

   The memory skill runs a full-text search over `memory_chunks` filtered by
   project and category = `decision`.

2. Alternatively, run the query directly in psql or the Streamlit SQL console.

   ```sql
   SELECT content, confidence, created_at::date
   FROM public.memory_chunks
   WHERE project_name = 'fintech-api'
     AND category     = 'decision'
     AND content ILIKE '%cache%'
     AND status       = 'active'
   ORDER BY created_at DESC;
   ```

3. If there is a past decision, Claude surfaces it with the original rationale.
   If there is no prior decision, that tells you this is genuinely new ground —
   proceed with confidence that you are not re-inventing a wheel that was
   already rejected.

4. After you make the decision in the current session, it will be extracted
   automatically by the nightly ingest + extract pipeline, or you can log it
   manually:

   ```
   Log decision: We chose Redis for rate-limiting state in fintech-api
   because it is already in the infrastructure and supports atomic INCR.
   Rejected Postgres because it would add write pressure to the primary.
   ```

---

## 3. Understanding how the scheduler works

**Situation:** You want to know when sessions are ingested and how often memory
extraction runs. You are new to the project.

### Steps

1. Check the launchd configuration files in the `launchd/` directory of the
   repository. Each plist file corresponds to one scheduled job.

   ```
   ls launchd/
   ```

2. The three main jobs are:

   | Job | Schedule | What it does |
   |-----|----------|--------------|
   | `ingest_sessions` | Every 15 minutes | Scans `~/.claude/projects/` for new JSONL session files and imports them into `conversations` + `messages`. |
   | `extract_memory` | Every 6 hours | Sends recent conversations to Claude Sonnet, extracts structured memory chunks, stores them in `memory_chunks`. |
   | `backup` | Nightly at 03:00 | `pg_dump` of the full database, compressed, rotated to keep the last 14 days. |

3. To check the status of any job:

   ```bash
   launchctl list | grep claude-memory
   ```

4. To trigger ingest manually (useful after a long session):

   ```bash
   python3 scripts/ingest_sessions.py --once
   ```

5. To trigger memory extraction on demand:

   ```bash
   python3 scripts/extract_memory.py --limit 5
   ```

   This processes the 5 most recent un-extracted conversations.

---

## 4. Adding memory manually

**Situation:** You just had a phone call with a client contact (nothing was in
a Claude Code session) and want to save what you learned to the memory database.

### Steps

**Option A — From Claude Code (natural language)**

```
Add a contact memory chunk:
  Name: Sam Johansson
  Project: project-aurora
  Role: Platform Lead at Team Neptune
  Notes: Prefers async communication via Slack. Owns the Trino cluster.
  Expertise: Iceberg, Spark, cost optimisation on object storage.
```

The memory skill translates this into an `INSERT` on `memory_chunks` with
`category = 'contact'` and `source_type = 'manual'`.

**Option B — Direct SQL**

```sql
INSERT INTO public.memory_chunks
  (source_type, content, category, tags, confidence, project_name, status)
VALUES
  ('manual',
   'Sam Johansson (sam@teamn.example) — Platform Lead, Team Neptune / project-aurora.
    Owns the Trino cluster. Prefers async Slack over email.
    Deep expertise in Iceberg and object-storage cost optimisation.',
   'contact',
   ARRAY['project-aurora','team-neptune','contact','trino'],
   0.90,
   'project-aurora',
   'active');
```

**Option C — Streamlit GUI**

Open the GUI (`streamlit run gui/app.py`), navigate to "Memory Chunks", click
"Add chunk", fill in the form and save. The GUI writes the same SQL as Option B.

### Tips

- Use `confidence` values to indicate how certain you are: `0.95+` for confirmed
  facts, `0.80-0.90` for things you are reasonably sure about, below `0.80` for
  things that need verification.
- Set `expires_at` for time-sensitive facts (e.g. a contact's interim role, a
  temporary architecture decision during a migration).

---

## 5. Exporting memory to Markdown

**Situation:** You want a human-readable snapshot of everything the system knows
about `fintech-api` — for a handoff document, a project retrospective, or to
share with a colleague who doesn't have database access.

### Steps

1. Use the CLI export script.

   ```bash
   python3 scripts/export_memory.py --project fintech-api --format markdown \
       --output ~/Desktop/fintech-api-memory-$(date +%Y-%m-%d).md
   ```

2. The output file is structured as:

   ```markdown
   # Memory Export: fintech-api
   Generated: 2026-04-18

   ## Decisions
   - **2026-01-21** — Encrypt PII at application layer …
   - **2026-02-10** — Rate-limit /reconcile to 10 req/min …

   ## Team contacts
   - **Alex Kim** (alex.kim@widget.example) — Engineering Manager
   - **Priya Nair** (priya.nair@widget.example) — Security Auditor

   ## Patterns
   - N+1 fix: always bulk-fetch with ANY($1) …

   ## Error solutions
   - SSL connection drop: set keepalives_idle=30 …
   ```

3. All sections are present even if empty, making it easy to spot gaps in
   coverage.

4. To export **all projects** to separate files:

   ```bash
   python3 scripts/export_memory.py --all --format markdown --output-dir ~/Desktop/memory-export/
   ```

5. For JSON export (e.g. to import into another tool or share via API):

   ```bash
   python3 scripts/export_memory.py --project fintech-api --format json \
       --output fintech-api-memory.json
   ```
