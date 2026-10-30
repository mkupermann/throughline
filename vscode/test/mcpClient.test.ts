import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { McpClient } from "../src/mcpClient";
import { ThroughlineError } from "../src/errors";
import { FakeServer, closedPort } from "./fakeServer";

const srv = new FakeServer();
const other = new FakeServer();
const KEY = "k".repeat(40);
const key = async () => KEY;
before(async () => {
  await srv.start();
  await other.start();
});
after(async () => {
  await srv.stop();
  await other.stop();
});

const ok = (text: string) => ({
  jsonrpc: "2.0",
  id: 1,
  result: { content: [{ type: "text", text }], isError: false, resultType: "complete" },
});
const rpcError = (code: number, message: string) => ({ jsonrpc: "2.0", id: 1, error: { code, message } });

test("mcp_call_sends_modern_headers_and_meta", async () => {
  srv.requests.length = 0;
  srv.respond(200, ok(JSON.stringify(["a", "b"])));
  const names = await new McpClient(srv.baseUrl, key).listProjects();
  assert.deepEqual(names, ["a", "b"]);
  const r = srv.requests[0];
  assert.equal(r.method, "POST");
  assert.equal(r.path, "/mcp");
  assert.equal(r.headers["authorization"], `Bearer ${KEY}`);
  assert.equal(r.headers["mcp-protocol-version"], "2026-07-28");
  assert.equal(r.headers["mcp-method"], "tools/call");
  assert.equal(r.headers["mcp-name"], "list_projects");
  assert.equal(r.headers["accept"], "application/json, text/event-stream");
  assert.equal(r.headers["content-type"], "application/json");
  const b = JSON.parse(r.body);
  assert.equal(b.method, "tools/call");
  assert.equal(b.params.name, "list_projects");
  const m = b.params._meta;
  assert.equal(m["io.modelcontextprotocol/protocolVersion"], "2026-07-28");
  assert.equal(m["io.modelcontextprotocol/clientInfo"].name, "throughline-vscode");
  assert.ok(m["io.modelcontextprotocol/clientInfo"].version);
  assert.deepEqual(m["io.modelcontextprotocol/clientCapabilities"], {});
});

test("mcp_errors_are_mapped", async () => {
  const c = new McpClient(srv.baseUrl, key);
  const cases: Array<[number, unknown, string]> = [
    [401, { error: "unauthorized", detail: "invalid credentials" }, "unauthorized"],
    [503, { error: "agent_access_off", detail: "off" }, "access_off"],
    [403, { error: "forbidden", detail: "origin" }, "forbidden"],
    [413, { error: "payload_too_large", detail: "big" }, "too_large"],
    [400, rpcError(-32020, "Header mismatch"), "protocol"],
    [400, rpcError(-32022, "Unsupported"), "protocol"],
    [200, rpcError(-32602, "Unknown tool"), "invalid"],
    [200, rpcError(-32000, "note not found"), "server"],
  ];
  for (const [status, body, kind] of cases) {
    srv.respond(status, body);
    await assert.rejects(
      () => c.call("get_note", { id: 1 }),
      (e: unknown) => e instanceof ThroughlineError && e.kind === kind,
      `${status} ${JSON.stringify(body)} -> ${kind}`,
    );
  }
  srv.respond(200, rpcError(-32000, "note not found"));
  await assert.rejects(() => c.call("get_note", {}), /note not found/);
});

test("mcp_without_a_key_makes_no_request", async () => {
  srv.requests.length = 0;
  await assert.rejects(
    () => new McpClient(srv.baseUrl, async () => undefined).call("stats", {}),
    (e: unknown) => e instanceof ThroughlineError && e.kind === "no_key",
  );
  assert.equal(srv.requests.length, 0);
});

test("unreachable_server_is_reported (mcp)", async () => {
  const port = await closedPort();
  await assert.rejects(
    () => new McpClient(`http://127.0.0.1:${port}`, key).call("stats", {}),
    (e: unknown) => e instanceof ThroughlineError && e.kind === "unreachable",
  );
});

test("clients_never_call_another_host (mcp)", async () => {
  srv.requests.length = 0;
  other.requests.length = 0;
  srv.respond(200, ok("[]"));
  await new McpClient(`${srv.baseUrl}/prefix`, key).listProjects();
  assert.equal(srv.requests[0].path, "/prefix/mcp");
  assert.equal(other.requests.length, 0);
});
