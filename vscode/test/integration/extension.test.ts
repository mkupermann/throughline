// Runs inside a real VS Code (see test/runIntegration.ts) against a Throughline test
// instance with demo data. Every command goes through vscode.commands.executeCommand;
// only the prompts are scripted. The agent key comes from THROUGHLINE_TEST_KEY and is
// never printed.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import * as vscode from "vscode";
import { ApiClient, type NoteDetail, type RecallResult } from "../../src/apiClient";
import { detectAgents } from "../../src/agents";
import { CLIENT_NAME, CLIENT_VERSION, McpClient } from "../../src/mcpClient";
import { stateFromError, statusText } from "../../src/statusLogic";

const BASE = (process.env.THROUGHLINE_TEST_URL ?? "").replace(/\/+$/, "");
const KEY = process.env.THROUGHLINE_TEST_KEY ?? "";
const PROJECT = "Atlas-demo";
const MCP = `${BASE}/mcp`;

// V1: ten questions, answered by the extension's clients and by curl.
const QUERIES: [string, string?][] = [
  ["lexical retrieval", "Atlas-demo"],
  ["keyboard navigation", "Harbor-demo"],
  ["data contract", "Orchard-demo"],
  ["baseline"],
  ["mobile drawer"],
  ["interrupted batch recovery", "Orchard-demo"],
  ["visible focus"],
  ["long document counterexample", "Atlas-demo"],
  ["release handoff", "Harbor-demo"],
  ["decision"],
];

interface Call {
  who: "extension" | "test";
  method: string;
  url: string;
  auth: boolean;
  status?: number;
  body?: string;
}

const calls: Call[] = [];
let who: Call["who"] = "test";
const realFetch = globalThis.fetch;

const inputs: (string | undefined)[] = [];
const picks: (string | undefined)[] = [];
const seen = {
  infos: [] as string[],
  warnings: [] as string[],
  errors: [] as { msg: string; items: string[] }[],
  opened: [] as string[],
  unexpected: [] as string[],
};
const results: { step: string; ok: boolean; detail: string }[] = [];

function next(queue: (string | undefined)[], what: string): string | undefined {
  if (!queue.length) {
    seen.unexpected.push(what);
    return undefined;
  }
  return queue.shift();
}

function stub(target: object, name: string, fn: unknown): void {
  (target as Record<string, unknown>)[name] = fn;
  if ((target as Record<string, unknown>)[name] !== fn) throw new Error(`cannot replace ${name}`);
}

function install(): void {
  // Records every request made in this extension host, the extension's and the test's own.
  globalThis.fetch = async (input: string | URL | Request, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const call: Call = { who, method: init?.method ?? "GET", url, auth: new Headers(init?.headers).has("authorization") };
    calls.push(call);
    const res = await realFetch(input, init);
    call.status = res.status;
    call.body = await res.clone().text();
    return res;
  };
  stub(vscode.window, "showInputBox", async (o?: vscode.InputBoxOptions) => next(inputs, `input "${o?.prompt ?? ""}"`));
  stub(vscode.window, "showQuickPick", async (_items: unknown, o?: vscode.QuickPickOptions) =>
    next(picks, `pick "${o?.placeHolder ?? ""}"`),
  );
  stub(vscode.window, "showInformationMessage", async (m: string) => void seen.infos.push(m));
  stub(vscode.window, "showWarningMessage", async (m: string) => void seen.warnings.push(m));
  stub(vscode.window, "showErrorMessage", async (m: string, ...items: string[]) => void seen.errors.push({ msg: m, items }));
  stub(vscode.env, "openExternal", async (u: vscode.Uri) => {
    seen.opened.push(u.toString());
    return true;
  });
}

function withTimeout<T>(p: Thenable<T>, ms: number, what: string): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  const late = new Promise<T>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${what} did not finish within ${ms} ms`)), ms);
  });
  return Promise.race([Promise.resolve(p), late]).finally(() => clearTimeout(timer));
}

/** Runs a command with scripted prompt answers; returns the requests it made. */
async function exec(command: string, script: { inputs?: (string | undefined)[]; picks?: string[] } = {}, ...args: unknown[]): Promise<Call[]> {
  inputs.splice(0, inputs.length, ...(script.inputs ?? []));
  picks.splice(0, picks.length, ...(script.picks ?? []));
  const from = calls.length;
  who = "extension";
  try {
    await withTimeout(vscode.commands.executeCommand(command, ...args), 30_000, command);
  } finally {
    who = "test";
  }
  return calls.slice(from);
}

async function step(name: string, f: () => Promise<string>): Promise<void> {
  seen.infos.length = 0;
  seen.warnings.length = 0;
  seen.errors.length = 0;
  seen.unexpected.length = 0;
  try {
    const detail = await f();
    if (seen.unexpected.length) throw new Error(`unexpected prompt: ${seen.unexpected.join(", ")}`);
    results.push({ step: name, ok: true, detail });
  } catch (e) {
    results.push({ step: name, ok: false, detail: e instanceof Error ? e.message : String(e) });
  }
}

function noErrors(): void {
  assert.deepEqual(seen.errors, [], "no error message");
  assert.deepEqual(seen.warnings, [], "no warning");
}

function only(cs: Call[], pred: (c: Call) => boolean, what: string): Call {
  const hit = cs.filter(pred);
  assert.equal(hit.length, 1, `exactly one ${what} request, got ${hit.length}`);
  return hit[0];
}

const isMcp = (c: Call) => c.url === MCP;

/** The text of a tools/call answer. */
function toolText(body: string | undefined): string {
  const j = JSON.parse(body ?? "") as { result?: { content?: { text?: string }[] } };
  const text = j.result?.content?.[0]?.text;
  assert.equal(typeof text, "string", "tools/call answer carries text");
  return text as string;
}

function activeText(): string {
  return vscode.window.activeTextEditor?.document.getText() ?? "";
}

/** What StatusBar.refresh (src/status.ts) shows for a key, built from the same functions. */
async function statusFor(key: string | undefined): Promise<string> {
  try {
    await new McpClient(BASE, async () => key).listProjects();
    return statusText({ kind: "connected", project: PROJECT });
  } catch (e) {
    return statusText(stateFromError(e, BASE));
  }
}

function hitIds(json: string): number[] {
  return (JSON.parse(json) as RecallResult).hits.map((h) => h.id);
}

function curl(args: string[], stdin = ""): string {
  // The key travels on stdin (-H @-), never on the command line.
  return execFileSync("curl", ["-sS", "--noproxy", "*", "--max-time", "20", ...args], { input: stdin, encoding: "utf8" });
}

function curlRecallMcp(args: object): string {
  const body = JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    method: "tools/call",
    params: {
      name: "recall",
      arguments: args,
      _meta: {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": { name: "curl", version: "1" },
        "io.modelcontextprotocol/clientCapabilities": {},
      },
    },
  });
  const headers = [
    `Authorization: Bearer ${KEY}`,
    "Content-Type: application/json",
    "Accept: application/json, text/event-stream",
    "MCP-Protocol-Version: 2026-07-28",
    "Mcp-Method: tools/call",
    "Mcp-Name: recall",
  ].join("\n");
  return toolText(curl(["-H", "@-", "--data-binary", body, MCP], `${headers}\n`));
}

async function checkNote(id: number, content: string, word: string): Promise<NoteDetail> {
  const n = await new ApiClient(BASE).note(id);
  assert.equal(n.content, content, "GET /api/memory/{id} returns the same content");
  assert.equal(n.category, "decision");
  assert.equal(n.project_name, PROJECT);
  // The web UI lists notes through /api/memory; the new note must be there.
  const list = JSON.parse(curl([`${BASE}/api/memory?${new URLSearchParams({ q: word })}`])) as {
    chunks: { id: number }[];
  };
  assert.ok(list.chunks.some((c) => c.id === id), "the note appears in the UI list (/api/memory?q=)");
  return n;
}

async function steps(): Promise<void> {
  let firstHit = 0;
  const token = `vsxcheck${Date.now().toString(36)}`;

  await step("no key: write command offers Set Agent Key (V3)", async () => {
    const cs = await exec("throughline.quickNote", { inputs: ["note without a key"], picks: ["decision"] });
    assert.equal(cs.filter(isMcp).length, 0, "no /mcp request without a key");
    assert.equal(seen.errors.length, 1);
    assert.match(seen.errors[0].msg, /No agent key is stored/);
    assert.deepEqual(seen.errors[0].items, ["Set Agent Key"]);
    const s = await statusFor(undefined);
    assert.equal(s, "Throughline: not connected (no key, run Throughline: Set Agent Key)");
    return `error "${seen.errors[0].msg}" with action "Set Agent Key"; status "${s}"`;
  });

  await step("wrong key: 401, readable error, no crash (V3)", async () => {
    const wrong = `wrong-${randomBytes(8).toString("hex")}`;
    let cs = await exec("throughline.setKey", { inputs: [wrong] });
    assert.equal(only(cs, isMcp, "/mcp").status, 401, "status refresh gets 401");
    cs = await exec("throughline.quickNote", { inputs: ["note with a wrong key"], picks: ["decision"] });
    assert.equal(only(cs, isMcp, "/mcp").status, 401);
    assert.match(seen.errors[0]?.msg ?? "", /agent key is wrong or missing/);
    const s = await statusFor(wrong);
    assert.equal(s, "Throughline: not connected (key rejected)");
    return `HTTP 401; error "${seen.errors[0].msg}"; status "${s}"`;
  });

  await step("setKey and list_projects over /mcp", async () => {
    const cs = await exec("throughline.setKey", { inputs: [KEY] });
    const c = only(cs, isMcp, "/mcp");
    assert.equal(c.status, 200);
    assert.ok(c.auth, "Authorization header sent");
    const names = JSON.parse(toolText(c.body)) as string[];
    for (const p of ["Atlas-demo", "Harbor-demo", "Orchard-demo"]) assert.ok(names.includes(p), `${p} listed`);
    const s = await statusFor(KEY);
    assert.equal(s, "Throughline: Atlas-demo · connected");
    return `projects ${names.join(", ")}; status "${s}"`;
  });

  await step("recall", async () => {
    const cs = await exec("throughline.recall", { inputs: ["lexical retrieval"] });
    noErrors();
    const c = only(cs, (x) => x.url.startsWith(`${BASE}/api/recall?`), "/api/recall");
    assert.equal(c.auth, false, "display goes without a key");
    const r = JSON.parse(c.body ?? "") as RecallResult;
    assert.ok(r.hits.length >= 1, "at least one hit");
    assert.ok(r.hits.every((h) => h.project_name === PROJECT), "hits are from Atlas-demo");
    assert.ok(seen.infos.some((m) => m.startsWith(`${r.hits.length} note(s) found`)), "count shown");
    firstHit = r.hits[0].id;
    return `${r.hits.length} hits, ids ${r.hits.map((h) => h.id).join(", ")}`;
  });

  await step("projectContext", async () => {
    const cs = await exec("throughline.projectContext");
    noErrors();
    const c = only(cs, (x) => x.url.startsWith(`${BASE}/api/context?`), "/api/context");
    assert.equal(c.status, 200);
    assert.equal(c.auth, false);
    assert.ok(activeText().startsWith("# Throughline context"), "context document opened");
    const pack = JSON.parse(c.body ?? "") as { items: unknown[]; tokens_used: number };
    return `${pack.items.length} items, ${pack.tokens_used} tokens, document opened`;
  });

  await step("openNote (palette input and tree argument)", async () => {
    let cs = await exec("throughline.openNote", { inputs: [String(firstHit)] });
    noErrors();
    const note = JSON.parse(only(cs, (x) => x.url === `${BASE}/api/memory/${firstHit}`, "/api/memory/{id}").body ?? "") as NoteDetail;
    assert.ok(activeText().startsWith(`# Note ${firstHit}`));
    assert.ok(activeText().includes(note.content));
    cs = await exec("throughline.openNote", {}, firstHit);
    noErrors();
    only(cs, (x) => x.url === `${BASE}/api/memory/${firstHit}`, "/api/memory/{id}");
    assert.ok(activeText().startsWith(`# Note ${firstHit}`));
    return `note ${firstHit} opened twice`;
  });

  await step("conflicts", async () => {
    const cs = await exec("throughline.conflicts");
    noErrors();
    const c = only(cs, (x) => x.url === `${BASE}/api/conflicts`, "/api/conflicts");
    assert.equal(c.status, 200);
    assert.ok(activeText().startsWith("# Throughline conflicts"));
    const r = JSON.parse(c.body ?? "") as { conflicts: unknown[]; checked: number };
    return `${r.conflicts.length} conflicts, ${r.checked} checked`;
  });

  await step("quickNote creates a note", async () => {
    const word = `${token}q`;
    const content = `Integration check ${word}: quick note written from the VS Code test run.`;
    const cs = await exec("throughline.quickNote", { inputs: [content], picks: ["decision"] });
    noErrors();
    assert.ok(seen.infos.includes("Saved to Throughline."));
    const c = only(cs, isMcp, "/mcp");
    assert.ok(c.auth);
    const id = (JSON.parse(toolText(c.body)) as { id: number }).id;
    await checkNote(id, content, word);
    return `note ${id}, GET /api/memory/${id} same content, listed in /api/memory`;
  });

  await step("rememberSelection creates a note", async () => {
    const word = `${token}s`;
    const content = `Integration check ${word}: selection remembered from the VS Code test run.`;
    const doc = await vscode.workspace.openTextDocument({ language: "markdown", content });
    const ed = await vscode.window.showTextDocument(doc);
    ed.selection = new vscode.Selection(doc.positionAt(0), doc.positionAt(content.length));
    const cs = await exec("throughline.rememberSelection", { picks: ["decision"] });
    noErrors();
    const id = (JSON.parse(toolText(only(cs, isMcp, "/mcp").body)) as { id: number }).id;
    await checkNote(id, content, word);
    return `note ${id}, GET /api/memory/${id} same content, listed in /api/memory`;
  });

  await step("copyContext", async () => {
    await vscode.env.clipboard.writeText("sentinel");
    const cs = await exec("throughline.copyContext", { inputs: [""] });
    noErrors();
    const pack = toolText(only(cs, isMcp, "/mcp").body);
    assert.ok(pack.length > 0);
    assert.equal(await vscode.env.clipboard.readText(), pack, "clipboard holds the pack");
    assert.ok(seen.infos.includes("Context copied to the clipboard."));
    return `${pack.length} characters on the clipboard`;
  });

  await step("sendToAgent without Cline or Continue copies", async () => {
    const agents = detectAgents(await vscode.commands.getCommands(true));
    assert.deepEqual(agents, ["clipboard"], "no agent command registered");
    await vscode.env.clipboard.writeText("sentinel");
    const cs = await exec("throughline.sendToAgent", { inputs: [""] });
    noErrors();
    const pack = toolText(only(cs, isMcp, "/mcp").body);
    assert.equal(await vscode.env.clipboard.readText(), pack, "clipboard holds the pack");
    // This message comes only from the clipboard branch, which returns "copied".
    const msg = "Context copied to the clipboard (no Cline or Continue command found).";
    assert.ok(seen.infos.includes(msg));
    return `result copied: "${msg}"`;
  });

  await step("sendToAgent to Cline: selection in the scratch tab, closed without a save prompt", async () => {
    // A stand-in for Cline's command, registered in this extension host.
    assert.ok((await vscode.commands.getCommands(true)).includes("workbench.action.revertAndCloseActiveEditor"));
    let atAdd: { text: string; selected: string; dirty: boolean; untitled: boolean } | undefined;
    let scratch: vscode.TextDocument | undefined;
    const fake = vscode.commands.registerCommand("cline.addToChat", () => {
      const ed = vscode.window.activeTextEditor;
      if (!ed) return;
      scratch = ed.document;
      atAdd = { text: ed.document.getText(), selected: ed.document.getText(ed.selection), dirty: ed.document.isDirty, untitled: ed.document.isUntitled };
    });
    const ran: string[] = [];
    const realExec = vscode.commands.executeCommand;
    stub(vscode.commands, "executeCommand", (id: string, ...a: unknown[]) => {
      ran.push(id);
      return realExec.call(vscode.commands, id, ...a);
    });
    try {
      await vscode.env.clipboard.writeText("sentinel");
      const cs = await exec("throughline.sendToAgent", { inputs: [""] });
      noErrors();
      const pack = toolText(only(cs, isMcp, "/mcp").body);
      assert.ok(seen.infos.includes("Context sent to Cline."));
      assert.equal(await vscode.env.clipboard.readText(), pack, "clipboard holds the pack");
      assert.ok(atAdd && scratch, "cline.addToChat ran with an editor in front");
      assert.ok(atAdd.untitled, "the scratch tab is in front");
      assert.equal(atAdd.text, pack, "the scratch tab holds the pack");
      assert.equal(atAdd.selected, pack, "the whole pack is selected");
      assert.ok(ran.includes("workbench.action.revertAndCloseActiveEditor"), "closed with revert");
      assert.ok(!ran.includes("workbench.action.closeActiveEditor"), "no plain close, which asks to save");
      assert.ok(!ran.includes("editor.action.selectAll"), "no selectAll on whatever editor is active");
      const doc = scratch;
      assert.ok(!vscode.window.visibleTextEditors.some((e) => e.document === doc), "scratch tab closed");
      return `pack of ${pack.length} characters selected (dirty at hand-over: ${atAdd.dirty}); commands: ${ran.join(", ")}`;
    } finally {
      stub(vscode.commands, "executeCommand", realExec);
      fake.dispose();
    }
  });

  await step("openUi", async () => {
    const cs = await exec("throughline.openUi");
    noErrors();
    assert.equal(cs.length, 0, "no request");
    assert.equal(seen.opened.length, 1);
    assert.equal(new URL(seen.opened[0]).origin, new URL(BASE).origin);
    return `opened ${seen.opened[0]}`;
  });

  await step("refreshStatus and showEmbeddingStatus", async () => {
    let cs = await exec("throughline.refreshStatus");
    assert.equal(only(cs, isMcp, "/mcp").status, 200);
    cs = await exec("throughline.showEmbeddingStatus");
    noErrors();
    assert.equal(only(cs, (x) => x.url === `${BASE}/api/health-recovery`, "/api/health-recovery").status, 200);
    assert.ok(seen.infos[0]?.startsWith("Throughline. "));
    return seen.infos[0];
  });

  await step("V1: 10 queries, extension clients vs curl", async () => {
    const mcp = new McpClient(BASE, async () => KEY);
    const api = new ApiClient(BASE);
    const lines: string[] = [];
    let same = 0;
    for (const [q, project] of QUERIES) {
      const args = project ? { query: q, project } : { query: q };
      const viaMcp = hitIds(await mcp.call("recall", args));
      const curlMcp = hitIds(curlRecallMcp(args));
      const viaApi = (await api.recall(q, { project, rank: "fulltext" })).hits.map((h) => h.id);
      const p = new URLSearchParams({ q });
      if (project) p.set("project", project);
      p.set("rank", "fulltext");
      const curlApi = hitIds(curl([`${BASE}/api/recall?${p}`]));
      const ok = JSON.stringify(viaMcp) === JSON.stringify(curlMcp) && JSON.stringify(viaApi) === JSON.stringify(curlApi);
      if (ok) same++;
      lines.push(`  ${ok ? "same" : "DIFF"} "${q}"${project ? ` [${project}]` : ""} mcp=${viaMcp.join(",")} curl=${curlMcp.join(",")} api=${viaApi.join(",")} curl=${curlApi.join(",")}`);
    }
    console.log(lines.join("\n"));
    assert.equal(same, QUERIES.length, `${same} of ${QUERIES.length} identical`);
    return `${same} of ${QUERIES.length} identical (client ${CLIENT_NAME} ${CLIENT_VERSION})`;
  });

  await step("V6: every request went to the configured address", async () => {
    const outside = calls.filter((c) => !c.url.startsWith(`${BASE}/`));
    assert.deepEqual(outside.map((c) => c.url), []);
    const ext = calls.filter((c) => c.who === "extension");
    assert.ok(ext.filter((c) => c.url.startsWith(`${BASE}/api/`)).every((c) => !c.auth), "/api never carries the key");
    assert.ok(ext.filter(isMcp).every((c) => c.auth), "/mcp always carries the key");
    const hosts = [...new Set(calls.map((c) => new URL(c.url).host))];
    return `${calls.length} requests (${ext.length} from the extension), hosts: ${hosts.join(", ")}`;
  });
}

export async function run(): Promise<void> {
  assert.ok(BASE && KEY, "THROUGHLINE_TEST_URL and THROUGHLINE_TEST_KEY are set");
  const ext = vscode.extensions.getExtension("mkupermann.throughline");
  assert.ok(ext, "extension mkupermann.throughline is loaded");
  await ext.activate();
  const clipboardBefore = await vscode.env.clipboard.readText();
  install();
  try {
    await steps();
  } finally {
    globalThis.fetch = realFetch;
    await vscode.env.clipboard.writeText(clipboardBefore);
  }
  console.log(`\nVS Code ${vscode.version}, Throughline test instance ${BASE}`);
  for (const r of results) console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.step}: ${r.detail}`);
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed} of ${results.length} steps passed`);
  if (failed) throw new Error(`${failed} integration step(s) failed`);
}
