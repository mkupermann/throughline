# README verification record

Scope: default Rust application, fictional demo and documentation. This is not a product-wide certification.

## Directly checked

- Default Compose points to `Dockerfile.rust`; the server embeds the Rust HTML interface, not `web/src/App.tsx`.
- A separate `throughline-readme-demo` stack started with a fresh database volume. Rust migrations and the SQL seed completed successfully. The run reused the existing local Rust image; a clean image build was not repeated.
- The served HTML matches `rust/crates/throughline/web/index.html` after line-ending normalization. Its SHA-256 and actual capture timestamp are in the [manifest](../../media/rust/manifest.json).
- The API reported 18 conversations, 72 messages, 12 memory chunks, three projects and zero embeddings. All 18 session IDs and project labels matched the fictional fixture before capture.
- Browser automation navigated all nine menus, switched History to cards, opened a conversation and a message, ran lexical search, opened Calendar and the command palette, and executed Status.
- Fifteen actual screenshots were captured without JavaScript page exceptions. No API mocks, model calls or agent launches were used. Images were inspected for readable content and accidental private data.
- The running CLI's help exposed 24 application commands. `search decision --limit 2` and `handoff --project Atlas-demo --limit 2` returned fictional corpus records.
- All four new/revised Markdown documents passed markdownlint with the repository configuration. After expanding the gallery, all 68 relative file links resolved, and `git diff --check` passed.
- A local browser preview rendered all menu and supporting screenshots inline, with no collapsed sections; all 17 image elements loaded (15 distinct screenshots, the repeated Home image and the wordmark). The preview is in the ignored `.tmp/README-preview.html` file and is not a published website.

## Findings reflected in the documentation

| Finding | Documentation consequence |
| --- | --- |
| Two applications coexist in the checkout. | Rust is documented as default; earlier React/PM images are not presented as its features. |
| Rust `ingest` accepts `--source all`. | Removed the older `--all` quick-start command. |
| Rust reads `THROUGHLINE_BACKUP_DIR`; Compose still carries the older variable. | Backup example uses an explicit persistent `--dir`. |
| Rust router has no application authentication/role layer. | Local trusted deployment only; Python team settings are not claimed to protect it. |
| Rust MCP `forget` writes `updated_at`, absent from its fresh memory schema. | Marked that tool as a known fresh-schema gap. |
| Ask resolves its backend before entering the generation fallback. | No unconditional offline-answer promise. |
| CSV requests up to 50,000 matching records. | Described as bounded conversation export, not a database backup. |
| Some UI glyphs are garbled, date formatting remains German, and rows truncate text. | Screenshots retain actual rendering; no cosmetic retouching. |
| Conversation role toggle uses `tool`, while stored tool-result messages use `tool_result`. | The captured conversation omits that role; Browse/message detail is used to inspect tool output. |

## Not established by this pass

- A fresh Docker image build on a clean host, platform portability or a full Rust/Python/React regression run.
- All 11 adapters against real session formats; the demo seeds SQL records and only six tool labels.
- Model generation quality, embeddings, semantic/hybrid ranking or answer-evaluation validity.
- Backup restoration, production migrations, destructive retention or instruction writeback.
- Success of every CLI/MCP action, or a complete security audit. The schema mismatch above was identified from source, not by mutating real memory.

No application source was modified by this documentation pass. No commit, push or public publication is part of this verification.
