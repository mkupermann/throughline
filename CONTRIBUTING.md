# Contributing to Throughline

Thank you for considering a contribution. This project is small, opinionated,
and moves fast — a little coordination up front saves everyone time.

## Ground rules

- Be kind. The [Code of Conduct](CODE_OF_CONDUCT.md) applies everywhere.
- One topic per PR. Large drive-by refactors will be asked to split.
- If you are not sure whether a change is wanted, open an issue first.
- Never commit secrets. `.env`, `config.yaml`, `*.dump`, `backups/` are gitignored — keep it that way.

## Getting set up

Use Python 3.12 and Node 22 to match the current CI environment. Runtime metadata
permits Python 3.10+, but CI currently exercises Python 3.12 only. PostgreSQL 16
with pgvector is required for database-backed tests; it is not needed for the
DB-free suite. The activation command below is for a POSIX shell.

```bash
git clone https://github.com/mkupermann/throughline.git
cd throughline
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -r requirements-dev.txt
npm --prefix web ci
```

For platform-specific setup and PostgreSQL prerequisites, see [`docs/INSTALLATION.md`](docs/INSTALLATION.md)
for a manual walkthrough.

## Branch naming

Use a short prefix and a hyphenated description.

| Prefix | Purpose | Example |
|---|---|---|
| `feature/` | New functionality | `feature/ollama-embeddings` |
| `fix/` | Bug fix | `fix/ingest-duplicate-sessions` |
| `docs/` | Documentation only | `docs/installation-linux` |
| `refactor/` | Internal cleanup, no behavior change | `refactor/split-app-py` |
| `chore/` | Tooling, CI, dependencies | `chore/bump-dependencies` |
| `test/` | Test additions or repairs | `test/reflect-memory-dedup` |

Branch off `main`. Keep branches rebased, not merged.

## Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/).

```text
<type>(<scope>): <short summary>

<body, wrapped at 72 cols, explaining the "why">

<footer — BREAKING CHANGE:, Refs #123, Co-authored-by: ...>
```

Accepted types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`.

Examples:

```text
feat(embeddings): add Ollama provider with nomic-embed-text

Adds an alternative 768-dim embedding path so users can run fully offline.
The provider is selected via config.embeddings.provider. HNSW index over
the new embedding_768 column is created on first use.

Refs #42
```

```text
fix(ingest): stop double-counting tool_result messages

The map_role function was returning 'user' for tool_result blocks when
the enclosing message had mixed content. We now inspect all blocks.
```

## Code style

### Python

- Target 3.10+.
- Format with [`black`](https://github.com/psf/black) at the configured 120-column line length.
- Lint with [`ruff`](https://github.com/astral-sh/ruff) using the config in `pyproject.toml`.
- Type hints on public functions (`def foo(x: int) -> str:`). Internal helpers may skip them.
- Imports in three blocks: stdlib, third-party, local — separated by blank lines.
- No emojis in code or comments.
- Prefer f-strings over `%` or `.format()`.
- Log with `print()` in CLI scripts, the `logging` module in library code.
- Database access through `psycopg2` with parameterized queries. Never build SQL with string concatenation on user input.

Run locally:

```bash
black throughline memory_mcp scripts skill/scripts evals tests
ruff check throughline memory_mcp scripts skill/scripts evals tests
```

### SQL

- One statement per logical block; blank line between blocks.
- Table and column names `snake_case`.
- Enum types use singular names (`memory_category`, not `memory_categories`).
- Indexes named `idx_<table>_<column>[_<suffix>]`.
- Prefer `jsonb` over `json`, `text` over `varchar(n)` unless a hard limit is needed.

### AppleScript (launchd helpers)

- Plain AppleScript, no extensions.
- Four-space indent.
- Keep scripts under 100 lines; extract to shell if longer.

### Markdown

- GitHub-flavored.
- One sentence per line makes diffs readable, but not required.
- Link relative (`[installation](docs/INSTALLATION.md)`) not absolute.
- Fenced code blocks always specify a language: ` ```bash `, ` ```python `, ` ```sql `.

## Pre-commit hooks

Before your first commit, install the hooks:

```bash
pip install pre-commit
pre-commit install
```

On every commit this runs `ruff --fix`, `ruff-format`, the fast unit tests
(`pytest -m "not integration"`), and a handful of sanity checks
(trailing whitespace, merge-conflict markers, accidentally-staged private keys,
files over 1 MiB). To run all hooks ad hoc:

```bash
pre-commit run --all-files
```

The hook formatter is Ruff; CI separately enforces Black. After hooks change
Python files, run both the Ruff checks and Black check above before submission.
Passing the hooks alone does not imply that all CI checks pass.

Integration tests are **not** part of the pre-commit run — they require a live
Postgres. Run them with:

```bash
make test-integration
```

## Running the CI checks locally

```bash
# Python lint, format, and syntax
ruff check throughline memory_mcp scripts skill/scripts evals tests
black --check throughline memory_mcp scripts skill/scripts evals tests
python3 -m compileall -q throughline memory_mcp scripts skill/scripts evals tests

# DB-free tests and the enforced runtime coverage floor
pytest tests/ -v --tb=short -m "not integration" --ignore=tests/integration \
  --cov=throughline --cov=memory_mcp \
  --cov-report=term-missing:skip-covered --cov-fail-under=47.5

# Frontend tests, types, and reproducible production assets
npm --prefix web ci
npm --prefix web run test
npm --prefix web run typecheck
npm --prefix web run build
git diff --exit-code -- throughline/web

# Shell syntax
while IFS= read -r file; do bash -n "$file"; done < <(git ls-files '*.sh')

# Fresh schema snapshot (requires PostgreSQL and psql)
createdb throughline_schema_check
psql -v ON_ERROR_STOP=1 -d throughline_schema_check -f sql/schema.sql
dropdb throughline_schema_check

# Packaged migration discovery and status
throughline migrate --status

# Markdown formatting (same rules as CI)
markdownlint-cli2 '**/*.md'
```

CI (`.github/workflows/ci.yml`) also builds and smoke-installs the wheel outside
the checkout, verifies migration idempotence, runs all PostgreSQL-backed tests,
and exercises the eval/status smoke paths. Check Compose configuration separately with
`docker compose config --quiet` after initializing the ignored environment file.
CI runs for pushes to `main` and pull requests targeting `main`; a feature-branch
push alone does not trigger it. Markdown CI checks formatting, not every link target.

## Tests

The DB-free suite covers packaged runtime behavior; PostgreSQL-backed tests use
fresh temporary databases and must run without skips in CI.

### Real-server browser checks

With a disposable PostgreSQL 16/pgvector service and standard `PG*` connection
variables configured, run the same fixture and browser checks as CI:

```bash
npm --prefix web ci
(cd web && npx playwright install --with-deps chromium)
npm --prefix web run build
python scripts/seed_demo_data.py --dbname throughline_browser_demo
PGDATABASE=throughline_browser_demo THROUGHLINE_AUTH_MODE=local \
  throughline serve --host 127.0.0.1 --port 8795
```

Keep that server running and use a second terminal:

```bash
THROUGHLINE_DEMO_URL=http://127.0.0.1:8795 npm --prefix web run test:browser
```

The script checks three actual pages in both themes with axe, plus mobile
navigation focus and horizontal overflow. It writes screenshots and results under
`/tmp/throughline-browser-results` by default. This is a bounded browser regression,
not a full WCAG conformance audit. The `_demo` database is reseeded by the fixture;
never substitute a production database.

Use [the fictional demo guide](docs/DEMO.md) for browser smoke tests against an
isolated database. Never point fixture seeders or integration tests at a real corpus.
For UI changes, inspect desktop and mobile, both themes, keyboard focus, loading,
empty and error states. Record the exact commit, commands and observed results in
the PR; screenshots and model review do not replace behavior checks.

## Submitting a pull request

1. Fork the repo and create a branch with the right prefix.
2. Make your change. Keep it focused.
3. Run the relevant Python, frontend, database, and documentation gates above.
4. Update `CHANGELOG.md` under `## [Unreleased]`.
5. Open the PR using the template. Include:
   - What changed and why.
   - A test plan (the exact commands you ran).
   - Screenshots for GUI changes.
6. Respond to review comments with commits, not amend + force-push, until approval.
7. After approval, a maintainer will squash-merge. Rebase-and-merge on request.

## Reviewing user-facing claims

Anything a reader will act on — the README, `SECURITY.md`, the surface
descriptions, the screenshots — gets read by a second model before it ships,
through a different vendor's CLI than the one that wrote it. Six personas, each
asked for a verdict, quoted defects, and what would change its mind: a
developer-experience lead, a security reviewer, a technical writer, a
vendor-neutrality PM, a skeptical engineer, and an open-source maintainer.

Two rules make the difference between a review and a rubber stamp:

- **Submit the file, not a summary of it.** A reviewer handed your description
  grades your description. The failure is silent — the review comes back
  positive and the defect ships.
- **The reviewer is not a vote.** Fix what is real, say plainly what you refused
  to fix and why, and separate a defect in the text from a fact about the
  project. "Needs multi-user validation" is true here and cannot be fixed by
  editing.

It is not a substitute for tests, review, or judgement, and it does not catch
everything — a second model is simply one that cannot be persuaded by the
reasoning that produced the work, because it never saw it. What it has caught
so far was mostly not in the prose: three pipelines that still required one
vendor's CLI inside a tool that claims not to require any one vendor's CLI, a privacy statement
that contradicted itself two paragraphs later, and an absolute filesystem path
rendered into a screenshot bound for a public repository.

Any second model works. The runs behind the current docs used Mistral's `vibe`
CLI; the mechanics only matter in that a reviewer must be able to open the file
it is reviewing, and one that cannot is a void review, not a passing one.

## Architecture decisions

Document non-trivial choices in the relevant architecture or design document,
including alternatives, consequences and migration implications. Start from
[the architecture overview](docs/architecture.md) or [design blueprint](DESIGN.md).
There is currently no ADR directory or ADR template in this repository.

## Release process

Releases are maintainer actions, separate from merging a contribution. The current
[release workflow](.github/workflows/release.yml) builds and publishes a Docker image
to GHCR for matching version tags, targeting Linux amd64 and arm64. It does **not**
create a GitHub Release, publish to PyPI, or wait for CI to pass. Manual dispatch
also publishes an image; it is not a validation-only run.

Before publishing:

1. Choose a reviewed commit on `main` and confirm its complete CI run is green.
2. Update `pyproject.toml` and `CHANGELOG.md` consistently; review migration,
   deployment and security notes for the release.
3. Follow the [release validation checklist](docs/RELEASE_CHECKLIST.md), including
   clean installation, upgrade rehearsal, shipped assets and claims review.
4. Commit the version change through normal review and recheck CI for that commit.
5. Create an annotated version tag on the intended commit and push that exact tag.
   Avoid `--follow-tags`, which can publish unrelated local tags.
6. Inspect the Release workflow result and verify the published image digest and
   architectures. Create a GitHub Release with reviewed notes separately if desired.

Do not use a release tag to test the pipeline: it publishes externally. The
workflow requires an exact version tag at the checked-out commit and refuses
untagged commits. Image tags derive from that resolved version; only a stable
version receives `latest`. Use the published digest when verifying a release
rather than assuming that a mutable tag still identifies it.

## Questions

Open a [Discussion](https://github.com/mkupermann/Throughline/discussions) for
design questions, or an [Issue](https://github.com/mkupermann/Throughline/issues)
for bugs and feature requests.
