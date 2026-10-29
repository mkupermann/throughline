# Fictional Rust demo

[Back to the project](../../../README.md) · [Visual guide](../../FEATURE_GUIDE.md)

This is the demo for the **default Rust application**, independent of the older Python fixture in `scripts/seed_demo_data.py`.

## Start

From the repository root, with Docker and Compose available:

```bash
docker compose -f docs/demo/rust/compose.yml up -d --build
```

Open <http://127.0.0.1:8796>. The Compose project is `throughline-readme-demo`; its volume is `throughline-readme-demo_demo_data`. No private source directories, host credentials or real project folders are mounted. PostgreSQL has no published host port. The web service is bound to host loopback.

The seed runs after Rust migrations and replaces its owned demo tables. It refuses any database name other than `throughline_readme_demo`. It creates 18 conversations, 72 messages, 12 notes and three projects. UUIDs are fixed; dates are relative to seed time. The fixture has no embeddings and makes no model calls. Its six source labels demonstrate presentation, not adapter ingestion coverage.

Model endpoints deliberately target unavailable container loopback port 1. Ask is photographed before submission. Do not replace those endpoints with production credentials for a public recording.

The `fictional-demo-only` database password is public, for this isolated fixture only. Never reuse it for a real deployment.

## Stop or reseed

Stop without deleting the fixture:

```bash
docker compose -f docs/demo/rust/compose.yml stop
```

To deliberately replace the fictional fixture after exploring:

```bash
docker compose -f docs/demo/rust/compose.yml stop web
docker compose -f docs/demo/rust/compose.yml run --rm seed
docker compose -f docs/demo/rust/compose.yml up -d web
```

To remove **only this disposable demo**, including its volume:

```bash
docker compose -f docs/demo/rust/compose.yml down -v
```

Keep the explicit `-f docs/demo/rust/compose.yml`; omitting it targets the ordinary installation instead.

## Regenerate the screenshots

Requires Node/npm and a Playwright browser. Run from the repository root:

```bash
npm --prefix web ci
cd web
npx playwright install chromium
cd ..
node web/scripts/capture-readme.mjs
```

For an installed Edge browser on Windows:

```powershell
$env:THROUGHLINE_SCREENSHOT_BROWSER = 'msedge'
node web/scripts/capture-readme.mjs
```

The script checks loopback, exact corpus counts, all fictional session IDs and project labels before opening the browser. It also compares served HTML with the checkout, normalizing line endings. A mismatched build is rejected. These are accidental-capture safeguards, not an authentication mechanism.

It captures all nine menu surfaces plus History cards, conversation detail, message detail, Calendar, the Status result and command palette. Outputs are PNGs and a provenance manifest under `docs/media/rust/`. The browser requests an English locale and UTC, but some application date formatting remains German. Screenshots show real rendered UI, including current layout and encoding limitations; nothing is painted over or substituted.

For a different port, set `THROUGHLINE_DEMO_PORT` for Compose and `THROUGHLINE_DEMO_URL` for capture. A locally built image can be selected using `THROUGHLINE_DEMO_IMAGE`; only use `up --no-build` when that image is already available. The default clean-install command builds its own image.

Review every image before publication. The manifest records the normalized UI hash, capture time, corpus totals, screenshot names and JavaScript exceptions. An empty exception list is not proof that every function succeeds.
