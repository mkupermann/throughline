import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { AlertTriangle, CheckCircle2, Info, OctagonAlert } from "lucide-react";

import { curateApi, type ActResult, type CurateItem, type QueueSummary } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { useToast } from "@/components/Toaster";
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
}: {
  queue: QueueSummary;
  active: boolean;
  onSelect: () => void;
}) {
  const Icon = SEVERITY_ICON[queue.severity as keyof typeof SEVERITY_ICON] ?? Info;
  return (
    <button
      type="button"
      className={`queue-tab${active ? " is-on" : ""}`}
      onClick={onSelect}
      aria-current={active ? "true" : undefined}
    >
      <Icon size={14} aria-hidden className={`sev-${queue.severity}`} />
      <span className="queue-tab-title">{queue.title}</span>
      <span className="queue-tab-count tabular">{formatCount(queue.count)}</span>
    </button>
  );
}

export function CuratePage() {
  const [sp, setSp] = useSearchParams();
  const active = sp.get("queue") ?? "low-confidence";
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const toast = useToast();
  const qc = useQueryClient();

  const { data: queues } = useQuery({ queryKey: ["curate", "queues"], queryFn: curateApi.queues });
  const { data: queue, isPending } = useQuery({
    queryKey: ["curate", "queue", active],
    queryFn: () => curateApi.queue(active),
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

  const run = (action: string) => {
    const ids = [...selected];
    if (!ids.length) return;
    act.mutate({ action, ids, value: action === "raise_confidence" ? 0.8 : undefined });
  };

  const totalOutstanding = queues?.total ?? 0;

  const body = useMemo(() => {
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
  }, [isPending, items, queue, selected]);

  return (
    <>
      <header className="page-header">
        <h1 className="page-title">Curate</h1>
        <p className="page-subtitle">
          {totalOutstanding === 0
            ? "Every queue is clear."
            : `${formatCount(totalOutstanding)} item${totalOutstanding === 1 ? "" : "s"} across all queues.`}
        </p>
      </header>

      <div className="curate-toolbar">
        <NewChunkForm />
      </div>

      <div className="queue-tabs" role="tablist" aria-label="Curation queues">
        {queues?.queues.map((q) => (
          <QueueTab key={q.name} queue={q} active={q.name === active} onSelect={() => setQueue(q.name)} />
        ))}
      </div>

      {queue && (
        <div className="queue-head">
          <div>
            <h2 className="queue-title">{queue.title}</h2>
            <p className="queue-desc">{queue.description}</p>
          </div>
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
                onClick={() => run(a)}
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
    </>
  );
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
