# Contributing to Throughline

Thank you for considering a contribution. This project is small, opinionated,
and moves fast — a little coordination up front saves everyone time.

## Ground rules

- Be kind. The [Code of Conduct](CODE_OF_CONDUCT.md) applies everywhere.
- One topic per PR. Large drive-by refactors will be asked to split.
- If you are not sure whether a change is wanted, open an issue first.
- Never commit secrets. `.env`, `config.yaml`, `*.dump`, `backups/` are gitignored — keep it that way.

## Getting set up

```bash
git clone https://github.com/mkupermann/Throughline.git
cd Throughline
./scripts/install.sh
pip3 install --break-system-packages -r requirements-dev.txt  # if you plan to run lint/tests
```

If the installer does not work on your OS, see [`docs/INSTALLATION.md`](docs/INSTALLATION.md)
for a manual walkthrough.

## Branch naming

Use a short prefix and a hyphenated description.

| Prefix | Purpose | Example |
|---|---|---|
| `feature/` | New functionality | `feature/ollama-embeddings` |
| `fix/` | Bug fix | `fix/ingest-duplicate-sessions` |
| `docs/` | Documentation only | `docs/installation-linux` |
| `refactor/` | Internal cleanup, no behavior change | `refactor/split-app-py` |
| `chore/` | Tooling, CI, dependencies | `chore/bump-streamlit` |
| `test/` | Test additions or repairs | `test/reflect-memory-dedup` |

Branch off `main`. Keep branches rebased, not merged.

## Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/).

```
<type>(<scope>): <short summary>

<body, wrapped at 72 cols, explaining the "why">

<footer — BREAKING CHANGE:, Refs #123, Co-authored-by: ...>
```

Accepted types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`.

Examples:

```
feat(embeddings): add Ollama provider with nomic-embed-text

Adds an alternative 768-dim embedding path so users can run fully offline.
The provider is selected via config.embeddings.provider. HNSW index over
the new embedding_768 column is created on first use.

Refs #42
```

```
fix(ingest): stop double-counting tool_result messages

The map_role function was returning 'user' for tool_result blocks when
the enclosing message had mixed content. We now inspect all blocks.
```

## Code style

### Python

- Target 3.10+.
- Format with [`black`](https://github.com/psf/black) (default 88-column line length).
- Lint with [`ruff`](https://github.com/astral-sh/ruff) using the config in `pyproject.toml`.
- Type hints on public functions (`def foo(x: int) -> str:`). Internal helpers may skip them.
- Imports in three blocks: stdlib, third-party, local — separated by blank lines.
- No emojis in code or comments.
- Prefer f-strings over `%` or `.format()`.
- Log with `print()` in CLI scripts, the `logging` module in library code.
- Database access through `psycopg2` with parameterized queries. Never build SQL with string concatenation on user input.

Run locally:

```bash
black scripts/ gui/ skill/
ruff check scripts/ gui/ skill/
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
- Link relative (`[foo](docs/foo.md)`) not absolute.
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

Integration tests are **not** part of the pre-commit run — they require a live
Postgres. Run them with:

```bash
docker compose up -d postgres
pytest tests/integration/ -v -m integration
```

## Running the CI checks locally

```bash
# Python syntax
python3 -m py_compile scripts/*.py gui/*.py skill/scripts/*.py

# SQL syntax (requires psql)
psql -d claude_memory -f sql/schema.sql --set ON_ERROR_STOP=1 --single-transaction --dry-run

# Markdown lint (requires markdownlint-cli)
markdownlint '**/*.md' --ignore node_modules
```

CI (`.github/workflows/ci.yml`) runs the same three checks on every PR.

## Tests

This project is database-backed, so unit tests are limited. Contributions that
add pytest coverage for pure-Python utilities are welcome.

End-to-end smoke test (requires a running Postgres with the schema loaded):

```bash
# Seed a fake session
python3 tests/seed_fake_session.py

# Ingest it
python3 scripts/ingest_sessions.py

# Verify
psql -d claude_memory -c "SELECT count(*) FROM conversations WHERE project_name = 'test-fixture';"
```

## Submitting a pull request

1. Fork the repo and create a branch with the right prefix.
2. Make your change. Keep it focused.
3. Run `black`, `ruff`, and the CI checks above.
4. Update `CHANGELOG.md` under `## [Unreleased]`.
5. Open the PR using the template. Include:
   - What changed and why.
   - A test plan (the exact commands you ran).
   - Screenshots for GUI changes.
6. Respond to review comments with commits, not amend + force-push, until approval.
7. After approval, a maintainer will squash-merge. Rebase-and-merge on request.

## Architecture decisions

Non-trivial design choices are documented as ADRs under `docs/adr/`.
If your PR introduces a new dependency, changes the schema, or alters a
public interface, add a short ADR (use `docs/adr/0000-template.md` as a
starting point) in the same PR.

## Release process

Maintainer-only:

```bash
# Bump version in pyproject.toml and CHANGELOG.md
git commit -am "chore(release): vX.Y.Z"
git tag vX.Y.Z
git push --follow-tags
```

CI will create a GitHub release from the tag.

## Questions

Open a [Discussion](https://github.com/mkupermann/Throughline/discussions) for
design questions, or an [Issue](https://github.com/mkupermann/Throughline/issues)
for bugs and feature requests.
