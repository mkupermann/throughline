import { useRef } from "react";
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

function Row({ item, onOpen }: { item: FindItem; onOpen: (i: FindItem) => void }) {
  return (
    <div
      role="row"
      tabIndex={0}
      className="trow"
      style={{ gridTemplateColumns: template }}
      onClick={() => onOpen(item)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(item);
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
}

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
                <Row item={items[v.index]} onOpen={open} />
              </div>
            ))}
          </div>
        ) : (
          items.map((item) => <Row key={`${item.kind}-${item.id}`} item={item} onOpen={open} />)
        )}
      </div>
    </div>
  );
}
