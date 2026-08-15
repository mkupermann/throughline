# Schema migrations

Throughline tracks schema changes as a flat, ordered list of numbered SQL files.
Each file is applied exactly once, recorded in the `applied_migrations` table.

## File naming

```
NNN_short_description.sql
```

- `NNN` — zero-padded sequential number (`000`, `001`, `002`, ...). New migrations always
  get the next free number; never reuse or re-order.
- `short_description` — lowercase, underscore-separated, terse but meaningful
  (`add_project_priorities`, `drop_legacy_mentions_index`, ...).
- `.sql` — always plain SQL (no templating, no `psql` meta-commands that require a shell).

Examples:

```
000_baseline.sql
001_add_project_priorities.sql
002_add_hnsw_index_on_memory_chunks.sql
```

## Authoring rules

- **Additive first.** Prefer `CREATE TABLE ... IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`,
  `CREATE INDEX IF NOT EXISTS`. Destructive changes (`DROP COLUMN`, `DROP TABLE`) need a
  migration of their own and a note in `CHANGELOG.md`.
- **One logical change per migration.** If you are tempted to add a second, start a new file.
- **Wrap in a transaction** unless you need something that cannot run inside one
  (`CREATE INDEX CONCURRENTLY`, for example). `scripts/migrate.py` will apply the file as-is;
  add `BEGIN;` / `COMMIT;` explicitly if you want transactional safety.
- **Idempotent helpers are nice** but not required — each migration only runs once.
- **Never edit an already-applied migration.** Add a new one that patches the state.

## Applying

```bash
python3 scripts/migrate.py              # apply all pending migrations
python3 scripts/migrate.py --status     # list which migrations are applied
python3 scripts/migrate.py --dry-run    # show what would run, without running it
```

The runner:

1. Ensures `applied_migrations` exists.
2. Lists every file matching `sql/migrations/*.sql` in lexicographic order.
3. For each file not in `applied_migrations`, runs it in a single transaction and records
   the name on success.

## Baseline

`000_baseline.sql` is the schema captured at release `0.1.0-beta`. On a fresh database,
running `python3 scripts/migrate.py` is equivalent to running
`psql -f sql/schema.sql` and then applying every later migration.

If you already created your database from `sql/schema.sql` directly, mark the baseline as
applied without re-running it:

```bash
psql -d claude_memory -c "
  CREATE TABLE IF NOT EXISTS applied_migrations (
      migration_name TEXT PRIMARY KEY,
      applied_at TIMESTAMPTZ DEFAULT now()
  );
  INSERT INTO applied_migrations (migration_name) VALUES ('000_baseline.sql')
  ON CONFLICT DO NOTHING;
"
```
