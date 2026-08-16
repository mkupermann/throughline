# Schema migrations

Throughline tracks schema changes as a flat, ordered list of numbered SQL files.
Each file is applied exactly once, recorded in the `applied_migrations` table.

## File naming

```
NNN_short_description.sql
```

- `NNN` — zero-padded sequential number (`000`, `001`, `002`, ...). New migrations always
  get the next free number; never reuse or re-order. The runner rejects duplicate ordinals.
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
- **The runner owns the transaction.** Migration files must not contain `BEGIN`,
  `COMMIT`, `ROLLBACK`, or other transaction-control statements. A migration and
  its `applied_migrations` record are committed together; work that cannot run
  inside a transaction (`CREATE INDEX CONCURRENTLY`, for example) needs an
  explicit runner capability before it can be introduced.
- **Idempotent helpers are nice** but not required — each migration only runs once.
- **Never edit an already-applied migration.** Add a new one that patches the state.

## Applying

```bash
throughline migrate              # apply all pending migrations
throughline migrate --status     # list which migrations are applied
throughline migrate --dry-run    # show what would run, without running it
```

The migration SQL is package data. An installed wheel therefore has the same
migration set as a source checkout; `scripts/migrate.py` remains only as a
direct-execution compatibility wrapper. Docker Compose runs `throughline
migrate` automatically after PostgreSQL is ready and waits for it before it
starts the web or MCP service. Native installations run the command after
creating the database and after each upgrade.

The runner:

1. Ensures `applied_migrations` exists.
2. Validates every file matching `throughline/migrations/NNN_*.sql` and applies it in ordinal order.
3. For each file not in `applied_migrations`, runs it in a single transaction and records
   the name on success.

The historical duplicate `001_widen_conversation_token_counts.sql` was
renumbered to `005_widen_conversation_token_counts.sql`. Databases that already
recorded the former name treat `005` as applied; the runner never rewrites or
deletes migration history.

## Baseline

`000_baseline.sql` is the schema captured at release `0.1.0-beta`. On a fresh
database, `throughline migrate` applies it and every later migration. Do not
initialize a new installation from `sql/schema.sql`; it exists for schema
inspection and CI validation, while the migration runner is the installation
and upgrade path.

If an older installation was created from `sql/schema.sql`, simply run
`throughline migrate`. It detects the existing schema, records the baseline,
and then applies later migrations. Use `throughline migrate --dry-run` first
if you want to inspect that plan without changing the database. Manual edits
to `applied_migrations` are unnecessary.
