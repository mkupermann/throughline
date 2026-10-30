import { describe, ThroughlineError } from "./errors";

export type StatusState =
  | { kind: "connected"; project?: string }
  | { kind: "no_key" }
  | { kind: "unauthorized" }
  | { kind: "access_off" }
  | { kind: "unreachable"; url: string }
  | { kind: "other"; reason: string };

/** The setting wins when it is not blank, otherwise the workspace folder name. */
export function resolveProject(folderName: string | undefined, setting: string | undefined): string | undefined {
  const s = setting?.trim();
  if (s) return s;
  return folderName?.trim() || undefined;
}

export function statusText(state: StatusState): string {
  switch (state.kind) {
    case "connected":
      return state.project ? `Throughline: ${state.project} · connected` : "Throughline: connected";
    case "no_key":
      return "Throughline: not connected (no key, run Throughline: Set Agent Key)";
    case "unauthorized":
      return "Throughline: not connected (key rejected)";
    case "access_off":
      return "Throughline: not connected (agent access is off on the server)";
    case "unreachable":
      return `Throughline: not connected (server unreachable at ${state.url})`;
    case "other":
      return `Throughline: not connected (${state.reason})`;
  }
}

export function stateFromError(e: unknown, url: string): StatusState {
  if (e instanceof ThroughlineError) {
    switch (e.kind) {
      case "no_key":
      case "unauthorized":
      case "access_off":
        return { kind: e.kind };
      case "unreachable":
        return { kind: "unreachable", url };
    }
  }
  return { kind: "other", reason: describe(e) };
}
