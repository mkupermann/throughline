# Screenshots

Every image here is generated, never taken by hand. The previous set was
captured with Cmd+Shift+4 and survived two UI rewrites for exactly that reason —
nobody was going to redo eleven images manually, so the documentation kept
showing a Streamlit app that had been deleted. Regenerating now costs one
command.

| File | Surface |
|---|---|
| `overview.png` | the worklist, then the last seven days by project |
| `project.png` | one project's sessions, sortable and searchable |
| `find.png` | one query across conversations, messages, memory, skills and prompts |
| `ask.png` | a cited answer assembled from your own records |
| `timeline.png` | one column per day, one lane per tool |
| `review.png` | the queues that keep memory trustworthy |
| `operate.png` | pipeline state and the jobs that change it |
| `console.png` | read-only SQL |
| `hero.png` | the current Overview in the blue diagonal launch composition at 1280 by 960 |
| `social-preview.png` | the current Timeline in the blue launch frame at GitHub's 1280 by 640 social-preview size |

## Regenerating them

Three commands. The first two build a demo database; the third drives a
headless browser against it.

```bash
# 1. A database that is not yours, from the bundled fixture
createdb throughline_demo
psql -d throughline_demo -f sql/schema.sql
psql -d throughline_demo -f examples/demo_data.sql
PGDATABASE=throughline_demo throughline embed --backend ollama   # so Ask works

# 2. Serve it, with HOME pointed somewhere synthetic
HOME=/path/to/a/fake/home PGDATABASE=throughline_demo throughline serve --port 8791

# 3. Capture
cd web && npm run screenshots -- --url http://127.0.0.1:8791 --out ../docs/screenshots
```

If Playwright's bundled Chromium is not installed, use a system browser with
`--browser chrome` or set `THROUGHLINE_SCREENSHOT_BROWSER=chrome`.

Before it opens a page, the script checks exact totals, the synthetic project
name and a known memory record from the bundled fixture. It refuses every
other database. This guard also protects against an accidentally supplied live
URL or a real database mounted on the expected demo port.

`examples/demo_data.sql` re-bases its own timestamps on load, so the seven-day
and per-day views are populated whatever date you run it — see the comment at
the bottom of that file.

## Why HOME is redirected

The provider bar and the Operate page do not read the database for their
"on disk" and "pending" columns — they scan the filesystem for each tool's
session directory. Run the capture against your real `$HOME` and the screenshots
publish how many sessions you personally have, and where. Point `HOME` at a
directory holding a handful of synthetic session files instead:

```text
<fake-home>/.claude/projects/-Users-dev-projects-<name>/*.jsonl
<fake-home>/.cursor/sessions/*.jsonl
<fake-home>/.codex/sessions/<date>/rollout-*.jsonl
<fake-home>/.vibe/logs/session/session_<YYYYMMDD>_<HHMMSS>_<hex>/
<fake-home>/.zed/data/sessions/session_*.json
```

The names matter: several adapters match a pattern rather than an extension, so
a file called `s1.json` is not discovered where `session_1.json` is.

## Rules for a usable screenshot

- **Nothing real.** No real names, paths, emails, tokens, project names, or
  costs. The fixture is synthetic and the capture must stay pointed at it.
- **Nothing empty.** A surface showing "nothing in this queue" documents
  nothing. If a page comes out empty, the fixture is missing rows — fix the
  fixture, not the screenshot.
- **Dark theme, 1440×900, 2× scale.** Set by the script; the width is the
  narrowest desktop the layout targets, so the images show it under pressure.
- **Full page.** Several surfaces are taller than the viewport and the fold is
  not a natural crop.
- **Reproducible launch artwork.** `hero.png` is always 1280 by 960 pixels and
  `social-preview.png` is always 1280 by 640 pixels, both at 1x scale. Their
  blue frames follow the launch artwork. Each interface is a fresh browser
  capture in light, compact mode, not a separate dashboard mockup.

## When they go stale

Any change to a surface's layout, a route, or the fixture. The script fails
loudly on a selector that no longer matches and still writes a
`<name>.FAILED.png` so you can see what it found instead.
