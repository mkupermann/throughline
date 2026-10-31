# Team mode

Team mode lets one team share one Throughline server and one database. People sign in, get a role, and can keep notes to themselves. It is not multi-tenancy: everyone who signs in sees the same conversations, projects and shared notes. If some data must not reach every member, give it its own installation and database.

This page covers the Rust runtime that Docker Compose starts. It replaces the earlier guide for the Python application.

## Two modes

`THROUGHLINE_AUTH_MODE` picks the mode when the server starts.

| Value | What happens |
| --- | --- |
| unset, empty or `local` | The single-user behaviour you already know. No sign-in, every request acts as the local operator with all rights. Keep the server on loopback. |
| `team` | The page shows a sign-in form. Every `/api` route except `/api/health` and `/api/login` needs a session or an account's agent key, otherwise 401. `/mcp` needs an account's agent key. |
| anything else | The server does not start: ``THROUGHLINE_AUTH_MODE is `<value>`: use local or team``. |

The server checks the database before it binds the port and refuses to start when the mode does not fit:

| Mode | Refuses to start when | Message |
| --- | --- | --- |
| `local` | the database holds at least one account (enabled or not) or one private note | `the database has <n> accounts and <m> private notes; start with THROUGHLINE_AUTH_MODE=team` |
| `team` | `THROUGHLINE_PUBLIC_URL` is missing, or uses `http` on a host that is not loopback | `team mode needs THROUGHLINE_PUBLIC_URL with https (http only on localhost, 127.x.x.x or ::1)` |
| `team` | no enabled account has the role `admin` | ``team mode needs an enabled admin: run `throughline account add <name> --role admin` `` |
| both | migration 014 has not run | ``read accounts (run `throughline migrate` first)`` |

The `local` rule is there so a team database cannot run open by mistake: a missing variable would otherwise show every private note to anyone who reaches the port. `throughline doctor` prints `auth mode: local` or `auth mode: team (<n> accounts, <m> private notes)`.

## Public URL and TLS

`THROUGHLINE_PUBLIC_URL` is the exact address people type into the browser, for example `https://throughline.example.internal`. It has to be `https`. Plain `http` is accepted only for `localhost`, `127.x.x.x` and `::1`, which is enough for a test on your own machine and nothing more.

The URL decides three things:

- The session cookie gets `Secure` when the URL is `https`.
- Writes from the browser must carry an `Origin` whose host is the URL's host.
- The server answers only to that host, loopback and the names in `THROUGHLINE_ALLOWED_HOSTS`. Any other `Host` gets 403.

The server itself does not speak TLS. Put your organisation's reverse proxy on the same host, terminate TLS there and forward to `http://127.0.0.1:8788` (the Compose port). Keep the web port and the PostgreSQL port on loopback; Compose already publishes both on `127.0.0.1` only. The proxy must pass the browser's `Origin` header through unchanged. Rate limits and request size limits at the proxy are a good idea on top of the server's own sign-in limits.

## Accounts

Accounts exist only on the server's command line. There is no account page in the browser, no sign-up and no password reset by mail. Create an admin first, because team mode does not start without one:

```bash
throughline migrate
throughline account add alice --role admin --display-name "Alice"
```

The command asks for the password twice without echo. For scripts, `--password-file <path>` reads it from a file that only its owner can read (mode 600). A password never goes into an argument. Passwords have 15 to 128 characters. Names use lower-case letters, digits and `. _ @ -`, start with a letter or digit and have at most 120 characters.

| Command | What it does |
| --- | --- |
| `account add <name> [--role viewer\|editor\|admin] [--display-name <text>] [--password-file <path>]` | Creates an account. The role defaults to `viewer`. |
| `account list` | Accounts with display name, role and enabled flag, then the active agent keys (key id, account, label). |
| `account role <name> <role>` | Changes the role and ends the account's sessions. |
| `account enable <name>` | Enables a disabled account. |
| `account disable <name>` | Disables the account, ends its sessions; its agent keys stop working. |
| `account unlock <name>` | Clears the account's failed sign-ins. |
| `account password <name> [--password-file <path>]` | Sets a new password and ends all of the account's sessions. |
| `account agent-key <name> [--label <text>]` | Creates an agent key and prints it once. |
| `account agent-key-revoke <name> <id>` | Revokes one key; the id comes from `account list`. |

The last enabled admin can be neither demoted nor disabled. In Compose, prefix the commands with `docker compose exec web`.

Whoever has the server's command line is the operator. The operator can create admins and reads everything, private notes included (see [Known limits](#known-limits)).

## Roles

Roles hang on routes, not on HTTP methods. Each role includes the one above it.

| Role | May do |
| --- | --- |
| `viewer` | Read: overview, sessions, messages, memory list and detail, search, projects, timeline, conflicts, handoff, stale digest, audit, costs, ingest status, recall and the context pack. Sign out and change the own password. Over MCP: `search`, `recall`, `project_context`, `get_note`, `conflicts`, `list_projects`, `stats`, resources and prompts. |
| `editor` | Everything a viewer may, plus: ask the model (`ask`, answer evaluation), write and pin notes, keep, forget and supersede notes it can see, set and remove stars. Over MCP also `write`, `forget`, `supersede` and `keep`. |
| `admin` | Everything an editor may, plus operations: jobs (`/api/operate`: ingest, embed, reflect, backup and the rest), `writeback` to `AGENTS.md`/`CLAUDE.md`, the merge proposal and merging similar notes, importing agent memory files, the AI configuration view (reading and writing the host's assistant files) and the CSV export of sessions. |

A call above the account's role gets 403 `forbidden_role` with the role it needs. A route that is not in the role table needs `admin`. The page hides buttons the role does not allow: an editor sees nine views (no AI configuration), a viewer eight.

An admin has no extra right on other people's private notes. Private stays private.

## Private and shared notes

- A new note is shared unless its author asks otherwise. Its owner is the account that wrote it, through the UI, `POST /api/memory` or MCP `write`.
- Only the owner can make a note private, and only when writing it: the pin dialog has a "Private: only I can see this note" box, the API and MCP `write` take `"visibility": "private"`.
- A private note is visible to its owner and nobody else, on every read path: recall (with the supersede chain and neighbours), the context pack, search, the memory list and detail, decisions, handoff, the stale digest, answer evaluation, conflicts, the timeline, merging, `ask`, and every MCP tool, resource and prompt.
- Notes from before team mode, and notes written by jobs or in local mode, have no owner. They stay shared and cannot become private.
- `keep` and `supersede` leave the existing note's owner and visibility alone. The replacement note belongs to whoever wrote it and is shared, unless it replaces the writer's own private note; then it stays private.
- Merging never mixes private and shared notes. Merged private notes stay with their owner.
- Server jobs (`reflect`, entity extraction, `writeback`, merging run as a job) see shared notes only. A private note never ends up in `AGENTS.md` or `CLAUDE.md`, and no job marks it stale or merges it. Embedding covers every note, but vectors are only reached through queries that apply the same filter.

## Stars

Stars belong to the account that set them. Two people can star the same session without seeing each other's stars. Setting and removing a star needs `editor`. Local mode keeps its stars as before, without an account.

## Agent keys

Agents (Claude Code, Codex, Cline, the VS Code extension) sign in with a key of an account, not with a password.

```bash
throughline account agent-key alice --label "laptop, Claude Code"
```

The key is printed once. The database keeps only its SHA-256. `account list` shows the key id, the account and the label, never the key. `account agent-key-revoke alice <id>` revokes it.

- A key acts as its account, with that account's role and view: on `/mcp` and as `Authorization: Bearer <key>` on any `/api` route. An editor's key writes notes owned by that editor; a viewer's key cannot write.
- A key request has no cookie, so it skips the request-token check of the browser. The role check still applies, and a foreign `Origin` or a cross-site request is still refused.
- Recall and the context pack through a key count as use, as they do with the agent key in local mode.
- Disabling the account stops all of its keys at once. A role change applies from the next call.
- The global `THROUGHLINE_AGENT_KEY` does nothing in team mode. A request that sends it gets 401, the same answer as no key at all. If the variable is set, the server says so once at start-up and `doctor` prints `note: THROUGHLINE_AGENT_KEY is ignored in team mode: each agent uses a key of its account (throughline account agent-key <name>)`.
- Keys are 64 hex characters from two random UUIDs, about 244 random bits. Key checks are not throttled; at that size guessing is not a practical attack.

Client setup is the same as in local mode ([usage guide](USAGE.md#agent-access-over-http)), with the account key in place of `THROUGHLINE_AGENT_KEY`. The VS Code extension retries a 401 on `/api` once with the stored key; its [README](../vscode/README.md#team-mode) has the details.

## Sessions and sign-in

- `POST /api/login` with `username` and `password` sets the cookie `throughline_session`: `HttpOnly`, `SameSite=Strict`, `Path=/`, `Max-Age` of seven days, and `Secure` when the public URL is `https`. The token is 32 random bytes. The database stores only its SHA-256.
- A session lasts seven days from sign-in and is not extended by use. That is the price of not storing when an account was last seen.
- The sign-in answer and `GET /api/me` carry a request token. With a session, every `POST`, `PUT` and `DELETE` under `/api` needs the header `X-Throughline-Request-Token` with that token and an `Origin` on the public URL's host, otherwise 403 `forbidden_request`. The page does this on its own.
- Sessions end at sign-out, at expiry (expired ones are deleted at every sign-in), when the account is disabled, when its role changes and when `account password` sets a new password. Changing the own password in the page ends the account's other sessions and keeps the current one.
- Failed sign-ins: 10 attempts per account within 15 minutes, then 429 `too_many_attempts` until the window has passed or `account unlock` clears it. A name without an account stores nothing. On top of that, the server checks at most 60 passwords per minute in total, then 429.
- One trade-off is worth knowing: only an existing account can reach its per-account limit, so a 429 on a name tells the caller that the account exists. The server-wide limit keeps that probing slow.
- Password hashing (scrypt) runs outside the database lock, two at a time at most. New hashes have the form `scrypt2$15,8,3$<salt>$<digest>`.
- In team mode database error texts do not reach the client. The answer is `query_failed` without detail; the detail goes to the server log, without an account name.

## What is not recorded

Team mode does not watch the people who use it. That is a design decision, not a missing feature.

- No sign-in history, no record of who read which note or session, no "last seen", no access log. Migration 014 removes the old `access_audit` table and its trigger, and `access_sessions.last_seen_at`.
- No log line names an account. The only exception is an error message of the `account` command on the server's command line.
- MCP requests, keys and client names are not logged, as in local mode.

What the database does keep, and why:

| Table and column | What it holds | Why |
| --- | --- | --- |
| `access_users.created_at` | when the account was created | set once, never updated |
| `access_sessions.created_at`, `expires_at` | an open session | gone at sign-out, expiry, disable, role or password change; at most seven days |
| `access_attempts.window_start`, `attempts` | failed sign-ins of one account, keyed by a hash of its name | gone after a successful sign-in, after 15 minutes or on `account unlock` |
| `access_agent_keys.created_at`, `revoked_at` | when a key was made and revoked | key management; a revoked key's row stays |
| `memory_chunks.owner_id`, `visibility` | who owns a note and whether it is private | visibility |

Notes keep their usual counters `access_count` and `last_accessed`. They belong to the note, not to an account. On a shared note they do not say who used it; a private note can only be used by its owner.

## What stays shared

Conversations and their messages, projects, the AI configuration, costs and the timeline are one shared stock. Every signed-in account sees every ingested session. Only notes can be private.

## Known limits

- The server's command line and `throughline mcp` over stdio run as the operator, without an account. They see everything, private notes included: `export-markdown`, `ask`, `recall`, `context`, `handoff` and the rest. Anyone with a shell or database access on the server is an operator.
- Jobs touch shared notes only. Private notes are not reflected on, merged, written back or marked stale by a job.
- One database connection behind a lock serves all requests. A slow request delays the others. That is fine for a small team, not for many people at once.
- The session cookie has no `__Host-` prefix.
- No account page in the browser, no password reset by mail, no SSO or OAuth, no tenants, no rights per project.

## Compose

Create the admin, then switch the mode:

```bash
docker compose exec web throughline account add alice --role admin --display-name "Alice"
# In .env:
#   THROUGHLINE_AUTH_MODE=team
#   THROUGHLINE_PUBLIC_URL=https://throughline.example.internal
docker compose up -d web
docker compose exec web throughline doctor   # auth mode: team (1 accounts, 0 private notes)
```

`account add` warns that a database with accounts starts only in team mode. The running server keeps going until the restart.

## Upgrading to team mode

1. Back up first: `throughline backup` or `pg_dump -Fc`. Migration 014 deletes data that no later step brings back.
2. Run `throughline migrate` (Compose does this on `up`). Migration 014:
   - creates or keeps `access_users`, `access_sessions`, `access_attempts` and the new `access_agent_keys`;
   - drops `access_sessions.last_seen_at`;
   - drops the table `access_audit` with all its rows, its protection function and the triggers `record_access_change` and `audit_processing_lifecycle`;
   - adds `memory_chunks.owner_id` and `visibility` (existing notes: no owner, shared) and `favorites.user_id`;
   - keeps the table `conversation_project_changes`; its trigger now writes `operator` as the actor.
3. A database without accounts keeps running in local mode, nothing else to do.
4. A database from the earlier Python team mode keeps its accounts and roles. Their `scrypt$…` password hashes keep working and are replaced by `scrypt2` at the next successful sign-in. Because accounts exist, this database starts only in team mode.

A restored backup carries the session hashes that were open when it was taken. Before you expose a restored copy, delete them: `DELETE FROM access_sessions`.
