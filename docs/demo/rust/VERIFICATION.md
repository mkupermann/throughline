# README verification record

Scope: default Rust application, fictional demo and documentation. This is not a product-wide certification.

Updated 2026-10-24 after the presentation-readiness program (M2 to M5). The original pass is preserved below where still valid.

## Directly checked

- Default Compose points to `Dockerfile.rust`; the server embeds the Rust HTML interface, not `web/src/App.tsx`.
- A separate `throughline-readme-demo` stack started with a fresh database volume. Rust migrations and the SQL seed completed successfully.
- The served HTML matches `rust/crates/throughline/web/index.html` after line-ending normalization. Its SHA-256 and actual capture timestamp are in the [manifest](../../media/rust/manifest.json). Every capture run re-proves this (served==checkout guard).
- The API reported 18 conversations, 72 messages, 12 memory chunks and three projects. All 18 session IDs and project labels matched the fictional fixture before capture. With the model overlay active, 72 embeddings exist (768 dimensions, 0 errors).
- Browser automation navigated all nine menus, switched History to cards, opened a conversation and a message, ran lexical search, opened Calendar and the command palette, and executed Status.
- Screenshots (15 English, 15 German) were captured without JavaScript page exceptions, dark theme pinned. No API mocks were used for the model-free demo. The model overlay runs real local models (nomic-embed-text, qwen2.5:1.5b-instruct).
- The e2e suite `web/scripts/e2e-ux.mjs` passes 10 checks against the live demo: clipboard handoff, markdown download, theme persistence, pin-as-note, review keep/forget with undo, empty-state filters, responsive at 1280x720, language toggle EN/DE, zero page errors, and an empty-database phase on a throwaway DB.
- Migration idempotency: a second and third `throughline migrate` run applied 0 migrations and exited 0 (throwaway database, 2026-10-24).
- Backup restoration: `pg_dump` from the running demo restored into a fresh PostgreSQL container. Row counts matched the live `/api/status` exactly (18/72/12 plus 72 embeddings; 2026-10-24).
- The running CLI's help exposed 24 application commands. `search decision --limit 2` and `handoff --project Atlas-demo --limit 2` returned fictional corpus records. `export-markdown --evidence` produced a provenance manifest whose SHA-256 entries verified against the exported files.
- `cargo test -p throughline --lib`: 81 passed, 0 failed (2026-10-24). CI green on `main` through commit 4c0887a.
- Accessibility: `web/scripts/a11y-rust.mjs` ran axe-core over all nine views in dark/en, light/en and dark/de (27 checks). Result: 0 critical, 0 serious, 3 moderate (heading order on Projects, listed, not gating). Keyboard walk on Home: 15 of 15 focusable elements show a visible focus style (2026-10-24).

## Resolved since the first pass

| Original finding | Resolution | Commit |
| --- | --- | --- |
| Some UI glyphs were garbled (about 350 double-encoded sequences). | Repaired; scan shows 0 chars in U+0080..U+009F and 0 mojibake patterns. | 7d251ed |
| Date formatting followed the browser locale. | All date rendering goes through two formatter functions; locale follows the UI language (en-GB/de-DE). | 7d251ed, 4c0887a |
| Rows truncated text without a tooltip. | Truncating cells carry `title` attributes. | 7d251ed |
| Rust MCP `forget` wrote `updated_at`, absent from a fresh schema. | Migration 006 adds the column; insert plus forget runs clean on an empty database. | 7d251ed |
| No embeddings, semantic ranking unvalidated. | Demo model overlay embeds all 72 messages; semantic and hybrid return hits; Ask answers with sources in 0.5-1.9 s warm. | fc1b869 |
| Ask returned zero sources on natural-language questions. | Retrieval switched from whole-string lexical match to hybrid search. | fc1b869 |
| Backup restoration not established. | Proven by dump-and-restore with matching totals (see above). | - |

## Findings still reflected in the documentation

| Finding | Documentation consequence |
| --- | --- |
| Two applications coexist in the checkout. | Rust is documented as default; earlier React/PM images are not presented as its features. |
| Rust `ingest` accepts `--source all`. | Removed the older `--all` quick-start command. |
| Rust reads `THROUGHLINE_BACKUP_DIR`; Compose still carries the older variable. | Backup example uses an explicit persistent `--dir`. |
| Rust router has no application authentication/role layer. | Local trusted deployment only; Python team settings are not claimed to protect it. |
| Ask resolves its backend before entering the generation fallback. | No unconditional offline-answer promise. |
| CSV requests up to 50,000 matching records. | Described as bounded conversation export, not a database backup. |
| Conversation role toggle uses `tool`, while stored tool-result messages use `tool_result`. | The captured conversation omits that role; Browse/message detail is used to inspect tool output. |

## Not established by this pass

- A fresh Docker image build on a clean host, platform portability or a full Rust/Python/React regression run.
- All 11 adapters against real session formats; the demo seeds SQL records and only six tool labels.
- Model generation quality or answer-evaluation validity. Latency was measured (cold model about 9.5 s, warm under 2 s), quality was not scored.
- Production migrations on real data, destructive retention or instruction writeback.
- Success of every CLI/MCP action, or a complete security audit.

No commit, push or public publication is part of this verification record itself. The resolutions above reference the commits that shipped them.
