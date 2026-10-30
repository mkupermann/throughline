import { test } from "node:test";
import assert from "node:assert/strict";
import { detectAgents, sendToAgent, type AgentHost } from "../src/agents";

function host(failOn?: string) {
  const calls: string[] = [];
  const infos: string[] = [];
  const h: AgentHost = {
    executeCommand: async (id) => {
      calls.push(`exec:${id}`);
      if (id === failOn) throw new Error("boom");
    },
    openScratchDocument: async (t) => void calls.push(`open:${t}`),
    selectAll: async () => void calls.push("selectAll"),
    closeScratchDocument: async () => void calls.push("close"),
    clipboard: async (t) => void calls.push(`clip:${t}`),
    info: (m) => void infos.push(m),
  };
  return { h, calls, infos };
}

test("detect_agents_from_command_list", () => {
  assert.deepEqual(detectAgents(["x"]), ["clipboard"]);
  assert.deepEqual(detectAgents(["cline.addToChat", "y"]), ["cline", "clipboard"]);
  assert.deepEqual(detectAgents(["continue.focusContinueInputWithoutClear", "cline.addToChat"]), ["cline", "continue", "clipboard"]);
});

test("send_to_agent_falls_back_to_clipboard", async () => {
  const { h, calls, infos } = host();
  assert.equal(await sendToAgent("clipboard", "T", h), "copied");
  assert.deepEqual(calls, ["clip:T"]);
  assert.deepEqual(infos, ["Context copied to the clipboard (no Cline or Continue command found)."]);
});

test("send_to_cline_runs_add_to_chat", async () => {
  const { h, calls } = host();
  assert.equal(await sendToAgent("cline", "T", h), "sent");
  assert.deepEqual(calls, ["clip:T", "open:T", "selectAll", "exec:cline.addToChat", "close"]);
});

test("send_to_cline_also_copies_to_clipboard", async () => {
  const { h, calls } = host();
  await sendToAgent("cline", "T", h);
  assert.equal(calls[0], "clip:T");
});

test("send_to_continue_copies_and_focuses", async () => {
  const { h, calls, infos } = host();
  assert.equal(await sendToAgent("continue", "T", h), "copied");
  assert.deepEqual(calls, ["clip:T", "exec:continue.focusContinueInputWithoutClear"]);
  assert.deepEqual(infos, ["Context copied. Paste it into Continue with Cmd+V."]);
});

test("send_to_agent_recovers_when_the_command_throws", async () => {
  const { h, calls, infos } = host("cline.addToChat");
  assert.equal(await sendToAgent("cline", "T", h), "copied");
  assert.equal(calls[calls.length - 1], "clip:T");
  assert.match(infos[0], /could not reach Cline: boom/);
});
