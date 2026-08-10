import { useEffect, useRef, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { CalendarRange, Download, Info, LayoutList, Network, OctagonAlert, Rows3, Search, X } from "lucide-react";

import { findApi, type ApiError } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { FacetRail } from "./FacetRail";
import { ResultList } from "./ResultList";
import { ResultTable } from "./ResultTable";
import { ResultTimeline } from "./ResultTimeline";
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

  const params = toApiParams(state);
  const { data, isFetching, error } = useQuery({
    queryKey: ["find", params.toString()],
    queryFn: () => findApi.search(params),
    // Browsing counts as a query: with filters set and no text, the API
    // returns a time-ordered listing rather than nothing.
    enabled: state.q.trim().length > 0 || activeFilterCount > 0,
    // Keep the previous page on screen while the next one loads — a list that
    // blanks on every keystroke is unreadable.
    placeholderData: keepPreviousData,
  });
  const { data: facets } = useQuery({ queryKey: ["facets"], queryFn: findApi.facets });

  const terms = state.q.split(/\s+/).filter((t) => t.length > 1);
  const pageCount = data ? Math.ceil(data.total / state.perPage) : 0;

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
          placeholder="Search everything…   (press / to focus)"
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
            className={state.mode === "timeline" ? "is-on" : ""}
            onClick={() => update({ mode: "timeline" })}
            aria-pressed={state.mode === "timeline"}
          >
            <CalendarRange size={14} aria-hidden /> Timeline
          </button>
          <button
            type="button"
            className={state.mode === "graph" ? "is-on" : ""}
            onClick={() => update({ mode: "graph" })}
            aria-pressed={state.mode === "graph"}
          >
            <Network size={14} aria-hidden /> Graph
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
          {!state.q.trim() && activeFilterCount === 0 && (
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

          {error && (
            <div className="empty-state">
              <OctagonAlert size={22} aria-hidden />
              <h2>Search failed</h2>
              <p>{(error as ApiError).message}</p>
              {(error as ApiError).hint && <p className="empty-hint">{(error as ApiError).hint}</p>}
            </div>
          )}

          {data && (
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
              ) : state.mode === "timeline" ? (
                <ResultTimeline items={data.items} />
              ) : state.mode === "graph" ? (
                <ResultGraph items={data.items} />
              ) : (
                <ResultList items={data.items} terms={terms} />
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
      </div>
    </>
  );
}
