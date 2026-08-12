import { useState } from "react";
import { ChevronRight, Terminal, User, Bot, CornerUpRight } from "lucide-react";

/**
 * A conversation as it actually happened — prose, commands, and their output.
 *
 * The detail view used to render `content` and truncate it at 400 characters.
 * On a real session that showed the model's prose and silently dropped
 * everything it did: measured on one 5,560-message transcript, 772 assistant
 * messages have an EMPTY `content` because they are pure tool calls, and 1,853
 * carry `tool_calls`. A reader saw "Read" where a file had been opened and
 * nothing at all where a command had run — which is why the history looked
 * like fragments rather than work.
 *
 * The substance lives in `content_blocks`: a `tool_use` block holds the tool's
 * name and its input (the bash command, the file path, the patch), and a
 * `tool_result` block holds what came back. Both are rendered here.
 *
 * Output is collapsed by default and text is not truncated. Those two choices
 * belong together: a transcript is unreadable if every 40KB of command output
 * is inline, and untrustworthy if any of it is silently cut. Collapsed means
 * the reader chooses; truncated means the tool chose for them.
 */

export interface TranscriptMessage {
  id: number;
  role: string;
  content: string | null;
  content_blocks?: unknown;
  tool_calls?: unknown;
  tool_name?: string | null;
  model?: string | null;
  created_at?: string | null;
}

interface Block {
  type?: string;
  text?: string;
  name?: string;
  input?: Record<string, unknown>;
  content?: unknown;
}

function blocksOf(m: TranscriptMessage): Block[] {
  const raw = m.content_blocks;
  if (Array.isArray(raw)) return raw as Block[];
  // A few adapters store a single block rather than a list.
  if (raw && typeof raw === "object") return [raw as Block];
  return [];
}

/** The one field of a tool's input worth showing first. */
function primaryArg(input: Record<string, unknown> | undefined): string {
  if (!input) return "";
  for (const key of ["command", "file_path", "path", "query", "url", "pattern", "prompt"]) {
    const v = input[key];
    if (typeof v === "string" && v.trim()) return v;
  }
  const first = Object.values(input)[0];
  if (typeof first === "string") return first;
  return first === undefined ? "" : JSON.stringify(first);
}

function textOf(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value
      .map((b) => (typeof b === "string" ? b : ((b as Block)?.text ?? "")))
      .filter(Boolean)
      .join("\n");
  }
  return "";
}

function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  // Date AND time: a transcript without timestamps cannot be placed against
  // anything else that happened that day.
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

/** Output collapsed behind its own first line. */
function Collapsible({ label, body }: { label: string; body: string }) {
  const [open, setOpen] = useState(false);
  const lines = body.split("\n");
  const long = lines.length > 3 || body.length > 400;
  if (!body.trim()) return null;
  if (!long) return <pre className="tx-out">{body}</pre>;
  return (
    <div className="tx-collapse">
      <button type="button" className="tx-toggle" onClick={() => setOpen((o) => !o)}>
        <ChevronRight size={13} aria-hidden className={open ? "tx-caret is-open" : "tx-caret"} />
        {open ? "Hide" : label.replace("{n}", String(lines.length))}
      </button>
      {open && <pre className="tx-out">{body}</pre>}
    </div>
  );
}

function ToolCall({ block }: { block: Block }) {
  const arg = primaryArg(block.input);
  const extra = Object.entries(block.input ?? {}).filter(
    ([, v]) => typeof v !== "string" || v !== arg,
  );
  return (
    <div className="tx-tool">
      <div className="tx-tool-head">
        <Terminal size={13} aria-hidden />
        <span className="tx-tool-name">{block.name ?? "tool"}</span>
      </div>
      {arg && <pre className="tx-arg">{arg}</pre>}
      {extra.length > 0 && (
        <Collapsible label="Show {n} more argument lines" body={JSON.stringify(Object.fromEntries(extra), null, 2)} />
      )}
    </div>
  );
}

export function Transcript({ messages }: { messages: TranscriptMessage[] }) {
  return (
    <ol className="tx">
      {messages.map((m) => {
        const blocks = blocksOf(m);
        const tools = blocks.filter((b) => b.type === "tool_use");
        const results = blocks.filter((b) => b.type === "tool_result");
        const prose =
          blocks
            .filter((b) => b.type === "text")
            .map((b) => b.text ?? "")
            .join("\n\n") || (tools.length || results.length ? "" : (m.content ?? ""));

        const Icon = m.role === "user" ? User : m.role === "tool_result" ? CornerUpRight : Bot;

        return (
          <li key={m.id} className={`tx-msg tx-${m.role}`}>
            <div className="tx-meta">
              <Icon size={13} aria-hidden />
              <span className="tx-role">{m.role}</span>
              {m.model && <span className="tx-model">{m.model}</span>}
              <time className="tx-time">{when(m.created_at)}</time>
            </div>

            {prose && <div className="tx-prose">{prose}</div>}

            {tools.map((b, i) => (
              <ToolCall key={i} block={b} />
            ))}

            {results.map((b, i) => (
              <Collapsible key={i} label="Show output ({n} lines)" body={textOf(b.content)} />
            ))}

            {/* A message with neither prose nor blocks is a real thing in the
                data — a system marker, or a shape no adapter maps yet. Saying
                so beats an empty row the reader cannot account for. */}
            {!prose && tools.length === 0 && results.length === 0 && (
              <div className="tx-empty">({m.tool_name ? `${m.tool_name} — no recorded content` : "no recorded content"})</div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
