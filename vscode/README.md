# Throughline for VS Code

Browse your Throughline memory notes from VS Code. The extension talks only to the address in
`throughline.url` (default `http://127.0.0.1:8788`). It sends no telemetry and calls no other host.

- Reading goes through `/api` without a key.
- Writing and handing notes to an agent go through `POST /mcp` with the agent key. The key is kept in
  VS Code's secret storage, never in `settings.json` or a file.

This is the scaffold: the API and MCP clients exist, the views follow.
