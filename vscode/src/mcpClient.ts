import { join, send } from "./apiClient";
import { NO_MCP_ENDPOINT, ThroughlineError, redact } from "./errors";

const PROTOCOL_VERSION = "2026-07-28";
export const CLIENT_NAME = "throughline-vscode";
export const CLIENT_VERSION = "0.1.0";

interface RpcError {
  code?: number;
  message?: string;
}

export class McpClient {
  private nextId = 1;

  constructor(
    private readonly baseUrl: string,
    private readonly key: () => Promise<string | undefined>,
  ) {}

  async call(tool: string, args: object): Promise<string> {
    const key = (await this.key())?.trim();
    if (!key) {
      throw new ThroughlineError("no_key", "No agent key is stored. Run \"Throughline: Set Agent Key\".");
    }
    try {
      return await this.request(tool, args, key);
    } catch (e) {
      throw redact(e, key);
    }
  }

  private async request(tool: string, args: object, key: string): Promise<string> {
    const body = JSON.stringify({
      jsonrpc: "2.0",
      id: this.nextId++,
      method: "tools/call",
      params: {
        name: tool,
        arguments: args,
        _meta: {
          "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
          "io.modelcontextprotocol/clientInfo": { name: CLIENT_NAME, version: CLIENT_VERSION },
          "io.modelcontextprotocol/clientCapabilities": {},
        },
      },
    });
    const res = await send(join(this.baseUrl, "/mcp"), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${key}`,
        "Content-Type": "application/json",
        Accept: "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": tool,
      },
      body,
    });
    const text = await res.text();
    let json: { result?: any; error?: RpcError; detail?: string } | undefined;
    try {
      json = JSON.parse(text);
    } catch {
      json = undefined;
    }
    const rpc = json?.error;
    const detail = rpc?.message ?? json?.detail ?? text.slice(0, 300);

    switch (res.status) {
      case 401:
        throw new ThroughlineError("unauthorized", "The agent key is wrong or missing. Set it again.", detail);
      case 503:
        throw new ThroughlineError("access_off", "Agent access is off on the server. Set THROUGHLINE_AGENT_KEY and restart.", detail);
      case 403:
        throw new ThroughlineError("forbidden", "The server refused the request", detail);
      case 413:
        throw new ThroughlineError("too_large", "The request is over the 1 MiB limit", detail);
      default:
        break;
    }
    if (res.status === 404 && !rpc) {
      // An older server without the MCP endpoint; a 404 with a JSON-RPC error comes from /mcp itself.
      throw new ThroughlineError("protocol", NO_MCP_ENDPOINT);
    }
    if (res.status === 400 && (rpc?.code === -32020 || rpc?.code === -32022)) {
      throw new ThroughlineError("protocol", "The server does not speak this protocol version", detail);
    }
    if (rpc) {
      if (rpc.code === -32602) {
        throw new ThroughlineError("invalid", "The server rejected the input or the tool is blocked (read-only mode)", detail);
      }
      throw new ThroughlineError("server", rpc.message ?? "The server reported an error", detail);
    }
    if (!res.ok) {
      throw new ThroughlineError("server", `Throughline answered ${res.status}`, detail);
    }
    const result = json?.result;
    const first = result?.content?.[0];
    if (!result || first?.type !== "text" || typeof first.text !== "string") {
      throw new ThroughlineError("protocol", "The server sent an answer this client does not understand");
    }
    if (result.isError) {
      throw new ThroughlineError("server", first.text);
    }
    return first.text;
  }

  async listProjects(): Promise<string[]> {
    const text = await this.call("list_projects", {});
    try {
      const v: unknown = JSON.parse(text);
      if (Array.isArray(v) && v.every((x) => typeof x === "string")) return v;
    } catch {
      /* fall through */
    }
    throw new ThroughlineError("protocol", "list_projects did not return a list of names", text.slice(0, 200));
  }
}
