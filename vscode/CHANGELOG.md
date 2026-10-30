# Changelog

## 0.1.0

First local release, installed from a VSIX. Not on the marketplace.

- Commands: Recall, Show Project Context, Open Note, Show Conflicts, Open Web UI, Quick Note, Remember Selection, Copy Context, Send Context to Agent, Set Agent Key, Refresh Connection, Show Embedding Status.
- Display goes through `/api` without a key and does not count as use. Writing and handing context to an agent go through `POST /mcp` (protocol `2026-07-28`) with the agent key from VS Code's secret storage.
- Throughline view with the context pack and the last recall. A click opens the note.
- Status bar with the project and the connection, or the reason it is not connected. Embedding status on click. No polling.
- Send Context to Agent hands the pack to Cline or Continue through their chat commands and always keeps a copy on the clipboard. Without either, the clipboard is the result.
- Calls only `throughline.url`. No telemetry.
- Unit tests in CI, an end-to-end run in a real VS Code against a test instance (`npm run test:integration`, local only).
