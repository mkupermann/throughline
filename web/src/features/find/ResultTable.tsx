import { forwardRef, useEffect, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useNavigate } from "react-router-dom";

import type { FindItem } from "@/lib/api";
import { routeFor } from "./ResultList";

/** Rows above this are virtualized; below it the DOM cost is irrelevant and
 *  a plain table keeps find-in-page working. */
export const VIRTUALIZE_ABOVE = 50;
const ROW_HEIGHT = 36;

const COLUMNS = [
  { key: "kind", label: "Type", width: "110px" },
  { key: "title", label: "Title", width: "minmax(180px, 1.2fr)" },
  { key: "snippet", label: "Snippet", width: "minmax(220px, 2fr)" },
  { key: "project", label: "Project", width: "140px" },
  { key: "category", label: "Category", width: "120px" },
  { key: "occurred_at", label: "Date", width: "110px" },
] as const;

const template = COLUMNS.map((c) => c.width).join(" ");

function cell(item: FindItem, key: (typeof COLUMNS)[number]["key"]): string {
  const v = item[key as keyof FindItem];
  if (v === null || v === undefined) return "";
  if (key === "occurred_at") {
    const d = new Date(String(v));
    return Number.isNaN(d.getTime()) ? "" : d.toISOString().slice(0, 10);
  }
  return String(v).replace(/\s+/g, " ").trim();
}

/** Roving tabindex (ARIA grid keyboard pattern): only the focused row is a
 *  tab stop, and arrow keys move focus between rows without adding tab
 *  stops of their own — reaching row 50 of a 200-row page used to cost 50
 *  real Tab presses past the pager and everything else on the page (UI
 *  audit full-app M2, same root pattern as the PM audit's SkillPicker H2). */
const Row = forwardRef<
  HTMLDivElement,
  {
    item: FindItem;
    focused: boolean;
    onOpen: (i: FindItem) => void;
    onMove: (delta: number | "start" | "end") => void;
  }
>(function Row({ item, focused, onOpen, onMove }, ref) {
  return (
    <div
      ref={ref}
      role="row"
      tabIndex={focused ? 0 : -1}
      className="trow"
      style={{ gridTemplateColumns: template }}
      onClick={() => onOpen(item)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(item);
        } else if (e.key === "ArrowDown") {
          e.preventDefault();
          onMove(1);
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          onMove(-1);
        } else if (e.key === "Home") {
          e.preventDefault();
          onMove("start");
        } else if (e.key === "End") {
          e.preventDefault();
          onMove("end");
        }
      }}
    >
      {COLUMNS.map((c) => (
        <div role="gridcell" key={c.key} className={`tcell${c.key === "occurred_at" ? " tabular" : ""}`}>
          {c.key === "kind" ? (
            <span className={`kind kind-${item.kind}`}>{item.kind}</span>
          ) : (
            cell(item, c.key)
          )}
        </div>
      ))}
    </div>
  );
});

export function ResultTable({ items }: { items: FindItem[] }) {
  const navigate = useNavigate();
  const parentRef = useRef<HTMLDivElement>(null);
  const virtualize = items.length > VIRTUALIZE_ABOVE;

  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
    enabled: virtualize,
  });

  const open = (i: FindItem) => navigate(routeFor(i));

  // The row currently reachable by Tab. Reset whenever the result set
  // itself changes (a new search/filter/page), so focus never lands past
  // the end of a shorter list.
  const [focusIdx, setFocusIdx] = useState(0);
  const rowRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const pendingFocus = useRef(false);

  useEffect(() => {
    setFocusIdx((i) => Math.min(i, Math.max(0, items.length - 1)));
  }, [items]);

  // Runs after a keyboard-initiated move only (see `pendingFocus`) — moving
  // the mouse over a row, or the initial render, must not steal focus onto
  // the grid.
  useEffect(() => {
    if (!pendingFocus.current) return;
    pendingFocus.current = false;
    if (virtualize) virtualizer.scrollToIndex(focusIdx, { align: "auto" });
    // The virtualizer may need a tick to mount the target row's DOM node.
    requestAnimationFrame(() => rowRefs.current.get(focusIdx)?.focus());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusIdx]);

  const moveFocus = (delta: number | "start" | "end") => {
    pendingFocus.current = true;
    setFocusIdx((i) => {
      const next = delta === "start" ? 0 : delta === "end" ? items.length - 1 : i + delta;
      return Math.max(0, Math.min(items.length - 1, next));
    });
  };

  return (
    <div className="table-wrap scroll-x" role="grid" aria-rowcount={items.length}>
      <div className="thead" role="row" style={{ gridTemplateColumns: template }}>
        {COLUMNS.map((c) => (
          <div role="columnheader" key={c.key}>
            {c.label}
          </div>
        ))}
      </div>

      <div ref={parentRef} className="tbody" data-virtualized={virtualize}>
        {virtualize ? (
          <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {virtualizer.getVirtualItems().map((v) => (
              <div
                key={v.key}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  height: v.size,
                  transform: `translateY(${v.start}px)`,
                }}
              >
                <Row
                  item={items[v.index]}
                  focused={v.index === focusIdx}
                  onOpen={open}
                  onMove={moveFocus}
                  ref={(el) => {
                    if (el) rowRefs.current.set(v.index, el);
                    else rowRefs.current.delete(v.index);
                  }}
                />
              </div>
            ))}
          </div>
        ) : (
          items.map((item, i) => (
            <Row
              key={`${item.kind}-${item.id}`}
              item={item}
              focused={i === focusIdx}
              onOpen={open}
              onMove={moveFocus}
              ref={(el) => {
                if (el) rowRefs.current.set(i, el);
                else rowRefs.current.delete(i);
              }}
            />
          ))
        )}
      </div>
    </div>
  );
}
