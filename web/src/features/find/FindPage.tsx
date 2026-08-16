import { useEffect, useRef, useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Download, Info, LayoutList, MessageCircleQuestion, Network, OctagonAlert, Rows3, Search, X } from "lucide-react";

import { findApi, type ApiError } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { FacetRail } from "./FacetRail";
import { ResultList, routeFor } from "./ResultList";
import { AskPanel } from "./AskPanel";
import { ResultPreview, previewTarget } from "./ResultPreview";
import { ResultTable } from "./ResultTable";
import { ResultGraph } from "./ResultGraph";
import { toApiParams, useFindState } from "./useFindState";
import { downloadCsv } from "./exportCsv";

/** Debounce so every keystroke does not become a query. */
function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function FindPage() {
  const { state, update, toggle, clearAll, activeFilterCount } = useFindState();
  const [draft, setDraft] = useState(state.q);
  const debounced = useDebounced(draft, 220);
  const inputRef = useRef<HTMLInputElement>(null);

  // The URL is the source of truth; the input is a view of it. Typing pushes
  // into the URL (replace, so one search is one history entry rather than
  // one per keystroke), and back/forward pushes into the input.
  useEffect(() => {
    if (debounced !== state.q) update({ q: debounced }, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);
  useEffect(() => {
    setDraft(state.q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.q]);

  // "/" focuses search from anywhere on the page.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const typing =
        el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
      if (e.key === "/" && !typing) {
        e.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // ── Moving through results without leaving the page ──────────────────
  // j/k (and the arrows, for readers who do not know the vi bindings) move the
  // selection; Enter opens it in full. The preview follows the selection, so
  // reading twenty results costs no navigation at all.
  const [selected, setSelected] = useState(0);

  const params = toApiParams(state);
  const hasActiveQuery = state.q.trim().length > 0 || activeFilterCount > 0;
  const { data, isFetching, isPending, error } = useQuery({
    queryKey: ["find", params.toString()],
    queryFn: () => findApi.search(params),
    // Browsing counts as a query: with filters set and no text, the API
    // returns a time-ordered listing rather than nothing.
    enabled: hasActiveQuery,
    // Keep the previous page on screen while the next one loads — a list that
    // blanks on every keystroke is unreadable.
    placeholderData: keepPreviousData,
  });
  const { data: facets } = useQuery({ queryKey: ["facets"], queryFn: findApi.facets });

  const terms = state.q.split(/\s+/).filter((t) => t.length > 1);
  const pageCount = data ? Math.ceil(data.total / state.perPage) : 0;

  const items = data?.items ?? [];
  const listMode = state.mode === "list";
  const current = listMode && items.length > 0 ? (items[selected] ?? items[0]) : null;

  // A new result set invalidates the old position. Snapping to the first row
  // rather than clamping is deliberate: after changing the query, "where I
  // was" is meaningless, and the top of the new list is what the reader is
  // looking at.
  useEffect(() => {
    setSelected(0);
  }, [params.toString()]);

  // Prefetch the neighbours of whatever is selected. Without this, holding j
  // is a series of round trips and the panel flashes empty between them — the
  // difference between navigating and waiting. The keys match the preview's
  // own query exactly, so a prefetched record is served from cache rather than
  // refetched.
  const qc = useQueryClient();
  useEffect(() => {
    if (!listMode) return;
    for (const i of [selected + 1, selected - 1]) {
      const neighbour = items[i];
      if (!neighbour) continue;
      const t = previewTarget(neighbour);
      if (!t) continue;
      void qc.prefetchQuery({
        queryKey: ["detail", t.kind, t.id],
        queryFn: () =>
          t.kind === "project" ? findApi.projectByName(t.id) : findApi.detail(t.kind, t.id),
        staleTime: 60_000,
      });
    }
    // `items` is referenced by identity via the query key above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, listMode, params.toString()]);

  const navigate = useNavigate();
  useEffect(() => {
    if (!listMode || items.length === 0) return;
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      // Never steal a key from a field the reader is typing in.
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return;
      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        setSelected((i) => Math.min(i + 1, items.length - 1));
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        setSelected((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter" && current) {
        e.preventDefault();
        navigate(routeFor(current));
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [listMode, items.length, current, navigate]);

  return (
    <>
      <header className="page-header">
        <h1 className="page-title">Find</h1>
        <p className="page-subtitle">
          One query across conversations, messages, memory, skills, projects and prompts.
        </p>
      </header>

      <div className="searchbar">
        <Search size={16} aria-hidden className="searchbar-icon" />
        <input
          ref={inputRef}
          type="search"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          // The field is auto-focused, so j and k would otherwise be typed into
          // the query rather than move the selection — the keyboard navigation
          // was unreachable without first clicking somewhere else. Down-arrow
          // hands over to the list (the pattern every search-first tool uses),
          // and Escape lets go without moving.
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              e.currentTarget.blur();
              setSelected(0);
            } else if (e.key === "Escape") {
              e.currentTarget.blur();
            }
          }}
          placeholder={
            state.mode === "ask"
              ? "Ask a question about your history…"
              : "Search everything…   (/ to focus, ↓ for results)"
          }
          aria-label="Search"
          autoFocus
          className="searchbar-input"
        />
        {draft && (
          <button type="button" className="icon-button" onClick={() => setDraft("")} aria-label="Clear search">
            <X size={15} aria-hidden />
          </button>
        )}
        <div className="mode-switch" role="group" aria-label="View mode">
          <button
            type="button"
            className={state.mode === "list" ? "is-on" : ""}
            onClick={() => update({ mode: "list" })}
            aria-pressed={state.mode === "list"}
          >
            <LayoutList size={14} aria-hidden /> List
          </button>
          <button
            type="button"
            className={state.mode === "table" ? "is-on" : ""}
            onClick={() => update({ mode: "table" })}
            aria-pressed={state.mode === "table"}
          >
            <Rows3 size={14} aria-hidden /> Table
          </button>
          <button
            type="button"
            className={state.mode === "graph" ? "is-on" : ""}
            onClick={() => update({ mode: "graph" })}
            aria-pressed={state.mode === "graph"}
          >
            <Network size={14} aria-hidden /> Graph
          </button>
          {/* Not a fourth view of the same rows — a different question. List,
              Table and Graph all show what matched; Ask says what it means. */}
          <button
            type="button"
            className={state.mode === "ask" ? "is-on" : ""}
            onClick={() => update({ mode: "ask" })}
            aria-pressed={state.mode === "ask"}
          >
            <MessageCircleQuestion size={14} aria-hidden /> Ask
          </button>
        </div>
      </div>

      <div className="find-layout">
        <FacetRail
          facets={facets}
          state={state}
          onToggle={toggle}
          onUpdate={update}
          onClear={clearAll}
          activeCount={activeFilterCount}
        />

        <div className="find-main">
          {state.mode === "ask" && <AskPanel question={state.q} />}

          {state.mode !== "ask" && hasActiveQuery && isPending && (
            <p className="muted">Searching…</p>
          )}

          {state.mode !== "ask" && !state.q.trim() && activeFilterCount === 0 && (
            <div className="empty-state">
              <Search size={22} aria-hidden />
              <h2>Search or browse your memory</h2>
              <p>
                Type to search across every record type at once — text matching always
                runs, and meaning-based matching joins in when an embedding backend is
                configured. Or pick a filter on the left to browse by time instead.
              </p>
            </div>
          )}

          {state.mode !== "ask" && error && (
            <div className="empty-state">
              <OctagonAlert size={22} aria-hidden />
              <h2>Search failed</h2>
              <p>{(error as ApiError).message}</p>
              {(error as ApiError).hint && <p className="empty-hint">{(error as ApiError).hint}</p>}
            </div>
          )}

          {state.mode !== "ask" && data && (
            <>
              <div className="result-bar">
                <span aria-live="polite">
                  <strong className="tabular">{formatCount(data.total)}</strong>{" "}
                  {data.total === 1 ? "result" : "results"}
                  {isFetching && <span className="result-loading"> · updating…</span>}
                </span>
                <span className="result-actions">
                  {data.items.length > 0 && (
                    <button
                      type="button"
                      className="linkbutton"
                      onClick={() => downloadCsv(data.items)}
                      title="Export the results on this page as CSV"
                    >
                      <Download size={12} aria-hidden />
                      CSV
                    </button>
                  )}
                </span>
                <span className="result-modes">
                  {data.modes.includes("browse")
                    ? "newest first"
                    : data.modes.includes("semantic")
                      ? "text + meaning"
                      : "text only"}
                </span>
              </div>

              {/* Two different things can make a result set incomplete, and
                  conflating them misleads: a *search* can be missing its
                  semantic half, and a *browse* can be capped per type. Only
                  the first is about text matching. */}
              {data.modes.includes("lexical") && !data.backend.available && data.total > 0 && (
                <div className="disclosure">
                  <Info size={15} aria-hidden />
                  <div>
                    <strong>Text matching only.</strong>{" "}
                    {data.notes.find((n) => n.includes("Semantic")) ?? data.notes[0]}
                  </div>
                </div>
              )}
              {data.modes.includes("browse") && data.notes.length > 0 && (
                <div className="disclosure">
                  <Info size={15} aria-hidden />
                  <div>
                    <strong>Browsing by time.</strong> {data.notes[0]}
                  </div>
                </div>
              )}

              {data.total === 0 ? (
                <div className="empty-state">
                  <h2>No results for “{state.q}”</h2>
                  <p>
                    {activeFilterCount > 0
                      ? "Try removing some filters, or search for a different term."
                      : "Try a different term. Text matching looks for the literal string."}
                  </p>
                  {activeFilterCount > 0 && (
                    <button type="button" className="button" onClick={clearAll}>
                      Clear {activeFilterCount} filter{activeFilterCount === 1 ? "" : "s"}
                    </button>
                  )}
                </div>
              ) : state.mode === "table" ? (
                <ResultTable items={data.items} />
              ) : state.mode === "graph" ? (
                <ResultGraph items={data.items} />
              ) : (
                <ResultList
                  items={data.items}
                  terms={terms}
                  selected={selected}
                  onSelect={setSelected}
                />
              )}

              {pageCount > 1 && (state.mode === "list" || state.mode === "table") && (
                <nav className="pager" aria-label="Pagination">
                  <button
                    type="button"
                    className="button"
                    disabled={state.page === 0}
                    onClick={() => update({ page: state.page - 1 })}
                  >
                    Previous
                  </button>
                  <span className="tabular">
                    Page {state.page + 1} of {pageCount}
                  </span>
                  <button
                    type="button"
                    className="button"
                    disabled={state.page + 1 >= pageCount}
                    onClick={() => update({ page: state.page + 1 })}
                  >
                    Next
                  </button>
                </nav>
              )}
            </>
          )}
        </div>

        {/* Third column, list mode only. Table has its own row semantics and
            Graph is a canvas — neither has a "current row" to preview. */}
        {listMode && data && data.total > 0 && <ResultPreview item={current} />}
      </div>
    </>
  );
}
