# Changelog

## Unreleased

- Team mode: when `/api` answers 401 and an agent key is stored, the call is repeated once with `Authorization: Bearer <key>`. A second 401 asks for `Throughline: Set Agent Key`; without a key the status bar reads "sign-in required", not "key rejected". Local mode is unchanged. Error messages never show the key.

## 0.1.0

First local release, installed from a VSIX. Not on the marketplace.

- Commands: Recall, Show Project Context, Open Note, Show Conflicts, Open Web UI, Quick Note, Remember Selection, Copy Context, Send Context to Agent, Set Agent Key, Refresh Connection, Show Embedding Status.
- In local mode, display goes through `/api` without a key and does not count as use (in team mode it does, see Unreleased). Writing and handing context to an agent go through `POST /mcp` (protocol `2026-07-28`) with the agent key from VS Code's secret storage.
- Throughline view with the context pack and the last recall. A click opens the note.
- Status bar with the project and the connection, or the reason it is not connected. Embedding status on click. No polling.
- Send Context to Agent hands the pack to Cline or Continue through their chat commands and always keeps a copy on the clipboard. Without either, the clipboard is the result.
- Calls only `throughline.url`, which is a user setting (`machine` scope) a workspace cannot override, and only over `http` or `https`. No telemetry.
- Status bar item at the bottom right. An older server without `/mcp` shows as "server has no /mcp endpoint".
- The agent key must be printable ASCII without spaces. Error messages never show the key; an echoed key appears as `***`.
- The Cline scratch tab is selected through its own editor and closes without a save prompt.
- Unit tests in CI, an end-to-end run in a real VS Code against a test instance (`npm run test:integration`, local only).
