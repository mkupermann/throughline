import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { ApiClient } from "../src/apiClient";
import { McpClient } from "../src/mcpClient";
import * as cmd from "../src/commands";
import { FakeServer } from "./fakeServer";

const srv = new FakeServer();
before(() => srv.start());
after(() => srv.stop());

function fakeUi(over: Partial<cmd.Ui> = {}) {
  const log = { docs: [] as string[], errors: [] as string[], actions: [] as string[], inputs: 0, picks: [] as string[][], infos: [] as string[], clip: [] as string[] };
  const ui: cmd.Ui = {
    input: async () => { log.inputs++; return "typed"; },
    pick: async (items) => { log.picks.push(items); return items[0]; },
    info: (m) => void log.infos.push(m),
    error: (m, a) => { log.errors.push(m); (a ?? []).forEach((x) => log.actions.push(x.label)); },
    openDocument: async (md) => void log.docs.push(md),
    openExternal: async () => {},
    clipboard: async (t) => void log.clip.push(t),
    selection: () => "sel",
    ...over,
  };
  return { ui, log };
}

function deps(ui: cmd.Ui, project: string | undefined, key?: string): cmd.Deps {
  return {
    api: new ApiClient(srv.baseUrl),
    mcp: new McpClient(srv.baseUrl, async () => key),
    ui,
    settings: { url: () => srv.baseUrl, project: () => project, budget: () => 999999, rank: () => "fulltext" },
  };
}

test("display_commands_work_without_a_key", async () => {
  srv.requests.length = 0;
  const { ui, log } = fakeUi();
  const d = deps(ui, "demo");
  srv.respond(200, { mode: "m", hits: [{ id: 1, content: "c", score: 1 }], degraded: null });
  await cmd.recall(d, "q");
  srv.respond(200, { text: "", items: [], citations: [], tokens_used: 1, budget: 64, within_budget: true, skipped: 0, estimate: "" });
  await cmd.projectContext(d);
  srv.respond(200, { id: 4, content: "n", category: "decision", project_name: "demo", created_at: null, source_at: null, valid_from: null, valid_to: null, status: "current", superseded_by: null, superseded_at: null, supersedes: null });
  await cmd.openNote(d, 4);
  srv.respond(200, { conflicts: [], checked: 0 });
  await cmd.conflicts(d);
  assert.deepEqual(log.errors, []);
  assert.equal(log.docs.length, 3);
  assert.equal(srv.requests.length, 4);
  for (const r of srv.requests) {
    assert.ok(r.path.startsWith("/api/"), r.path);
    assert.equal(r.headers.authorization, undefined);
  }
  const ctx = new URL(srv.requests[1].path, "http://x");
  assert.equal(ctx.searchParams.get("budget"), "32768", "budget is clamped");
});

test("no_workspace_folder_asks_for_a_project", async () => {
  srv.requests.length = 0;
  srv.respond(200, { text: "", items: [], citations: [], tokens_used: 1, budget: 64, within_budget: true, skipped: 0, estimate: "" });
  const a = fakeUi({ input: async () => "picked-proj" });
  await cmd.projectContext(deps(a.ui, undefined));
  assert.equal(new URL(srv.requests[0].path, "http://x").searchParams.get("project"), "picked-proj");
  assert.equal(srv.requests.length, 1, "display project choice does not use /mcp");
  // cancelled choice sends nothing
  srv.requests.length = 0;
  const b = fakeUi({ input: async () => undefined });
  await cmd.projectContext(deps(b.ui, undefined));
  assert.equal(srv.requests.length, 0);
  // agent commands offer the server's project list and use the pick
  srv.requests.length = 0;
  const rpc = (text: string) => ({ status: 200, body: { jsonrpc: "2.0", id: 1, result: { content: [{ type: "text", text }] } } });
  srv.queue(rpc(JSON.stringify(["alpha", "beta"])), rpc("ok"));
  const offered: string[][] = [];
  const c = fakeUi({ pick: async (items) => { offered.push(items); return items.includes("alpha") ? "beta" : "decision"; } });
  await cmd.rememberSelection(deps(c.ui, undefined, "k"));
  assert.ok(offered.some((i) => i.join() === "alpha,beta"), "the list_projects names were offered");
  assert.equal(JSON.parse(srv.requests[0].body).params.name, "list_projects");
  assert.equal(JSON.parse(srv.requests[1].body).params.arguments.project, "beta");
});

test("agent_project_choice_without_a_key_offers_to_set_it", async () => {
  srv.requests.length = 0;
  const { ui, log } = fakeUi();
  await cmd.rememberSelection({ ...deps(ui, undefined), setKey: () => {} });
  assert.deepEqual(log.actions, ["Set Agent Key"]);
  assert.equal(log.inputs, 0);
  assert.equal(srv.requests.length, 0);
});

test("open_ui_opens_the_configured_url", async () => {
  const opened: string[] = [];
  const { ui } = fakeUi({ openExternal: async (u) => void opened.push(u) });
  await cmd.openUi(deps(ui, "demo"));
  assert.deepEqual(opened, [srv.baseUrl]);
});

test("write_commands_need_a_key_and_offer_to_set_it", async () => {
  srv.requests.length = 0;
  const { ui, log } = fakeUi();
  let ran = 0;
  const d = { ...deps(ui, "demo"), setKey: () => void ran++ };
  await cmd.quickNote(d);
  assert.equal(srv.requests.length, 0);
  assert.deepEqual(log.actions, ["Set Agent Key"]);
});

test("remember_selection_sends_write_and_empty_selection_is_refused", async () => {
  srv.requests.length = 0;
  srv.respond(200, { jsonrpc: "2.0", id: 1, result: { content: [{ type: "text", text: "ok" }] } });
  const a = fakeUi();
  await cmd.rememberSelection(deps(a.ui, "demo", "k"));
  const body = JSON.parse(srv.requests[0].body);
  assert.equal(body.params.name, "write");
  assert.deepEqual(body.params.arguments, { content: "sel", category: "decision", project: "demo" });
  srv.requests.length = 0;
  const b = fakeUi({ selection: () => "  " });
  await cmd.rememberSelection(deps(b.ui, "demo", "k"));
  assert.deepEqual(b.log.errors, ["Select text first"]);
  assert.equal(srv.requests.length, 0);
});

test("open_note_rejects_non_numeric_input_without_a_request", async () => {
  srv.requests.length = 0;
  const { ui, log } = fakeUi({ input: async () => "12abc" });
  await cmd.openNote(deps(ui, "demo"));
  assert.equal(log.errors.length, 1);
  assert.equal(srv.requests.length, 0);
});

test("copy_context_uses_project_context_with_clamped_budget", async () => {
  srv.requests.length = 0;
  srv.respond(200, { jsonrpc: "2.0", id: 1, result: { content: [{ type: "text", text: "PACK" }] } });
  const { ui, log } = fakeUi({ input: async () => "" });
  await cmd.copyContext(deps(ui, "demo", "k"));
  assert.deepEqual(JSON.parse(srv.requests[0].body).params.arguments, { project: "demo", budget: 32768 });
  assert.deepEqual(log.clip, ["PACK"]);
});

function agentDeps(ui: cmd.Ui, commands: string[], calls: string[]): cmd.Deps {
  const d = deps(ui, "demo", "k");
  d.agents = {
    commands: async () => commands,
    host: {
      executeCommand: async (id) => void calls.push(`exec:${id}`),
      openScratchDocument: async () => void calls.push("open"),
      selectAll: async () => void calls.push("selectAll"),
      closeScratchDocument: async () => void calls.push("close"),
      clipboard: async (t) => void calls.push(`clip:${t}`),
      info: () => {},
    },
  };
  return d;
}

test("send_to_agent_command_picks_by_available_agents", async () => {
  const rpc = () => srv.respond(200, { jsonrpc: "2.0", id: 1, result: { content: [{ type: "text", text: "PACK" }] } });
  // exactly one agent: no question
  srv.requests.length = 0;
  rpc();
  let calls: string[] = [];
  let a = fakeUi({ input: async () => "" });
  await cmd.sendToAgent(agentDeps(a.ui, ["cline.addToChat"], calls));
  assert.deepEqual(a.log.picks, []);
  assert.deepEqual(calls, ["clip:PACK", "open", "selectAll", "exec:cline.addToChat", "close"]);
  assert.deepEqual(a.log.infos, ["Context sent to Cline."]);
  assert.equal(JSON.parse(srv.requests[0].body).params.name, "project_context");
  // both: offer a pick
  rpc();
  calls = [];
  a = fakeUi({ input: async () => "", pick: async (items) => { a.log.picks.push(items); return "Continue"; } });
  await cmd.sendToAgent(agentDeps(a.ui, ["cline.addToChat", "continue.focusContinueInputWithoutClear"], calls));
  assert.deepEqual(a.log.picks, [["Cline", "Continue", "Clipboard"]]);
  assert.deepEqual(calls, ["clip:PACK", "exec:continue.focusContinueInputWithoutClear"]);
  assert.deepEqual(a.log.infos, [], "the host's own text is not doubled");
  // an unexpected or cancelled pick does nothing
  for (const v of ["Other", undefined]) {
    rpc();
    calls = [];
    a = fakeUi({ input: async () => "", pick: async () => v });
    await cmd.sendToAgent(agentDeps(a.ui, ["cline.addToChat", "continue.focusContinueInputWithoutClear"], calls));
    assert.deepEqual(calls, []);
  }
  // none: straight to the clipboard
  rpc();
  calls = [];
  a = fakeUi({ input: async () => "" });
  await cmd.sendToAgent(agentDeps(a.ui, [], calls));
  assert.deepEqual(a.log.picks, []);
  assert.deepEqual(calls, ["clip:PACK"]);
});

test("send_to_agent_without_key_offers_set_key", async () => {
  const a = fakeUi({ input: async () => "" });
  const d = agentDeps(a.ui, [], []);
  d.mcp = new McpClient(srv.baseUrl, async () => undefined);
  await cmd.sendToAgent(d);
  assert.equal(a.log.errors.length, 1);
  assert.deepEqual(a.log.actions, [cmd.SET_KEY]);
});
