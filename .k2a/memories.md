# Memories — throughline

Exported from throughline (claude_memory) — 23 active memories.
Auto-generated for k2a knowledge-base ingestion. Do not edit by hand.

## decision

- Use `git push --force-with-lease origin main` (not `--force`) when syncing the claude-memory-db mirror to canonical throughline. `--force-with-lease` refuses if origin moved underneath, preventing accidental overwrite.
  _2026-05-10 · confidence 0.95_ · #git #throughline #mirror #force-push

- scripts/_db.py (22-line DB config helper) was deleted from throughline repo and its logic inlined into individual scripts and centralized in throughline/config.py — no separate DB helper script needed.
  _2026-05-06 · confidence 0.90_ · #refactoring #db-config #throughline

## error_solution

- Throughline CI unit-tests job does not install the `mcp` SDK. Tests that import `mcp` must start with `pytest.importorskip('mcp')` or they fail on CI even though they pass locally.
  _2026-05-10 · confidence 0.98_ · #throughline #ci #mcp #pytest

- docs/architecture.md is lowercase — macOS APFS resolves docs/ARCHITECTURE.md silently, but GitHub web view and Linux will 404. Use lowercase path in all links.
  _2026-05-06 · confidence 0.95_ · #docs #case-sensitivity #github

## insight

- PEP 668 brew Python guard blocks `pip install -e .` on the local machine. The editable install is already in place — new source changes are picked up automatically without reinstalling.
  _2026-05-10 · confidence 0.90_ · #python #pip #brew #pep668 #throughline

- Memory Health card on Throughline Dashboard reports 'Projects: 52' from DISTINCT project_name in memory_chunks, but the Projects page reads from the `projects` table which is empty (0 rows). These two sources are inconsistent — the card number is misleading.
  _2026-05-10 · confidence 0.97_ · #throughline #gui #data-inconsistency #projects

- YAML-Validator (`quick_validate.py`) schlägt bei Skill-Descriptions mit Doppelpunkten fehl (z.B. 'Triggers — EN:'), aber Claude Code's Skill-Loader ist permissiver — Skill funktioniert trotzdem. Parent-Skill hat dasselbe Pattern.
  _2026-05-06 · confidence 0.88_ · #skill-creation #yaml #validation

- Die Standard-`stakeholder-review`-Skill (CEO/CFO/COO/CIO/Procurement/HR/Headhunter/Coach/Anthropic) ist ungeeignet für Forschungsartefakte — keine Persona bewertet wissenschaftliche Rigorosität, Reproduzierbarkeit oder Methodenhonestität.
  _2026-05-06 · confidence 0.97_ · #stakeholder-review #skill #research

- human-voice-drafting skill explicitly excludes technical documentation (install commands, schema tables, reference sections) — apply only to narrative/prose front-matter, not README reference sections.
  _2026-05-06 · confidence 0.90_ · #human-voice-drafting #skill #readme

## project_context

- The `projects` table in Throughline's DB has 0 rows even though 552 memory chunks exist across ~52 distinct project_name values. Projects page is broken/empty as a result — backfill or migration needed.
  _2026-05-10 · confidence 0.95_ · #throughline #database #projects-table #bug

- Shipped in PRs #16 + #17 (merged 2026-05-10): `throughline status [--json|--pretty]` CLI, `memory.stats` MCP tool, `evals/run_eval.py --offline-stub` (30/30 deterministic smoke, DB-free), GUI Dashboard Memory Health card, CI eval-smoke job. 112 tests total (97 baseline + 15 new).
  _2026-05-10 · confidence 0.99_ · #throughline #observability #evals #gui #mcp

- throughline repo: mcp/ directory was removed and replaced by memory_mcp/ in PR #10 — any references to mcp/ path are stale.
  _2026-05-06 · confidence 0.95_ · #mcp #directory-structure #throughline

- CI-Run im 'throughline'-Repo fehlgeschlagen — Integration tests Postgres (41s). Wurde als GitHub-Notification gefiltert und nicht als Entwurf behandelt, aber manuell prüfenswert.
  _2026-04-22 · confidence 0.80_ · #throughline #ci #postgres #integration-tests

- CI auf mkupermann/throughline fehlgeschlagen (commit cd69cfa, 20.04.2026): Integration tests mit Postgres. Aktives Problem zum Zeitpunkt des Sessions.
  _2026-04-22 · confidence 0.75_ · #throughline #ci #postgres #integration-tests

- CI-Pipeline für mkupermann/throughline schlug fehl (commit cd69cfa) — Integration tests Postgres. Möglicherweise Datenbankverbindungsproblem oder Schema-Mismatch.
  _2026-04-21 · confidence 0.70_ · #throughline #ci #postgres #integration-tests

- throughline CI auf main fehlgeschlagen (Integration tests Postgres) — Stand 2026-04-20 23:09
  _2026-04-20 · confidence 0.85_ · #throughline #ci #postgres #integration-tests

- OpenAI API Key in throughline/tests/test_pii.py#L21 (commit 44c2afd9) wurde öffentlich exponiert. Aufgabe: Key revoken, rotieren, aus Code entfernen, in Env-Var auslagern. Kalender-Slot: Mo 20.04.2026 09:00-09:30.
  _2026-04-18 · confidence 0.95_ · #security #openai #api-key #throughline

## workflow

- Throughline GUI boots on localhost:8501 via `nohup streamlit run gui/app.py --server.headless true &`. Stop with `pkill -f 'streamlit run gui/app.py'`. HTTP health endpoint responds at `/healthz`.
  _2026-05-10 · confidence 0.90_ · #throughline #gui #streamlit #local-dev

- Throughline has two remotes: `throughline` (canonical: github.com/mkupermann/throughline) and `origin` (mirror: github.com/mkupermann/claude-memory-db). After every merge, force-sync origin to match canonical.
  _2026-05-10 · confidence 0.97_ · #throughline #git #remotes #mirror

- pre-commit's ruff auto-reformats argparse blocks and other code not touched in the current change, bloating the diff. Workaround: stage only intentional files, revert pre-commit churn with `git checkout -- <file>`, and re-apply surgical edits manually.
  _2026-05-10 · confidence 0.95_ · #pre-commit #ruff #commit-scope #throughline

- Neue Skill `research-stakeholder-review` erstellt unter `~/.claude/skills/research-stakeholder-review/` (286-Zeilen SKILL.md). Roster: Peer Reviewer, Replication Reviewer, Domain Expert, Methods/Stats, PI/Funder, Adversarial, Research-Software Maintainer, Null-Result Advocate, Science Communicator. Verdict-Wörter: submit/revise-and-submit/reject.
  _2026-05-06 · confidence 0.99_ · #research-stakeholder-review #skill #skill-creation

- gh pr merge merged PRs #5–#15 locally but never pushed the resulting main to GitHub — origin was frozen 6 hours and 11 PRs behind local. Always run git push origin main after gh pr merge to keep remote in sync.
  _2026-05-06 · confidence 0.95_ · #git #github #gh-cli

- When diagnosing git divergence, always check WHICH side is ahead before resetting — initial assumption was origin ahead of local, but local was actually 31 commits ahead (6+ hours newer). git log origin/main first.
  _2026-05-06 · confidence 0.90_ · #git #debugging #branching
