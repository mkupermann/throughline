# Shared-workspace review record

Review date: 2026-09-08. Scope: controlled internal use of one shared corpus. Six simulated perspectives reviewed raw source files and documentation through Mistral Vibe. These are model-generated reviews, not employees, endorsements, an independent security audit or organizational acceptance. A timed-out review was discarded.

## Latest valid verdicts

| Perspective | Verdict |
| --- | --- |
| Developer experience | WOULD-USE |
| Security and privacy | NOT-CONVINCED |
| Technical writing | WOULD-USE |
| Sovereignty and provider neutrality | WOULD-USE |
| Skeptical engineering | WOULD-USE |
| Open-source maintenance | NOT-CONVINCED |

The spread is four WOULD-USE and two NOT-CONVINCED. Verdicts are retained; acceptance rests on reproducible evidence, not unanimous praise. Reviewers inspected files with read-only tools. Browser checks and video inspection were performed separately; a text review does not establish visual usability.

## Defects corrected

- Added actor-attributed processing lifecycle audit events, including cancellation by a different administrator.
- Made failed worker startup visible and bounded automatic crash recovery. A real terminated worker was recovered without repeating completed full-pass steps.
- Canonicalized public origins and default ports; retained strict Host, Origin, application-header and session-token checks.
- Bounded CLI schema inputs, narrowed environment forwarding to the selected CLI and preserved access to the host OS keyring. Database and bridge credentials are excluded from that CLI environment.
- Replaced opaque connection failures with a fixed, safe diagnostic vocabulary. Empty Vibe output and Claude error responses produce defined errors. Real synthetic Codex and Vibe connection tests passed through the regular installation.
- Corrected account-screen contrast and verified project-assignment history in the actual browser.

## Findings not adopted

The final security review described inherited worker credentials as a leak into queued options. These are different boundaries: trusted first-party processing commands need the application database/provider environment; submitted queue options exclude credentials. The host CLI has a separate restricted environment. This distinction is documented in [AI processing](AI_PROCESSING.md).

The same review described 8,192-character log chunks as silently losing long lines. The pipe reader uses `readline(8192)`, so a longer line is read in subsequent chunks. The retained tail is deliberately bounded; old chunks and unflushed diagnostics can be lost. It is not a complete execution archive or the audit log.

The maintenance review proposed replacing the Origin/application-header rejection condition's `or` with `and`. That would weaken the requirement that both checks pass. PostgreSQL-backed browser/API tests verify accepted same-origin requests and rejected missing or incorrect headers; real login, project creation and assignment also succeeded.

The engineering review proposed a `Popen.pgid` property and described a blocking bridge lock. The child uses `start_new_session=True`, making its PID the process-group ID; the lock uses `acquire(blocking=False)` and returns HTTP 409 when busy. Captured stderr is a string because `stderr=PIPE` and `text=True` are set. These suggested changes were not applied.

## Remaining operating limits

Project-level confidentiality, tenant isolation, SSO/MFA integration, a secret vault, externally anchored audit storage, automatic retention and independent penetration testing are not delivered. Existing provider secrets remain stored as before. Interrupted processing steps can run again and repeat paid requests. Real organizational usability and rollout approval remain external evidence to collect.

The final publication review accepted the bounded public claims and repeated the disclosed secret-storage, audit and at-least-once limitations as objections. Those limits remain explicit.

See the [acceptance record](ENTERPRISE_ACCEPTANCE.md) and [deployment boundary](TEAM_DEPLOYMENT.md) before enabling access for colleagues.
