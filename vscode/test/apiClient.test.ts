import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { ApiClient } from "../src/apiClient";
import { ThroughlineError } from "../src/errors";
import { FakeServer, closedPort } from "./fakeServer";

const srv = new FakeServer();
const other = new FakeServer();
before(async () => {
  await srv.start();
  await other.start();
});
after(async () => {
  await srv.stop();
  await other.stop();
});

test("api_recall_builds_the_query", async () => {
  srv.requests.length = 0;
  srv.respond(200, { mode: "current", hits: [{ id: 7, content: "c", project_name: "p", score: 1.5 }], degraded: null });
  const r = await new ApiClient(srv.baseUrl).recall("café & more", { project: "demo project", rank: "weighted", limit: 5 });
  assert.equal(r.hits[0].id, 7);
  const req = srv.requests[0];
  assert.equal(req.method, "GET");
  const u = new URL(req.path, "http://x");
  assert.equal(u.pathname, "/api/recall");
  assert.equal(u.searchParams.get("q"), "café & more");
  assert.equal(u.searchParams.get("project"), "demo project");
  assert.equal(u.searchParams.get("rank"), "weighted");
  assert.equal(u.searchParams.get("limit"), "5");
  assert.equal(req.headers.authorization, undefined, "display reads carry no key");
});

test("unreachable_server_is_reported", async () => {
  const port = await closedPort();
  await assert.rejects(
    () => new ApiClient(`http://127.0.0.1:${port}`).health(),
    (e: unknown) => e instanceof ThroughlineError && e.kind === "unreachable",
  );
});

test("clients_never_call_another_host (api)", async () => {
  srv.requests.length = 0;
  other.requests.length = 0;
  srv.respond(200, { conflicts: [], checked: 0 });
  const c = new ApiClient(`${srv.baseUrl}/prefix/`);
  await c.conflicts();
  await c.note(3);
  assert.deepEqual(srv.requests.map((r) => r.path), ["/prefix/api/conflicts", "/prefix/api/memory/3"]);
  assert.equal(other.requests.length, 0);
  // A redirect to another host is refused, not followed.
  const raw = (await import("node:http")).createServer((_q, res) => {
    res.writeHead(302, { Location: `${other.baseUrl}/api/health-recovery` });
    res.end();
  });
  await new Promise<void>((ok) => raw.listen(0, "127.0.0.1", ok));
  const port = (raw.address() as import("node:net").AddressInfo).port;
  await assert.rejects(() => new ApiClient(`http://127.0.0.1:${port}`).health(), ThroughlineError);
  assert.equal(other.requests.length, 0);
  raw.closeAllConnections();
  raw.close();
});
