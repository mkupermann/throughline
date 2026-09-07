# Reproduce the demo

The media uses `scripts/seed_demo_data.py`, never an export of personal conversations. It creates 44 conversations, 249 messages, seven project records, extracted memories, review cases, four fictional agent tasks and two reusable teams. IDs and timestamps are not intended as stable benchmark inputs; some fixture dates are relative to the seed time.

Atlas (demo) tells one complete story: define a retrieval experiment, record a provisional result, discover a counterexample in long documents, then resume with the next step. Its [evaluation file](demo/atlas/evaluation.md) is an invented result, not a measured benchmark. Other projects populate search, review queues and team operations.

## Create a separate database

Use a development PostgreSQL 16 server with pgvector, a role allowed to create databases, and standard `PGHOST`, `PGPORT`, `PGUSER` and `PGPASSWORD` connection settings. Install Throughline first using the main README. Do not point demo tooling at a production database.

```bash
python scripts/seed_demo_data.py --dbname throughline_release_demo
PGDATABASE=throughline_release_demo throughline serve --host 127.0.0.1 --port 8794
```

Database names must end in `_demo`. The seeder owns the demo tables and reseeds them. `--reset` deliberately drops and recreates the named demo database. Keep that operation separate from your real corpus. Log fixtures are written under a temporary demo workspace; override it with `--workspace` if needed.

Open <http://127.0.0.1:8794>. Choose **Conversations → Atlas (demo)**. Open the counterexample conversation to see its prompt, answer, tool result and explicit file reference. Return to the project state to follow the source links.

## Record each area

Requires Node, pnpm, Playwright Chromium, `ffmpeg` with the `libx264` encoder, and `ffprobe` on PATH. From the repository root:

```bash
pnpm --dir web install
pnpm --dir web exec playwright install chromium
pnpm --dir web run build
node web/scripts/record-release.mjs
```

`THROUGHLINE_DEMO_URL` overrides the loopback URL. `FFMPEG_BIN` selects an ffmpeg build with H.264 support when the system package lacks it. The script checks the corpus counts and Atlas source-folder fingerprint before recording. These checks are a guard against accidental capture, not a security boundary: use a dedicated database.

The recordings drive the real UI and save MP4s, representative PNGs, WebVTT captions and a manifest under `docs/videos/`. They do not stub API responses or launch generation, import jobs or agents. Operate displays the local runtime's genuine status; capabilities and discovery counts can differ across machines.

Inspect every resulting clip before publication. Regenerate the gallery after substantial navigation changes. Old recordings in `docs/assets/` document the earlier interface and are not the current walkthrough set.

The recorder explicitly selects English without changing your normal browser preference. It also builds 800-pixel animated GIF previews for GitHub from the MP4 recordings.
