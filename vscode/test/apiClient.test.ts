import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { ApiClient } from "../src/apiClient";
import { ThroughlineError } from "../src/errors";
import { FakeServer, closedPort, type Recorded } from "./fakeServer";

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

test("api_retries_once_with_bearer_on_401", async () => {
  srv.requests.length = 0;
  srv.queue({ status: 401, body: { error: "unauthorized" } }, { status: 200, body: { mode: "current", hits: [], degraded: null } });
  const r = await new ApiClient(srv.baseUrl, () => Promise.resolve("sekret-key-1")).recall("x");
  assert.equal(r.mode, "current");
  assert.equal(srv.requests.length, 2);
  assert.equal(srv.requests[0].headers.authorization, undefined);
  assert.equal(srv.requests[1].headers.authorization, "Bearer sekret-key-1");
});

test("api_second_401_names_the_command_and_hides_the_key", async () => {
  srv.requests.length = 0;
  srv.respond(401, { error: "bad sekret-key-2 here" });
  await assert.rejects(
    () => new ApiClient(srv.baseUrl, () => Promise.resolve("sekret-key-2")).recall("x"),
    (e: unknown) =>
      e instanceof ThroughlineError &&
      e.kind === "unauthorized" &&
      e.message.includes("Throughline: Set Agent Key") &&
      !e.message.includes("sekret-key-2") &&
      !e.detail.includes("sekret-key-2"),
  );
  assert.equal(srv.requests.length, 2);
  srv.respond(200, {});
});

test("api_without_key_keeps_401", async () => {
  srv.requests.length = 0;
  srv.respond(401, { error: "unauthorized" });
  await assert.rejects(
    () => new ApiClient(srv.baseUrl, () => Promise.resolve(undefined)).recall("x"),
    (e: unknown) => e instanceof ThroughlineError && e.kind === "sign_in_required" && e.message.includes("Throughline: Set Agent Key"),
  );
  assert.equal(srv.requests.length, 1);
  srv.respond(200, {});
});

test("api_403_does_not_retry", async () => {
  srv.requests.length = 0;
  srv.respond(403, { error: "forbidden" });
  await assert.rejects(
    () => new ApiClient(srv.baseUrl, () => Promise.resolve("sekret-key-3")).recall("x"),
    (e: unknown) => e instanceof ThroughlineError && e.kind === "forbidden",
  );
  assert.equal(srv.requests.length, 1);
  srv.respond(200, {});
});

test("api_blank_or_failing_key_means_no_retry", async () => {
  for (const getter of [() => Promise.resolve("  "), () => Promise.reject(new Error("keychain locked"))]) {
    srv.requests.length = 0;
    srv.respond(401, { error: "unauthorized" });
    await assert.rejects(
      () => new ApiClient(srv.baseUrl, getter).recall("x"),
      (e: unknown) => e instanceof ThroughlineError && e.kind === "sign_in_required" && !e.message.includes("keychain"),
    );
    assert.equal(srv.requests.length, 1);
  }
  srv.respond(200, {});
});

test("api_key_getter_is_not_called_without_a_401", async () => {
  srv.requests.length = 0;
  srv.respond(200, { mode: "current", hits: [], degraded: null });
  let calls = 0;
  await new ApiClient(srv.baseUrl, () => {
    calls++;
    return Promise.resolve("sekret-key-4");
  }).recall("x");
  assert.equal(calls, 0);
  assert.equal(srv.requests.length, 1);
});

test("api_error_text_never_contains_an_echoed_header_key", async () => {
  srv.requests.length = 0;
  srv.respond(401, (r: Recorded) => ({ error: "unauthorized", detail: String(r.headers.authorization ?? "none") }));
  await assert.rejects(
    () => new ApiClient(srv.baseUrl, () => Promise.resolve("sekret-key-5")).recall("x"),
    (e: unknown) => e instanceof ThroughlineError && !e.message.includes("sekret-key-5") && !e.detail.includes("sekret-key-5") && e.detail.includes("Bearer ***"),
  );
  srv.respond(200, {});
});
