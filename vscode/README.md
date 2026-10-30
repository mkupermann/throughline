# Throughline for VS Code

Your Throughline memory inside the editor. Look up what holds for a project, write a decision down without opening the browser, and hand the context pack to Cline or Continue.

The extension talks to one address only, the Throughline server in `throughline.url` (default `http://127.0.0.1:8788`). It sends no telemetry and calls no other host. It does not change the server.

## Two paths, one rule

| Path | Used for | Key | Counts as use |
| --- | --- | --- | --- |
| `GET /api/...` | Recall, project context, open note, conflicts, embedding status | none | no |
| `POST /mcp` | Quick note, remember selection, copy context, send context to an agent, connection check | agent key | only the context pack |

Looking at notes is browsing, and browsing does not count. Handing the context pack to an agent does: it calls the MCP tool `project_context`, which raises `access_count` of every note in the pack. Writing a note goes through the MCP tool `write`, which creates a note and counts nothing. The connection check calls `list_projects`, which counts nothing either. The full counting rule is in the [usage guide](../docs/USAGE.md#how-often-a-note-is-used-and-how-strong-it-is).

## Setup

1. Turn on agent access on the server: `THROUGHLINE_AGENT_KEY` in `.env`, see [Agent access over HTTP](../docs/USAGE.md#agent-access-over-http).
2. Build the package with `npm ci && npm run package` in this folder, or take `throughline-vscode` from a CI run.
3. Install it: `code --install-extension throughline-0.1.0.vsix`. On macOS without `code` on the path, use `"/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"`.
4. Run **Throughline: Set Agent Key** from the command palette and paste the key.
5. Set `throughline.url` in your user settings if your server is not on port 8788, and `throughline.project` if the project is not named like the workspace folder.

The key lives in VS Code's secret storage under `throughline.agentKey`. It is never written to `settings.json` or any file. It may only contain printable ASCII characters without spaces; **Set Agent Key** refuses anything else. If the server echoes the key back in an error, the extension shows `***` in its place.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `throughline.url` | `http://127.0.0.1:8788` | Address of the Throughline server, `http` or `https`. The only address the extension calls. User settings only: a workspace's `.vscode/settings.json` cannot change it, so an opened repository cannot send your key elsewhere. Any other scheme falls back to the default, with one warning. |
| `throughline.project` | empty | Project name. Empty means the name of the workspace folder. Set per workspace. |
| `throughline.contextBudget` | `2000` | Token budget of a context pack, kept between 64 and 32768. |
| `throughline.rank` | `fulltext` | Ranking for recall: `fulltext` or `weighted` (needs embeddings). |

## Commands

| Command | What it does | Path |
| --- | --- | --- |
| Throughline: Recall | Asks for a question and shows the hits under "Last recall" in the Throughline view. A click opens the note. | `/api` |
| Throughline: Show Project Context | Opens the context pack of the project as a Markdown document and lists it in the view. | `/api` |
| Throughline: Open Note | Opens one note by id as a Markdown document: status, validity, and which note it replaces or was replaced by. | `/api` |
| Throughline: Show Conflicts | Opens the conflict report, grouped by kind. | `/api` |
| Throughline: Open Web UI | Opens `throughline.url` in the browser. | none |
| Throughline: Quick Note | Asks for text and a category and writes a note. | `/mcp` |
| Throughline: Remember Selection | Writes the selected text as a note, after asking for a category. | `/mcp` |
| Throughline: Copy Context | Puts the context pack, with an optional task, on the clipboard. | `/mcp` |
| Throughline: Send Context to Agent | Hands the context pack to Cline or Continue, see below. | `/mcp` |
| Throughline: Set Agent Key | Stores the agent key in secret storage. | none |
| Throughline: Refresh Connection | Checks the connection again. | `/mcp` |
| Throughline: Show Embedding Status | Shows the embedding target and how many embeddings are missing. Also a click on the status bar item. | `/api` |

The status bar item sits at the bottom right. It shows `Throughline: <project> · connected`, or `not connected` with the reason: no key, key rejected, agent access off on the server, server unreachable, or server has no `/mcp` endpoint. The last one means the server is older than the extension; the commands that use `/mcp` then say "This Throughline server has no /mcp endpoint. Update the server." The status is checked at start, after a settings change and on command. There is no polling.

## Handing context to an agent

**Send Context to Agent** fetches the context pack and looks for the chat commands of the agents you have installed.

- **Cline.** The pack goes to the clipboard first, then into a scratch tab, which is selected and passed to `cline.addToChat`. The scratch tab closes afterwards, without asking to save, if it is still in front.
- **Continue.** The pack goes to the clipboard and the Continue input gets the focus. Paste with Cmd+V (Ctrl+V on Windows and Linux).
- **Both installed.** You pick one.
- **Neither installed, or the command fails.** The pack stays on the clipboard and a message says so.

`cline.addToChat` and `continue.focusContinueInputWithoutClear` are not documented by Cline or Continue and may change. That is why the clipboard copy always comes first. The extension writes nothing into the configuration of Cline or Continue and puts no key there.

## What it does not do

- No marketplace release. The package is a local VSIX.
- No recording of AI code changes, no log of who did what and when.
- No telemetry and no call to any address other than `throughline.url`.
- No command deletes or replaces a note.
- No session import, no own chat, no model calls.

## Development

```bash
npm ci
npm run lint        # type check
npm test            # unit tests, no server needed
npm run package     # dist/extension.js and throughline-0.1.0.vsix
```

`npm run test:integration` runs every command in a real VS Code against a Throughline test instance. It is local only, never in CI, and skips with a message when `THROUGHLINE_TEST_URL` is unset.

```bash
THROUGHLINE_TEST_URL=http://127.0.0.1:8792 THROUGHLINE_TEST_KEY=<agent key of that instance> npm run test:integration
```

Point it at an instance with the fictional demo data (projects `Atlas-demo`, `Harbor-demo`, `Orchard-demo`), never at your own memory: it writes two notes into `Atlas-demo`. The first run downloads VS Code into `.vscode-test`. The run uses a throwaway profile with in-memory secret storage, restores the clipboard afterwards and leaves your own VS Code untouched.
