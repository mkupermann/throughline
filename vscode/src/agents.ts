export type Agent = "cline" | "continue" | "clipboard";

export interface AgentHost {
  executeCommand(id: string, ...args: unknown[]): Promise<unknown>;
  openScratchDocument(text: string): Promise<void>;
  selectAll(): Promise<void>;
  clipboard(text: string): Promise<void>;
  info(msg: string): void;
}

// NOTE: these are undocumented commands of the Cline and Continue extensions.
// Neither project promises them as a public API, so they may change or vanish.
// Every use below is guarded, and the clipboard is the fallback.
const CLINE_CMD = "cline.addToChat";
const CONTINUE_CMD = "continue.focusContinueInputWithoutClear";

export function detectAgents(commands: string[]): Agent[] {
  const out: Agent[] = [];
  if (commands.includes(CLINE_CMD)) out.push("cline");
  if (commands.includes(CONTINUE_CMD)) out.push("continue");
  out.push("clipboard");
  return out;
}

async function toClipboard(text: string, host: AgentHost, note: string): Promise<"copied"> {
  await host.clipboard(text);
  host.info(note);
  return "copied";
}

export async function sendToAgent(agent: Agent, text: string, host: AgentHost): Promise<"sent" | "copied"> {
  if (agent === "clipboard") {
    return toClipboard(text, host, "Context copied to the clipboard (no Cline or Continue command found).");
  }
  try {
    if (agent === "cline") {
      await host.openScratchDocument(text);
      await host.selectAll();
      await host.executeCommand(CLINE_CMD);
      return "sent";
    }
    await host.clipboard(text);
    await host.executeCommand(CONTINUE_CMD);
    host.info("Context copied. Paste it into Continue with Cmd+V.");
    return "copied";
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    const name = agent === "cline" ? "Cline" : "Continue";
    return toClipboard(text, host, `Context copied to the clipboard, but Throughline could not reach ${name}: ${msg}`);
  }
}
