export type ErrorKind =
  | "no_key"
  | "unauthorized"
  | "access_off"
  | "forbidden"
  | "too_large"
  | "protocol"
  | "invalid"
  | "server"
  | "unreachable";

export const NO_MCP_ENDPOINT = "This Throughline server has no /mcp endpoint. Update the server.";

export class ThroughlineError extends Error {
  readonly kind: ErrorKind;
  readonly detail: string;

  constructor(kind: ErrorKind, message: string, detail = "") {
    super(message);
    this.name = "ThroughlineError";
    this.kind = kind;
    this.detail = detail;
  }
}

/** A short English sentence for a user, for any thrown value. */
export function describe(e: unknown): string {
  if (e instanceof ThroughlineError) {
    return e.detail && !e.message.includes(e.detail) ? `${e.message}: ${e.detail}` : e.message;
  }
  if (e instanceof Error) return e.message;
  return String(e);
}
