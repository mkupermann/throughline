import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Database, Download, History, Play, ShieldCheck, Table2 } from "lucide-react";

import { consoleApi, type ConsoleResult } from "@/lib/api";
import { formatCount } from "@/lib/format";

const HISTORY_KEY = "throughline-console-history";
const MAX_HISTORY = 30;

function loadHistory(): string[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function saveHistory(items: string[]) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, MAX_HISTORY)));
  } catch {
    /* private mode — history is a convenience, not a requirement */
  }
}

function toCsv(result: ConsoleResult): string {
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [result.columns.join(","), ...result.rows.map((r) => r.map(esc).join(","))].join("\n");
}

export function ConsolePage() {
  const [sql, setSql] = useState("SELECT category::text, count(*) AS n\nFROM memory_chunks\nGROUP BY 1 ORDER BY n DESC;");
  const [history, setHistory] = useState<string[]>(loadHistory);
  const [showSchema, setShowSchema] = useState(true);
  const editorRef = useRef<HTMLTextAreaElement>(null);

  const { data: schema } = useQuery({ queryKey: ["console", "schema"], queryFn: consoleApi.schema });

  const run = useMutation({
    mutationFn: (text: string) => consoleApi.query(text),
    onSuccess: (_res, text) => {
      setHistory((h) => {
        const next = [text, ...h.filter((q) => q !== text)].slice(0, MAX_HISTORY);
        saveHistory(next);
        return next;
      });
    },
  });

  const execute = useCallback(() => {
    if (sql.trim()) run.mutate(sql);
  }, [sql, run]);

  // ⌘/Ctrl+Enter runs — the convention in every SQL tool.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        execute();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [execute]);

  const result = run.data;

  return (
    <>
      <header className="page-header">
        <h1 className="page-title">Console</h1>
        <p className="page-subtitle">Read-only SQL against your memory database.</p>
      </header>

      {/* State the guarantee and where it comes from. "Read-only" asserted by
          an app is worth little; enforced by the database is a fact. */}
      <div className="disclosure">
        <ShieldCheck size={15} aria-hidden />
        <div>
          <strong>Read-only.</strong> Every statement runs inside a{" "}
          <code>READ ONLY</code> transaction, so PostgreSQL itself rejects writes — not a
          keyword filter that clever SQL could slip past. Use Curate to change memory.
        </div>
      </div>

      <div className="console-layout">
        <div className="console-main">
          <div className="editor">
            <textarea
              ref={editorRef}
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              spellCheck={false}
              aria-label="SQL query"
              className="editor-area"
              rows={8}
            />
            <div className="editor-bar">
              <button
                type="button"
                className="button"
                onClick={execute}
                disabled={run.isPending || !sql.trim()}
              >
                <Play size={13} aria-hidden />
                {run.isPending ? "Running…" : "Run"}
              </button>
              <kbd className="editor-kbd">⌘↵</kbd>
              {result && !result.error && (
                <>
                  <span className="editor-stat tabular">
                    {formatCount(result.row_count)} row{result.row_count === 1 ? "" : "s"}
                    {result.truncated && " (capped)"}
                  </span>
                  <span className="editor-stat tabular">{result.duration_ms.toFixed(0)} ms</span>
                  <button
                    type="button"
                    className="button"
                    onClick={() => {
                      const blob = new Blob([toCsv(result)], { type: "text/csv" });
                      const a = document.createElement("a");
                      a.href = URL.createObjectURL(blob);
                      a.download = "throughline-query.csv";
                      a.click();
                      URL.revokeObjectURL(a.href);
                    }}
                  >
                    <Download size={13} aria-hidden />
                    CSV
                  </button>
                </>
              )}
            </div>
          </div>

          {run.isError && (
            <div className="console-error">{(run.error as Error).message}</div>
          )}

          {result?.error && (
            <div className="console-error">
              <pre>{result.error}</pre>
              {result.error_hint && <p className="empty-hint">{result.error_hint}</p>}
            </div>
          )}

          {result && !result.error && result.columns.length > 0 && (
            <div className="table-wrap scroll-x">
              <table className="sqltable">
                <thead>
                  <tr>
                    {result.columns.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((row, i) => (
                    <tr key={i}>
                      {row.map((cell, j) => (
                        <td key={j} className={typeof cell === "number" ? "tabular" : undefined}>
                          {cell === null ? <span className="null">null</span> : String(cell)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {result && !result.error && result.truncated && (
            <p className="empty-hint">
              Output capped at {formatCount(result.row_count)} rows. Add a LIMIT to see a
              deliberate slice.
            </p>
          )}
        </div>

        <aside className="console-side">
          <div className="rail-head">
            <span className="section-label" style={{ margin: 0 }}>
              <Table2 size={12} aria-hidden style={{ verticalAlign: "-2px" }} /> Schema
            </span>
            <button type="button" className="rail-clear" onClick={() => setShowSchema((v) => !v)}>
              {showSchema ? "Hide" : "Show"}
            </button>
          </div>

          {showSchema &&
            schema?.tables.map((t) => (
              <details key={t.name} className="schema-table">
                <summary>
                  <Database size={12} aria-hidden /> {t.name}
                  <span className="tabular">{t.columns.length}</span>
                </summary>
                <ul>
                  {t.columns.map((c) => (
                    <li key={c.name}>
                      <button
                        type="button"
                        className="schema-col"
                        onClick={() => setSql((s) => `${s}${c.name}`)}
                        title={c.type}
                      >
                        {c.name}
                      </button>
                      <span className="schema-type">{c.type}</span>
                    </li>
                  ))}
                </ul>
              </details>
            ))}

          <div className="section-label" style={{ marginTop: "var(--space-4)" }}>
            Starting points
          </div>
          <ul className="snippets">
            {schema?.snippets.map((s) => (
              <li key={s.title}>
                <button type="button" onClick={() => setSql(s.sql)}>
                  {s.title}
                </button>
              </li>
            ))}
          </ul>

          {history.length > 0 && (
            <>
              <div className="section-label" style={{ marginTop: "var(--space-4)" }}>
                <History size={12} aria-hidden style={{ verticalAlign: "-2px" }} /> History
              </div>
              <ul className="snippets">
                {history.slice(0, 8).map((h, i) => (
                  <li key={i}>
                    <button type="button" onClick={() => setSql(h)} title={h}>
                      {h.replace(/\s+/g, " ").slice(0, 44)}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </aside>
      </div>
    </>
  );
}
