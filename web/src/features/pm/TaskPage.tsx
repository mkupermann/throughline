/** /pm/tasks/:id — Task-Drilldown: header with status/tokens/budget and the
 *  Stop control, the rendered SPEC, and the iteration timeline with verdict
 *  badges and on-demand log excerpts. Polls every 4s while the task runs and
 *  stops polling on a terminal status. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { ChevronDown, ChevronRight, Square } from "lucide-react";

import { pmApi, type PmTask, type PmTaskEvent } from "@/lib/api";
import {
  Breadcrumbs,
  BudgetBar,
  ErrorState,
  Markdown,
  SkeletonRows,
  TERMINAL_STATUSES,
  TaskStatusChip,
  fmtInt,
  fmtRelative,
} from "./shared";
import "@/styles/pm.css";

const POLL_MS = 4000;

/** PASS/FAIL out of a verdict text, plus the reasoning without the marker
 *  line's prefix. Verdict files sometimes carry prose before the marker or
 *  none at all — then the whole text is reasoning and there is no badge. */
function parseVerdictText(text: string | null | undefined): {
  result: "pass" | "fail" | null;
  reasoning: string;
} {
  if (!text) return { result: null, reasoning: "" };
  const m = /VERDICT:\s*(PASS|FAIL):?\s*/.exec(text);
  if (!m) return { result: null, reasoning: text.trim() };
  const reasoning = (text.slice(0, m.index) + text.slice(m.index + m[0].length)).trim();
  return { result: m[1] === "PASS" ? "pass" : "fail", reasoning };
}

function VerdictBadge({ result }: { result: "pass" | "fail" }) {
  return (
    <span className={`pm-verdict pm-verdict-${result}`}>
      {result === "pass" ? "BESTANDEN" : "ABGELEHNT"}
    </span>
  );
}

/** One iteration's log excerpt, fetched only when opened. The endpoint can
 *  fail for individual iterations (e.g. an unreadable verdict file on the
 *  server side) — that failure stays inside this card. */
function LogExcerpt({ taskId, iteration }: { taskId: number; iteration: number }) {
  const { data, isPending, error, refetch } = useQuery({
    queryKey: ["pm-iteration-log", taskId, iteration],
    queryFn: () => pmApi.iterationLog(taskId, iteration, 200),
    staleTime: 60_000,
    retry: false,
  });

  if (isPending) return <div className="skeleton pm-log-skeleton" aria-label="Log lädt" />;

  if (error) {
    return (
      <div className="pm-log-error" role="alert">
        <p>Log-Auszug kann nicht geladen werden (Server meldet einen Fehler für diese Iteration).</p>
        <button type="button" className="pm-linklike" onClick={() => refetch()}>
          Erneut versuchen
        </button>
      </div>
    );
  }

  return (
    <div className="pm-log">
      <div className="pm-log-caption">Letzte 200 Zeilen von executor-{iteration}.log</div>
      <pre className="pm-log-pre">{data.log_tail || "(leer)"}</pre>
    </div>
  );
}

interface IterationView {
  n: number;
  tokens: number | null;
  verdictText: string | null;
  recordedAt: string | null;
}

/** The timeline is derived from the recorded events, but rendered for EVERY
 *  iteration from 1 to the highest one seen — an adopted run's history is on
 *  disk even where the event backfill has gaps, and a numbered card with an
 *  on-demand log excerpt is still useful for those. */
function buildIterations(events: PmTaskEvent[]): IterationView[] {
  const byIter = new Map<number, IterationView>();
  let max = 0;
  for (const e of events) {
    if (e.iteration === null) continue;
    max = Math.max(max, e.iteration);
    const view = byIter.get(e.iteration) ?? {
      n: e.iteration,
      tokens: null,
      verdictText: null,
      recordedAt: null,
    };
    if (e.step === "executor" && e.event_type === "log_update") {
      view.tokens = e.tokens_used;
      view.recordedAt = e.created_at;
    }
    if (e.step === "tester" && e.event_type === "verdict") {
      view.verdictText = e.message;
    }
    byIter.set(e.iteration, view);
  }
  const out: IterationView[] = [];
  for (let n = max; n >= 1; n -= 1) {
    out.push(byIter.get(n) ?? { n, tokens: null, verdictText: null, recordedAt: null });
  }
  return out;
}

function IterationCard({
  taskId,
  it,
  isLatest,
  running,
}: {
  taskId: number;
  it: IterationView;
  isLatest: boolean;
  running: boolean;
}) {
  const [open, setOpen] = useState(false);
  const verdict = parseVerdictText(it.verdictText);
  const live = isLatest && running;

  return (
    <li
      className="pm-iter"
      data-verdict={verdict.result ?? undefined}
      data-live={live ? "true" : undefined}
    >
      <span className="pm-iter-node" aria-hidden />
      <div className="pm-iter-card">
        <div className="pm-iter-head">
          <span className="pm-iter-n tabular">Iteration {it.n}</span>
          {live && <span className="pm-status pm-status-running">läuft</span>}
          <span className="pm-iter-tokens tabular">
            {it.tokens !== null ? `${fmtInt(it.tokens)} Tokens` : "Tokens unbekannt"}
          </span>
          {verdict.result && <VerdictBadge result={verdict.result} />}
        </div>
        {verdict.reasoning && <p className="pm-iter-reason">{verdict.reasoning}</p>}
        <button
          type="button"
          className="pm-iter-logtoggle"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          {open ? (
            <ChevronDown size={14} aria-hidden />
          ) : (
            <ChevronRight size={14} aria-hidden />
          )}
          {open ? "Log ausblenden" : "Log ansehen"}
        </button>
        {open && <LogExcerpt taskId={taskId} iteration={it.n} />}
      </div>
    </li>
  );
}

function SpecPanel({ spec }: { spec: string }) {
  return (
    <details className="pm-spec">
      <summary>Spezifikation (SPEC.md)</summary>
      <div className="pm-spec-body">
        <Markdown text={spec} />
      </div>
    </details>
  );
}

function TaskHeader({
  task,
  projectName,
  teamBudget,
  projectBudget,
}: {
  task: PmTask;
  projectName: string | undefined;
  teamBudget: number | null;
  projectBudget: number | null;
}) {
  const queryClient = useQueryClient();
  const stop = useMutation({
    mutationFn: () => pmApi.stop(task.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pm-task", task.id] });
      queryClient.invalidateQueries({ queryKey: ["pm-project-tasks", task.pm_project_id] });
    },
  });

  // The strictest applicable budget drives the gauge; without any budget the
  // spend stands alone.
  const budgets = [
    { label: "Team-Budget", value: teamBudget },
    { label: "Projekt-Budget", value: projectBudget },
  ].filter((b): b is { label: string; value: number } => b.value !== null);
  const strictest =
    budgets.length > 0
      ? budgets.reduce((a, b) => (b.value < a.value ? b : a))
      : null;

  const canStop = task.status === "running" && task.pid !== null;

  return (
    <header className="page-header">
      <Breadcrumbs
        items={[
          { label: "Project Management", to: "/pm" },
          { label: projectName ?? "Projekt", to: `/pm/projects/${task.pm_project_id}` },
          { label: task.title },
        ]}
      />
      <div className="page-header-row pm-header-row">
        <div className="pm-cockpit-title">
          <h1 className="page-title">{task.title}</h1>
          <TaskStatusChip status={task.status} />
        </div>
        {canStop && (
          <button
            type="button"
            className="button pm-button-danger"
            onClick={() => stop.mutate()}
            disabled={stop.isPending}
          >
            <Square size={13} aria-hidden />
            {stop.isPending ? "Stoppt…" : "Task stoppen"}
          </button>
        )}
      </div>
      <div className="pm-cockpit-meta">
        <span className="tabular">{fmtInt(task.tokens_used)} Tokens</span>
        <span aria-hidden>·</span>
        <span>
          {task.status === "running"
            ? `gestartet ${fmtRelative(task.started_at)}`
            : task.ended_at
              ? `beendet ${fmtRelative(task.ended_at)}`
              : `angelegt ${fmtRelative(task.started_at)}`}
        </span>
        <span aria-hidden>·</span>
        <span className="pm-task-runid">
          Run <code>{task.run_id}</code>
        </span>
      </div>
      {stop.isError && (
        <p className="pm-field-error" role="alert">
          Stoppen fehlgeschlagen: {(stop.error as Error).message}
        </p>
      )}
      {strictest && (
        <div className="pm-cockpit-budgetbar">
          <BudgetBar used={task.tokens_used} budget={strictest.value} label={strictest.label} />
        </div>
      )}
    </header>
  );
}

export function TaskPage() {
  const { id } = useParams<{ id: string }>();
  const taskId = Number(id);

  const task = useQuery({
    queryKey: ["pm-task", taskId],
    queryFn: () => pmApi.getTask(taskId),
    refetchInterval: (query) =>
      query.state.data && TERMINAL_STATUSES.includes(query.state.data.status)
        ? false
        : POLL_MS,
  });

  const terminal = task.data ? TERMINAL_STATUSES.includes(task.data.status) : false;

  const events = useQuery({
    queryKey: ["pm-task-events", taskId],
    queryFn: () => pmApi.taskEvents(taskId),
    refetchInterval: terminal ? false : POLL_MS,
  });

  const projects = useQuery({ queryKey: ["pm-projects"], queryFn: pmApi.listProjects });
  const teams = useQuery({
    queryKey: ["pm-project-teams", task.data?.pm_project_id],
    queryFn: () => pmApi.projectTeams(task.data!.pm_project_id),
    enabled: task.data !== undefined,
  });

  if (task.isPending) {
    return (
      <section className="pm-page">
        <header className="page-header">
          <Breadcrumbs items={[{ label: "Project Management", to: "/pm" }, { label: "Task" }]} />
        </header>
        <SkeletonRows n={4} header />
      </section>
    );
  }

  if (task.error) {
    return (
      <section className="pm-page">
        <header className="page-header">
          <Breadcrumbs items={[{ label: "Project Management", to: "/pm" }, { label: "Task" }]} />
        </header>
        <ErrorState
          title="Task kann nicht geladen werden"
          error={task.error}
          onRetry={task.refetch}
        />
      </section>
    );
  }

  const t = task.data;
  const project = projects.data?.projects.find((p) => p.id === t.pm_project_id);
  const team = teams.data?.teams.find((x) => x.id === t.team_id);
  const eventList = events.data?.events ?? [];

  const spec = eventList.find((e) => e.step === "analyst" && e.event_type === "started")?.message;
  const errors = eventList.filter((e) => e.event_type === "error");
  const iterations = buildIterations(eventList);

  return (
    <section className="pm-page pm-task-page">
      <TaskHeader
        task={t}
        projectName={project?.name}
        teamBudget={team?.token_budget ?? null}
        projectBudget={project?.token_budget ?? null}
      />

      {spec && <SpecPanel spec={spec} />}

      {errors.length > 0 && (
        <div className="pm-task-errors" role="alert">
          {errors.map((e) => (
            <p key={e.id}>
              <strong>Hinweis:</strong> {e.message ?? "Fehler ohne Meldung"} (
              {fmtRelative(e.created_at)})
            </p>
          ))}
        </div>
      )}

      <section className="pm-section" aria-labelledby="pm-iter-h">
        <h2 id="pm-iter-h" className="section-label">
          Iterationen
        </h2>
        {events.isPending ? (
          <SkeletonRows n={3} />
        ) : events.error ? (
          <ErrorState
            title="Iterationen können nicht geladen werden"
            error={events.error}
            onRetry={events.refetch}
          />
        ) : iterations.length === 0 ? (
          <p className="pm-task-list-none">
            Noch keine Iteration aufgezeichnet
            {t.status === "running" ? " — die Pipeline läuft an, die Seite aktualisiert sich selbst." : "."}
          </p>
        ) : (
          <ol className="pm-iter-list">
            {iterations.map((it, idx) => (
              <IterationCard
                key={it.n}
                taskId={taskId}
                it={it}
                isLatest={idx === 0}
                running={t.status === "running"}
              />
            ))}
          </ol>
        )}
      </section>
    </section>
  );
}
