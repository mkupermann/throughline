import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { OctagonAlert } from "lucide-react";

import { findApi, type ApiError } from "@/lib/api";
import { ConversationDetail } from "./ConversationDetail";
import { EntityDetail, MemoryDetail, PromptDetail, SkillDetail } from "./RecordDetails";

/** URL prefix -> API kind. Short prefixes keep deep links pasteable.
 *
 * "project" is deliberately absent: it used to be reachable at `/p/:id`
 * through a generic renderer -- a bare field grid with raw snake_case
 * labels and unformatted ISO timestamps -- while `/project/:name` already
 * routed to the purpose-built ProjectPage for the same entity. Every caller
 * (Find's routeFor, the command palette, Ask citations) now points at
 * ProjectPage directly; see ResultList.tsx's routeFor (UI audit
 * full-app H1). */
export const DETAIL_KINDS = {
  c: "conversation",
  m: "memory",
  e: "entity",
  s: "skill",
  pr: "prompt",
} as const;

export type DetailKind = (typeof DETAIL_KINDS)[keyof typeof DETAIL_KINDS];

/**
 * One record, presented as what it is. Each kind gets a hand-authored page
 * (a conversation reads as a conversation, a memory as a memory) built on
 * one shared layout system: breadcrumb, title block, metadata list, main
 * content, related records, and a collapsed raw-JSON escape hatch. The
 * generic field-grid renderer this replaced was the one auto-generated page
 * in the app — the single largest concentration of "the design system's
 * rules don't reach here" in the UI audit.
 */
export function DetailPage({ kind }: { kind: DetailKind }) {
  const { id } = useParams();
  const navigate = useNavigate();

  const { data, isPending, error, refetch } = useQuery({
    queryKey: ["detail", kind, id],
    queryFn: () => findApi.detail(kind, id!),
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
        <p className="sr-only" role="status">
          Loading…
        </p>
        {/* Mirrors the loaded page's bones — crumb, title, meta row, content —
            so nothing jumps when the data lands. */}
        <div aria-hidden>
          <div className="skeleton detail-skel-crumb" />
          <div className="skeleton skeleton-headline" />
          <div className="skeleton detail-skel-meta" />
          <div className="skeleton detail-skel-content" />
        </div>
      </>
    );
  }

  if (error) {
    const e = error as ApiError;
    const notFound = e.status === 404;
    return (
      <div className="empty-state">
        <OctagonAlert size={22} aria-hidden />
        <h2>{notFound ? "Not found" : "Could not load"}</h2>
        <p>{e.message}</p>
        {e.hint && <p className="empty-hint">{e.hint}</p>}
        {/* A 404 is an answer; anything else is worth one more try. */}
        {!notFound && (
          <button type="button" className="button" onClick={() => refetch()}>
            Try again
          </button>
        )}
        <Link to="/find" className="button">
          Back to Find
        </Link>
      </div>
    );
  }

  const record = data.record;
  const related = (data.related ?? {}) as Record<string, unknown>;
  const rid = id ?? String(record.id ?? "");

  switch (kind) {
    case "conversation":
      return <ConversationDetail id={rid} record={record} related={related} />;
    case "memory":
      return <MemoryDetail id={rid} record={record} />;
    case "skill":
      return <SkillDetail id={rid} record={record} />;
    case "prompt":
      return <PromptDetail id={rid} record={record} />;
    case "entity":
      return <EntityDetail id={rid} record={record} related={related} />;
  }
}
