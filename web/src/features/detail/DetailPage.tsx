import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, OctagonAlert } from "lucide-react";

import { findApi, type ApiError } from "@/lib/api";

/** URL prefix -> API kind. Short prefixes keep deep links pasteable. */
export const DETAIL_KINDS = {
  c: "conversation",
  m: "memory",
  e: "entity",
  p: "project",
  s: "skill",
  pr: "prompt",
} as const;

const TITLE: Record<string, string> = {
  conversation: "Conversation",
  memory: "Memory chunk",
  entity: "Entity",
  project: "Project",
  skill: "Skill",
  prompt: "Prompt",
};

/** Fields rendered as their own block rather than in the key/value grid. */
const LONG_FIELDS = new Set(["content", "description", "summary", "reasoning"]);
const HIDDEN_FIELDS = new Set(["id"]);

function renderValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function DetailPage({ kind }: { kind: (typeof DETAIL_KINDS)[keyof typeof DETAIL_KINDS] }) {
  const { id } = useParams();
  const navigate = useNavigate();

  const { data, isPending, error } = useQuery({
    queryKey: ["detail", kind, id],
    // Projects are addressed by name, everything else by numeric id.
    queryFn: () => (kind === "project" ? findApi.projectByName(id!) : findApi.detail(kind, id!)),
    enabled: Boolean(id),
  });

  // Escape goes back — a detail view is a modal in spirit and must always
  // have a keyboard exit.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return;
      if (e.key === "Escape") navigate(-1);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [navigate]);

  if (isPending) {
    return (
      <>
        <div className="skeleton skeleton-headline" />
        <div className="skeleton skeleton-row" />
      </>
    );
  }

  if (error) {
    const e = error as ApiError;
    return (
      <div className="empty-state">
        <OctagonAlert size={22} aria-hidden />
        <h2>{e.status === 404 ? "Not found" : "Could not load"}</h2>
        <p>{e.message}</p>
        <Link to="/find" className="button">
          Back to Find
        </Link>
      </div>
    );
  }

  const record = data.record;
  const longs = Object.entries(record).filter(([k]) => LONG_FIELDS.has(k));
  const shorts = Object.entries(record).filter(
    ([k]) => !LONG_FIELDS.has(k) && !HIDDEN_FIELDS.has(k),
  );

  return (
    <>
      <header className="page-header">
        <button type="button" className="backlink" onClick={() => navigate(-1)}>
          <ArrowLeft size={14} aria-hidden />
          Back
        </button>
        <h1 className="page-title">
          {TITLE[kind]}{" "}
          <span className={kind === "project" ? "detail-id" : "detail-id tabular"}>
            {kind === "project" ? id : `#${id}`}
          </span>
        </h1>
      </header>

      {longs.map(([k, v]) =>
        v ? (
          <section key={k} className="detail-long">
            <h2 className="section-label">{k}</h2>
            <p>{renderValue(v)}</p>
          </section>
        ) : null,
      )}

      <section>
        <h2 className="section-label">Fields</h2>
        <dl className="detail-grid">
          {shorts.map(([k, v]) => (
            <div key={k} className="detail-field">
              <dt>{k}</dt>
              <dd className={typeof v === "number" ? "tabular" : undefined}>{renderValue(v)}</dd>
            </div>
          ))}
        </dl>
      </section>

      {Object.entries(data.related ?? {}).map(([name, rowsList]) =>
        rowsList.length ? (
          <section key={name} className="stack-top">
            <h2 className="section-label">
              {name} <span className="tabular">({rowsList.length})</span>
            </h2>
            <ul className="results">
              {rowsList.slice(0, 200).map((row, i) => (
                <li key={i} className="result">
                  <div className="result-link">
                    <div className="result-head">
                      {"role" in row && <span className="kind kind-message">{String(row.role)}</span>}
                      {Boolean(row.category) && (
                        <span className="kind kind-memory">{String(row.category)}</span>
                      )}
                      {"other_name" in row && (
                        <span className="result-title">{String(row.other_name)}</span>
                      )}
                    </div>
                    <p className="result-snippet">
                      {String(row.content ?? row.relation_type ?? "").slice(0, 400)}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
            {rowsList.length > 200 && (
              <p className="empty-hint">Showing the first 200 of {rowsList.length}.</p>
            )}
          </section>
        ) : null,
      )}
    </>
  );
}
