# Task 7 report — documentation reconciliation

## Outcome

Reconciled the public documentation with the release-hardening implementation.

- README, installation, deployment, security, migration, systemd, MCP, usage,
  performance, and benchmark documents now use Python 3.10+ and the packaged
  runtime path.
- New and upgraded native databases use `throughline migrate`. The migration
  documentation explains packaged SQL, baseline detection, transactional
  execution, and the Compose migration gate.
- Compose documentation now covers the bootstrap script, loopback publication,
  unprivileged UID/GID-matched containers, read-only source mounts, readiness,
  and immutable role/database identities during credential rotation.
- English and German architecture references now describe the writer's complete
  refresh semantics, generated-session filtering, the eight UI route components,
  and the release CI gates.
- FAQ and MCP documentation no longer describe Claude Code as the only source,
  a `claude_memory` default, planned MCP/systemd support, or the retired
  Streamlit UI. The 0.1.0-beta release notes are explicitly marked historical.
- No screenshots changed. The current eight-route UI already has generated
  screenshots, and the hardening work changed error and retry handling rather
  than a material layout or navigation surface.

## Verification

```text
python3 -m throughline --help
python3 -m throughline migrate --help
python3 -m throughline.jobs.backfill_generated_by --help
python3 -m throughline.jobs.extract_entities --help
documented packaged command entry points parse

local Markdown links/assets passed: 65
structural Markdown lint passed for 19 files
git diff --check
```

The structural lint checks balanced fenced blocks, valid headings outside code
fences, and trailing whitespace. The repository's CI workflow also defines a
Markdownlint job. `markdownlint-cli2` is not installed locally; its offline
invocation waited for a package fetch, and installing an unpinned npm package
with elevated permissions was rejected. No installation workaround was used.

The current-document contradiction scan found only three deliberate references
to source wrappers: the two checkout-based systemd services and the migration
README's statement that `scripts/migrate.py` is a compatibility wrapper. No
current user instruction points to an obsolete migration path, manual new-schema
bootstrap, Python 3.11+, the old Streamlit UI, or the retired `claude_memory`
default.

## Prediction and verdict

Pre-registered bar: public installation, security, architecture, migration, and
release guidance must match the packaged commands, Compose boundaries, current
GUI, and verified test counts; local links/assets and documentation checks must
show no introduced errors.

Prediction: README, Usage, migrations, FAQ, release notes, and the German
architecture draft would contain most contradictions. Hit.

Verdict: PASS. The docs now describe the verified implementation. The formal
Markdownlint executable was unavailable locally, so structural lint and the
repository's CI configuration were inspected instead.
