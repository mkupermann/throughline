import { useCallback, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { AlertTriangle, CheckCircle2, Info, OctagonAlert, Play } from "lucide-react";

import {
  ApiError,
  curateApi,
  operateApi,
  type ActResult,
  type AuditStatus,
  type CurateItem,
  type QueueSummary,
} from "@/lib/api";
import { formatCount } from "@/lib/format";
import { useToast } from "@/components/Toaster";
import { JobConsole, type JobCompletion } from "@/features/operate/JobConsole";
import { NewChunkForm } from "./NewChunkForm";

const SEVERITY_ICON = { warning: AlertTriangle, info: Info } as const;

const ACTION_LABEL: Record<string, string> = {
  forget: "Forget",
  restore: "Restore",
  raise_confidence: "Set confidence 0.8",
  clear_expiry: "Clear expiry",
  dismiss: "Dismiss",
};

function QueueTab({
  queue,
  active,
  onSelect,
  onKeyDown,
}: {
  queue: QueueSummary;
  active: boolean;
  onSelect: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
}) {
  const Icon = SEVERITY_ICON[queue.severity as keyof typeof SEVERITY_ICON] ?? Info;
  return (
    <button
      id={`queue-tab-${queue.name}`}
      type="button"
      role="tab"
      className={`queue-tab${active ? " is-on" : ""}`}
      onClick={onSelect}
      onKeyDown={onKeyDown}
      aria-selected={active}
      aria-controls="curation-queue-panel"
      tabIndex={active ? 0 : -1}
    >
      <Icon size={14} aria-hidden className={`sev-${queue.severity}`} />
      <span className="queue-tab-title">{queue.title}</span>
      <span className="queue-tab-count tabular">{formatCount(queue.count)}</span>
    </button>
  );
}

export function CuratePage() {
  const [sp, setSp] = useSearchParams();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  //: A destructive action waiting to be confirmed, or null.
  const [pending, setPending] = useState<{ action: string; ids: number[] } | null>(null);
  const toast = useToast();
  const qc = useQueryClient();
  const [auditJobId, setAuditJobId] = useState<string | null>(null);
  const confirmRef = useRef<HTMLDivElement>(null);
  const confirmTriggerRef = useRef<HTMLButtonElement | null>(null);

  const { data: audit, error: auditError } = useQuery({
    queryKey: ["curate", "audit"],
    queryFn: curateApi.audit,
    refetchInterval: (query) =>
      auditJobId || (query.state.data as AuditStatus | undefined)?.job?.running ? 4000 : false,
  });

  const runAudit = useMutation({
    mutationFn: () => operateApi.run("audit-extraction"),
    onSuccess: (result) => setAuditJobId(result.job_id),
    onError: (error) =>
      toast.push({ message: (error as Error).message, tone: "error", duration: 8000 }),
  });

  const auditFinished = useCallback((completion: JobCompletion) => {
    setAuditJobId(null);
    void qc.invalidateQueries({ queryKey: ["curate"] });
    toast.push(
      completion.ok
        ? { message: "Drift audit finished. Review is up to date." }
        : {
            message: "Drift audit failed. Review was not updated.",
            tone: "error",
            duration: 8000,
          },
    );
  }, [qc, toast]);

  const { data: queues, error: queuesError, refetch: refetchQueues } = useQuery({
    queryKey: ["curate", "queues"],
    queryFn: curateApi.queues,
  });

  // An explicit ?queue= always wins — that is the user's own choice, and a
  // shared or bookmarked link must land where it says.
  //
  // Without one, open the first queue that actually holds something, in the
  // order the API returns (which is its priority order). The default used to
  // be the literal "low-confidence", so on a healthy database Curate opened on
  // an empty queue reading "Nothing in this queue" while 474 items waited two
  // tabs away — a worklist showing an empty list is indistinguishable from one
  // with no work in it.
  //
  // Falling back to the first queue when every count is zero is deliberate:
  // that case means there is genuinely nothing to curate, and "Nothing in this
  // queue" is then the correct and honest thing to show.
  const urlQueue = sp.get("queue");
  const resolved =
    urlQueue ?? queues?.queues.find((q) => q.count > 0)?.name ?? queues?.queues[0]?.name;
  const active = resolved ?? "low-confidence";

  // Hold the fetch until the choice is actually knowable. Without `enabled`,
  // the first paint runs before the queue list arrives, fetches whatever the
  // fallback happens to be, then re-fetches the real one — one wasted request
  // and a visible flash of the wrong queue's contents. With an explicit
  // ?queue= there is nothing to wait for, so it fetches immediately.
  const { data: queue, isPending, error: queueError, refetch: refetchQueue } = useQuery({
    queryKey: ["curate", "queue", active],
    queryFn: () => curateApi.queue(active),
    enabled: resolved !== undefined,
  });

  const setQueue = useCallback(
    (name: string) => {
      setSelected(new Set());
      const next = new URLSearchParams(sp);
      next.set("queue", name);
      setSp(next);
    },
    [sp, setSp],
  );

  const queueTabs = queues?.queues ?? [];
  const handleQueueKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % queueTabs.length;
    else if (event.key === "ArrowLeft") {
      nextIndex = (index - 1 + queueTabs.length) % queueTabs.length;
    }
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = queueTabs.length - 1;
    if (nextIndex === null || !queueTabs[nextIndex]) return;

    event.preventDefault();
    const next = queueTabs[nextIndex];
    setQueue(next.name);
    const tabs = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>(
      '[role="tab"]',
    );
    tabs?.[nextIndex]?.focus();
  };

  const items = queue?.items ?? [];
  const allSelected = items.length > 0 && selected.size === items.length;

  const act = useMutation({
    mutationFn: (body: { action: string; ids: number[]; value?: number }) => curateApi.act(body),
    // Optimistic: drop the rows locally and adjust the badge, so the list
    // updates without refetching everything. onError restores the snapshot.
    onMutate: async ({ ids }) => {
      await qc.cancelQueries({ queryKey: ["curate", "queue", active] });
      const prevQueue = qc.getQueryData(["curate", "queue", active]);
      const prevQueues = qc.getQueryData(["curate", "queues"]);
      const idSet = new Set(ids);

      qc.setQueryData(["curate", "queue", active], (old: typeof queue) =>
        old ? { ...old, items: old.items.filter((i) => !idSet.has(i.id)) } : old,
      );
      qc.setQueryData(["curate", "queues"], (old: { queues: QueueSummary[]; total: number } | undefined) =>
        old
          ? {
              ...old,
              total: Math.max(0, old.total - ids.length),
              queues: old.queues.map((q) =>
                q.name === active ? { ...q, count: Math.max(0, q.count - ids.length) } : q,
              ),
            }
          : old,
      );
      return { prevQueue, prevQueues };
    },
    onError: (err, _vars, ctx) => {
      if (ctx?.prevQueue) qc.setQueryData(["curate", "queue", active], ctx.prevQueue);
      if (ctx?.prevQueues) qc.setQueryData(["curate", "queues"], ctx.prevQueues);
      toast.push({ message: (err as Error).message, tone: "error", duration: 8000 });
    },
    onSuccess: (res: ActResult) => {
      setSelected(new Set());
      toast.push({
        message: res.message,
        onUndo: res.undo_token
          ? async () => {
              try {
                const r = await curateApi.undo(res.undo_token!);
                toast.push({ message: r.message });
              } catch (e) {
                toast.push({ message: (e as Error).message, tone: "error", duration: 8000 });
              } finally {
                // Undo changed rows in ways only the server knows; resync the
                // two affected keys rather than guessing.
                qc.invalidateQueries({ queryKey: ["curate"] });
              }
            }
          : undefined,
      });
    },
  });

  // Actions that remove something from view. Undo exists, but a single click
  // applying `forget` to every selected chunk is a decision worth stating
  // before it happens rather than offering to reverse afterwards — and a
  // toast that scrolls away is a poor place to keep the only way back.
  //
  // Deliberately narrow: raising confidence or clearing an expiry are
  // adjustments, and a confirmation on those trains the reader to click
  // through the ones that matter.
  const DESTRUCTIVE = new Set(["forget", "supersede"]);

  const run = (action: string, trigger?: HTMLButtonElement) => {
    const ids = [...selected];
    if (!ids.length) return;
    if (DESTRUCTIVE.has(action)) {
      confirmTriggerRef.current = trigger ?? null;
      setPending({ action, ids });
      return;
    }
    act.mutate({ action, ids, value: action === "raise_confidence" ? 0.8 : undefined });
  };

  const commit = () => {
    if (!pending) return;
    act.mutate({ action: pending.action, ids: pending.ids });
    setPending(null);
    confirmTriggerRef.current?.focus();
  };

  const cancelPending = () => {
    setPending(null);
    confirmTriggerRef.current?.focus();
  };

  const handleConfirmKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      cancelPending();
      return;
    }
    if (event.key !== "Tab") return;

    const focusable = confirmRef.current?.querySelectorAll<HTMLButtonElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable?.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (
      (event.shiftKey && document.activeElement === first) ||
      (!event.shiftKey && document.activeElement === last)
    ) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    }
  };

  const totalOutstanding = queues?.total ?? 0;

  const confirmDialog = pending ? (
    <div className="confirm-scrim" role="presentation" onClick={cancelPending}>
      <div
        ref={confirmRef}
        className="confirm"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-h"
        onKeyDown={handleConfirmKeyDown}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="confirm-h">
          {pending.action === "forget" ? "Forget" : "Supersede"} {pending.ids.length}{" "}
          {pending.ids.length === 1 ? "chunk" : "chunks"}?
        </h2>
        <p>
          {pending.action === "forget"
            ? "They stop being returned by search, by the MCP server, and by Ask. Nothing is erased — the rows stay and this can be undone."
            : "They are marked as replaced and drop out of active memory. Nothing is erased."}
        </p>
        <div className="confirm-actions">
          <button type="button" className="button" onClick={cancelPending} autoFocus>
            Cancel
          </button>
          <button type="button" className="button is-danger" onClick={commit}>
            {pending.action === "forget" ? "Forget" : "Supersede"} {pending.ids.length}
          </button>
        </div>
      </div>
    </div>
  ) : null;

  const body = useMemo(() => {
    if (queueError) {
      const e = queueError as ApiError;
      return (
        <div className="empty-state">
          <OctagonAlert size={22} aria-hidden />
          <h2>Cannot load this queue</h2>
          <p>{e.message}</p>
          {e.hint && <p className="empty-hint">{e.hint}</p>}
          <button type="button" className="button" onClick={() => refetchQueue()}>
            Try again
          </button>
        </div>
      );
    }
    if (isPending) return <div className="skeleton skeleton-row" />;
    if (!items.length) {
      return (
        <div className="empty-state">
          <CheckCircle2 size={22} aria-hidden />
          <h2>Nothing in this queue</h2>
          <p>{queue?.description}</p>
        </div>
      );
    }
    return (
      <ul className="curate-list">
        {items.map((item) => (
          <CurateRow
            key={item.id}
            item={item}
            checked={selected.has(item.id)}
            onToggle={() =>
              setSelected((s) => {
                const next = new Set(s);
                next.has(item.id) ? next.delete(item.id) : next.add(item.id);
                return next;
              })
            }
          />
        ))}
      </ul>
    );
  }, [isPending, items, queue, queueError, refetchQueue, selected]);

  if (queuesError) {
    const e = queuesError as ApiError;
    return (
      <>
        <header className="page-header">
          <h1 className="page-title">Review</h1>
        </header>
        <div className="empty-state">
          <OctagonAlert size={22} aria-hidden />
          <h2>Cannot load curation queues</h2>
          <p>{e.message}</p>
          {e.hint && <p className="empty-hint">{e.hint}</p>}
          <button type="button" className="button" onClick={() => refetchQueues()}>
            Try again
          </button>
        </div>
      </>
    );
  }

  const activeAuditJobId =
    auditJobId ?? (audit?.job?.running ? audit.job.job_id : null);

  return (
    <>
      <header className="page-header">
        <h1 className="page-title">Review</h1>
        <p className="page-subtitle">
          {totalOutstanding === 0
            ? "Every queue is clear."
            : `${formatCount(totalOutstanding)} item${totalOutstanding === 1 ? "" : "s"} across all queues.`}
        </p>
      </header>

      <section className="audit-panel" aria-labelledby="drift-audit-heading">
        <div className="audit-panel-copy">
          <h2 id="drift-audit-heading">Memory drift audit</h2>
          <p>{auditSummary(audit?.last_run ?? null)}</p>
          {audit?.last_run?.created_at && (
            <p className="audit-last-run">
              Last run {new Date(audit.last_run.created_at).toLocaleString("en-US")}
            </p>
          )}
          {auditError && <p className="job-unavailable">Audit status is unavailable.</p>}
          {audit?.job?.unavailable && (
            <p className="job-unavailable">{audit.job.unavailable}</p>
          )}
        </div>
        <button
          type="button"
          className="button audit-run"
          onClick={() => runAudit.mutate()}
          disabled={
            !audit?.job ||
            Boolean(audit.job.unavailable) ||
            audit.job.running ||
            runAudit.isPending ||
            Boolean(auditJobId)
          }
        >
          <Play size={14} aria-hidden />
          {audit?.job?.running || runAudit.isPending || auditJobId
            ? "Audit running…"
            : "Run drift audit"}
        </button>
        {activeAuditJobId && (
          <div className="audit-console">
            <JobConsole jobId={activeAuditJobId} onFinished={auditFinished} />
          </div>
        )}
      </section>

      <div className="curate-toolbar">
        <NewChunkForm />
      </div>

      <div className="queue-tabs" role="tablist" aria-label="Curation queues">
        {queueTabs.map((q, index) => (
          <QueueTab
            key={q.name}
            queue={q}
            active={q.name === active}
            onSelect={() => setQueue(q.name)}
            onKeyDown={(event) => handleQueueKeyDown(event, index)}
          />
        ))}
      </div>

      {resolved !== undefined && (
        <div
          id="curation-queue-panel"
          role="tabpanel"
          aria-labelledby={`queue-tab-${active}`}
          tabIndex={0}
        >
          {queue && (
            <div className="queue-head">
              <div>
                <h2 className="queue-title">{queue.title}</h2>
                <p className="queue-desc">{queue.description}</p>
              </div>
              <span className={`queue-urgency sev-${queue.severity}`}>
                {queue.severity === "warning" ? "Needs review" : "Maintenance"}
              </span>
            </div>
          )}

          {items.length > 0 && (
            <div className="bulkbar">
              <label className="facet-value">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={() =>
                    setSelected(allSelected ? new Set() : new Set(items.map((i) => i.id)))
                  }
                />
                <span>{selected.size ? `${selected.size} selected` : "Select all"}</span>
              </label>
              <div className="bulkbar-actions">
                {(queue?.actions ?? []).map((a) => (
                  <button
                    key={a}
                    type="button"
                    className={`button${a === "forget" ? " is-danger" : ""}`}
                    disabled={!selected.size || act.isPending}
                    onClick={(event) => run(a, event.currentTarget)}
                  >
                    {ACTION_LABEL[a] ?? a}
                  </button>
                ))}
              </div>
            </div>
          )}

          {body}

          {active === "forgotten" && items.length > 0 && (
            <div className="disclosure">
              <OctagonAlert size={15} aria-hidden />
              <div>
                These are soft-deleted and excluded from search, but still in the database.
                Permanent deletion lives under Operate and cannot be undone.
              </div>
            </div>
          )}
        </div>
      )}

      {confirmDialog}
    </>
  );
}

function auditSummary(lastRun: AuditStatus["last_run"]): string {
  if (!lastRun) return "No drift audit has run yet.";
  if (lastRun.state === "no-samples") {
    return "No eligible source-linked memory was available.";
  }
  if (lastRun.drifted === 0) {
    return `No drift found in ${formatCount(lastRun.sampled)} sampled chunks.`;
  }
  if (lastRun.findings_available === false) {
    return `The last legacy audit found ${formatCount(lastRun.drifted)} possible issues. Run again to identify them safely.`;
  }
  return `${formatCount(lastRun.drifted)} of ${formatCount(lastRun.sampled)} sampled chunks need review.`;
}

function CurateRow({
  item,
  checked,
  onToggle,
}: {
  item: CurateItem;
  checked: boolean;
  onToggle: () => void;
}) {
  const text = item.content ?? item.reasoning ?? "";
  return (
    <li className={`curate-row${checked ? " is-selected" : ""}`}>
      <label className="curate-check">
        <input type="checkbox" checked={checked} onChange={onToggle} />
        <span className="sr-only">Select item {item.id}</span>
      </label>
      <div className="curate-body">
        <div className="curate-meta">
          {item.category && <span className="kind kind-memory">{item.category}</span>}
          {item.project_name && <span>{item.project_name}</span>}
          {item.confidence !== null && item.confidence !== undefined && (
            <span className="tabular">conf {Number(item.confidence).toFixed(2)}</span>
          )}
          {item.status && item.status !== "active" && (
            <span className="result-status">{item.status}</span>
          )}
          {item.expires_at && <span>expires {String(item.expires_at).slice(0, 10)}</span>}
          {item.access_count !== null && item.access_count !== undefined && (
            <span className="tabular">reads {item.access_count}</span>
          )}
          <span className="curate-id tabular">#{item.id}</span>
        </div>
        <p className="curate-text">{text}</p>
      </div>
    </li>
  );
}
