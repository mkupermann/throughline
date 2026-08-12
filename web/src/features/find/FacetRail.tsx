import { useState } from "react";
import { ChevronDown, X } from "lucide-react";

import type { FacetValue, Facets } from "@/lib/api";
import { formatCount } from "@/lib/format";
import type { FindState } from "./useFindState";

const KIND_LABEL: Record<string, string> = {
  memory: "Memory",
  message: "Messages",
  conversation: "Conversations",
  skill: "Skills",
  project: "Projects",
  prompt: "Prompts",
};

function FacetGroup({
  title,
  values,
  selected,
  onToggle,
  initiallyOpen = true,
  labels,
  max = 8,
}: {
  title: string;
  values: FacetValue[];
  selected: string[];
  onToggle: (v: string) => void;
  initiallyOpen?: boolean;
  labels?: Record<string, string>;
  max?: number;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  const [showAll, setShowAll] = useState(false);
  if (!values.length) return null;

  const visible = showAll ? values : values.slice(0, max);
  const hidden = values.length - visible.length;

  return (
    <section className="facet">
      <h3>
        <button
          type="button"
          className="facet-head"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          <ChevronDown size={13} aria-hidden className={open ? "" : "rot-270"} />
          <span>{title}</span>
          {selected.length > 0 && <span className="facet-badge tabular">{selected.length}</span>}
        </button>
      </h3>
      {open && (
        <ul className="facet-values">
          {visible.map((v) => {
            const isOn = selected.includes(v.value);
            return (
              <li key={v.value}>
                <label className={`facet-value${isOn ? " is-on" : ""}`}>
                  <input
                    type="checkbox"
                    checked={isOn}
                    onChange={() => onToggle(v.value)}
                  />
                  <span className="facet-value-label">{labels?.[v.value] ?? v.value}</span>
                  <span className="facet-value-count tabular">{formatCount(v.n)}</span>
                </label>
              </li>
            );
          })}
          {hidden > 0 && (
            <li>
              <button type="button" className="facet-more" onClick={() => setShowAll(true)}>
                Show {hidden} more
              </button>
            </li>
          )}
        </ul>
      )}
    </section>
  );
}

export function FacetRail({
  facets,
  state,
  onToggle,
  onUpdate,
  onClear,
  activeCount,
}: {
  facets: Facets | undefined;
  state: FindState;
  onToggle: (facet: "kind" | "category" | "project" | "status" | "tag", v: string) => void;
  onUpdate: (patch: Partial<FindState>) => void;
  onClear: () => void;
  activeCount: number;
}) {
  if (!facets) {
    return (
      <aside className="rail" aria-label="Filters">
        <div className="skeleton skeleton-row" />
        <div className="skeleton skeleton-row" />
      </aside>
    );
  }

  return (
    <aside className="rail" aria-label="Filters">
      <div className="rail-head">
        {/* A real heading, not a styled span: the facet groups below are h3,
            and jumping straight from the page h1 to h3 breaks heading
            navigation for screen-reader users. */}
        <h2 className="section-label" style={{ margin: 0 }}>
          Filters
        </h2>
        {activeCount > 0 && (
          <button type="button" className="rail-clear" onClick={onClear}>
            <X size={12} aria-hidden />
            Clear {activeCount}
          </button>
        )}
      </div>

      <FacetGroup
        title="Type"
        values={facets.kinds}
        selected={state.kinds}
        onToggle={(v) => onToggle("kind", v)}
        labels={KIND_LABEL}
        max={6}
      />
      <FacetGroup
        title="Category"
        values={facets.categories}
        selected={state.categories}
        onToggle={(v) => onToggle("category", v)}
      />
      <FacetGroup
        title="Project"
        values={facets.projects}
        selected={state.projects}
        onToggle={(v) => onToggle("project", v)}
        initiallyOpen={false}
      />
      <FacetGroup
        title="Status"
        values={facets.statuses}
        selected={state.statuses}
        onToggle={(v) => onToggle("status", v)}
        initiallyOpen={false}
      />
      <FacetGroup
        title="Tag"
        values={facets.tags}
        selected={state.tags}
        onToggle={(v) => onToggle("tag", v)}
        initiallyOpen={false}
      />

      <section className="facet">
        <h3>
          <span className="facet-head as-text">Confidence</span>
        </h3>
        <label className="facet-range">
          <input
            type="range"
            min={0}
            max={1}
            step={0.1}
            value={state.minConfidence ?? 0}
            onChange={(e) => {
              const v = Number(e.target.value);
              onUpdate({ minConfidence: v === 0 ? null : v });
            }}
            aria-label="Minimum confidence"
          />
          <span className="tabular">
            {state.minConfidence === null ? "any" : `≥ ${state.minConfidence.toFixed(1)}`}
          </span>
        </label>
      </section>

      <section className="facet">
        <h3>
          <span className="facet-head as-text">Embedding</span>
        </h3>
        <ul className="facet-values">
          {[
            { label: "Any", value: null },
            { label: "Embedded", value: true },
            { label: "Not embedded", value: false },
          ].map((opt) => (
            <li key={String(opt.value)}>
              <label className={`facet-value${state.hasEmbedding === opt.value ? " is-on" : ""}`}>
                <input
                  type="radio"
                  name="has_embedding"
                  checked={state.hasEmbedding === opt.value}
                  onChange={() => onUpdate({ hasEmbedding: opt.value })}
                />
                <span className="facet-value-label">{opt.label}</span>
              </label>
            </li>
          ))}
        </ul>
      </section>
    </aside>
  );
}
