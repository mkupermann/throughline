# Controlled shared-workspace deployment

This guide describes a controlled internal deployment and its operating boundary. It is not a certification or a claim that every enterprise requirement is met.

Throughline's team mode serves one shared corpus. Every authenticated member can read that corpus. It does not provide separate tenants or project-level confidentiality. Use separate installations/databases for data that must not be shared with every member.

## Accounts and roles

Create the first administrator from the server's operator shell after applying migrations:

```bash
throughline migrate
throughline account owner --display-name "Workspace owner"
```

The command prompts for a password without echo. Passwords need 15–128 characters. Automation can pass `--password-file /absolute/owner-only-file`; passwords never belong in command arguments. `throughline account owner --reset` resets an existing account's password, revokes its sessions and preserves its role. Protect operator shell access: it can create administrators and access the database.

Configure the web application:

```bash
THROUGHLINE_AUTH_MODE=team
THROUGHLINE_PUBLIC_URL=https://throughline.example.internal
```

The public URL is the exact browser origin. Team mode refuses missing configuration, non-loopback HTTP URLs and unexpected Host headers. Use a TLS reverse proxy on the trusted network; preserve the public Host header and do not expose the backend or database port directly. HTTP is permitted only for loopback development. The existing remote-bind safeguard still applies. Local mode remains available for a single user on loopback.

| Role | Capabilities |
| --- | --- |
| Viewer | Read the shared project history, conversations, search and review queues; prepare a client-side handoff from already readable content. |
| Editor | Viewer capabilities plus project creation/assignment, source-linked project notes, readable names, knowledge curation and generated answers. |
| Administrator | Manage accounts, AI services, processing, filesystem exports, SQL Console and AI-team execution. |

Use **Workspace access** to add accounts, change roles, disable accounts and inspect the change history. Role changes and account disabling revoke sessions. The last enabled administrator cannot be demoted or disabled. Members can change their own password and sign out all their sessions. Recovery requires the operator command, not an unauthenticated password-reset link.

## Sessions and request protection

Login passwords use salted scrypt hashes. Session credentials are random, transmitted in HttpOnly SameSite=Strict cookies and stored only as hashes in PostgreSQL. HTTPS deployments use Secure cookies. Sessions expire after eight hours or 30 minutes of inactivity. The application checks account enablement and current role on each protected request.

Mutating browser requests require the configured origin, an application header and, after login, a session request token. Login attempt limits are stored in PostgreSQL, so starting another web process does not reset them. The reverse proxy should also limit request size and abusive traffic. Do not trust identity headers supplied by arbitrary clients.

Design references: [OWASP password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html), [session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html), and [CSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).

## Secrets and audit boundary

Existing provider secrets remain in their current database fields. This change neither deletes them nor claims encrypted secret storage. Only administrators can access provider configuration and the arbitrary read-only SQL Console. Database operators and backups can still expose the keys. Encrypt the deployment volume and backups according to your organization's policy. A dedicated secret vault remains a separate improvement.

Changes to accounts, project checkpoints, project display names, AI-purpose bindings, provider configuration, core team configuration, explicit project assignments and processing lifecycle transitions produce database audit records. Those records contain the actor, time, operation, record identifier and changed field names. They omit before/after content, password hashes and provider key values. API mutation attempts and results also receive request IDs. A database administrator can alter database protections; the audit table is not an externally anchored, tamper-proof log. Ordinary SQL updates, deletions and truncation of audit records are rejected.

## Release acceptance

Before enabling team mode for real colleagues, verify with separate viewer, editor and administrator accounts that forbidden API calls fail, revocation takes effect, imports preserve existing data, and a backup can be restored to a separate database. Enterprise rollout also requires the organization's TLS/network policy, identity/MFA policy, retention policy and ownership of recovery procedures. Those external approvals are not inferred from model reviews or CI.

The [acceptance record](ENTERPRISE_ACCEPTANCE.md) separates repository tests, browser checks and recovery evidence from outstanding organizational validation. [Upgrade notes](UPGRADING.md) describe migration and rollback constraints.

## Compose and TLS

The supplied Compose file accepts `THROUGHLINE_AUTH_MODE` and `THROUGHLINE_PUBLIC_URL` from `.env`; it keeps the backend on `127.0.0.1`. Bootstrap before enabling team mode:

```bash
docker compose exec web throughline account owner --display-name "Workspace owner"
# Set team mode and the exact HTTPS origin in your existing .env, then:
docker compose up -d --no-deps web
```

Terminate TLS with your organization's reverse proxy on the same host. Forward to `http://127.0.0.1:8788`, preserve the public Host header, support streaming responses, enforce request-size/rate limits and restrict network access. Do not publish the PostgreSQL port or CLI bridge to colleagues. `/api/health` is a minimal unauthenticated readiness endpoint and accepts the container's health-check Host; other paths enforce the configured Host. Origin/Host checks protect browser requests; authentication still requires a valid session cookie. They do not substitute for authentication or trusted network controls.

## Durable processing and recovery

Requests and completed full-pass steps live in PostgreSQL. The web server checks for queued/recoverable work every five seconds; a separate worker owns the database queue lock. Only one worker can execute registered processing commands per database. A guard terminates a command tree if its worker disappears; a replacement waits a short grace period before resuming. Finished steps are skipped, while interrupted steps may run again. This is at-least-once processing, including possible repeated hosted requests and charges. Use provider-side spending limits.

The UI retains the latest 50 run summaries and a bounded tail of 500 output chunks (up to 8,192 characters each) per run. Unflushed diagnostics may be lost at a crash. The durable state is the queue/checkpoint, not a complete log archive. Lifecycle audit records exclude log contents. Define retention and external log collection according to your policy; no automatic retention cleanup is enabled.

Back up PostgreSQL with `pg_dump -Fc` and separately preserve protected environment files, source mounts, CLI configuration and any external artifacts. Restore first to an isolated PostgreSQL 16 instance with pgvector. Verify conversation/message counts, account roles, explicit assignments, purpose bindings and provider-key equality without printing values. A restored backup contains active session hashes and pending jobs: before exposing it, revoke restored sessions (`DELETE FROM access_sessions`) and stop queued/running jobs in that isolated database. Do not run the web server until deciding whether those jobs should resume. Keep the original backup private and unchanged for recovery.

A database restore does not restore referenced source files, generated exports or the host's authenticated CLIs. Keep those in the organization's separate filesystem backup. An administrator's SQL Console can read provider keys; if that is unacceptable, do not grant that role or deploy an external secret store first.

Automatic recovery is limited to three interruptions per queued run. A further interruption marks that run failed and requires the operator to investigate before starting another pass. This bounds automatic retries, but is not a provider spending cap or an exactly-once guarantee. Worker startup failures are written to server stderr using a fixed message and exception class, without raw exception values.
