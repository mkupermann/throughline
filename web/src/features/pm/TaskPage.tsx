/** /pm/tasks/:id — Task-Drilldown: header with status/tokens/budget and the
 *  Stop control, the rendered SPEC, and the iteration timeline with verdict
 *  badges and on-demand log excerpts. Polls every 4s while the task runs and
 *  stops polling on a terminal status. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { ChevronDown, ChevronRight, Square } from "lucide-react";

import { pmApi, type PmTask, type PmTaskEvent } from "@/lib/api";
import { useLang } from "./i18n";
import {
  BudgetBar,
  ErrorState,
  InlineConfirmButton,
  Markdown,
  PmHeaderBar,
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
  const { t } = useLang();
  return (
    <span className={`pm-verdict pm-verdict-${result}`}>
      {result === "pass" ? t.taskPage.verdictPass : t.taskPage.verdictFail}
    </span>
  );
}

/** One iteration's log excerpt, fetched only when opened. The endpoint can
 *  fail for individual iterations (e.g. an unreadable verdict file on the
 *  server side) — that failure stays inside this card. */
function LogExcerpt({ taskId, iteration }: { taskId: number; iteration: number }) {
  const { t } = useLang();
  const { data, isPending, error, refetch } = useQuery({
    queryKey: ["pm-iteration-log", taskId, iteration],
    queryFn: () => pmApi.iterationLog(taskId, iteration, 200),
    staleTime: 60_000,
    retry: false,
  });

  if (isPending) return <div className="skeleton pm-log-skeleton" aria-label={t.taskPage.logLoading} />;

  if (error) {
    return (
      <div className="pm-log-error" role="alert">
        <p>{t.taskPage.logError}</p>
        <button type="button" className="pm-linklike" onClick={() => refetch()}>
          {t.common.retry}
        </button>
      </div>
    );
  }

  return (
    <div className="pm-log">
      <div className="pm-log-caption">{t.taskPage.logCaption(iteration)}</div>
      <pre className="pm-log-pre">{data.log_tail || t.taskPage.logEmpty}</pre>
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
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const verdict = parseVerdictText(it.verdictText);
  const live = isLatest && running;
  // A verdict event was recorded (it.verdictText is not null) but carried
  // neither a VERDICT: marker nor any reasoning text — an empty or
  // marker-less verdict-N.txt. Rendering nothing here used to look
  // identical to "no verdict yet", which is a different, non-terminal
  // state — this line says plainly that the tester ran and produced
  // nothing usable, rather than leaving silence that reads as a bug.
  const verdictRecordedButEmpty =
    it.verdictText !== null && verdict.result === null && verdict.reasoning.trim() === "";

  return (
    <li
      className="pm-iter"
      data-verdict={verdict.result ?? undefined}
      data-live={live ? "true" : undefined}
    >
      <span className="pm-iter-node" aria-hidden />
      <div className="pm-iter-card">
        <div className="pm-iter-head">
          <span className="pm-iter-n tabular">{t.taskPage.iteration(it.n)}</span>
          {live && <span className="pm-status pm-status-running">{t.status.task.running}</span>}
          <span className="pm-iter-tokens tabular">
            {it.tokens !== null ? `${fmtInt(it.tokens)} ${t.common.tokens}` : t.taskPage.tokensUnknown}
          </span>
          {verdict.result && <VerdictBadge result={verdict.result} />}
        </div>
        {verdict.reasoning && <p className="pm-iter-reason">{verdict.reasoning}</p>}
        {verdictRecordedButEmpty && (
          <p className="pm-iter-reason pm-iter-reason-empty">{t.taskPage.verdictEmpty}</p>
        )}
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
          {open ? t.taskPage.logHide : t.taskPage.logShow}
        </button>
        {open && <LogExcerpt taskId={taskId} iteration={it.n} />}
      </div>
    </li>
  );
}

function SpecPanel({ spec }: { spec: string }) {
  const { t } = useLang();
  return (
    <details className="pm-spec">
      <summary>{t.taskPage.specSummary}</summary>
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
  const { t } = useLang();
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
    { label: t.taskPage.teamBudgetLabel, value: teamBudget },
    { label: t.taskPage.projectBudgetLabel, value: projectBudget },
  ].filter((b): b is { label: string; value: number } => b.value !== null);
  const strictest =
    budgets.length > 0
      ? budgets.reduce((a, b) => (b.value < a.value ? b : a))
      : null;

  // Stop actually kills a process, and pipeline.sh runs Throughline
  // launched itself have one (pid !== null). An adopted run (pid === null,
  // register_existing_run) has nothing of ours to kill — the button still
  // shows so a task that lied about "running" forever can be closed out,
  // just labeled honestly: it only marks the status, it stops nothing.
  const canStop = task.status === "running";
  const adopted = task.pid === null;

  return (
    <header className="page-header">
      <PmHeaderBar
        items={[
          { label: t.common.projectManagement, to: "/pm" },
          { label: projectName ?? t.cockpit.breadcrumbFallback, to: `/pm/projects/${task.pm_project_id}` },
          { label: task.title },
        ]}
      />
      <div className="page-header-row pm-header-row">
        <div className="pm-cockpit-title">
          <h1 className="page-title">{task.title}</h1>
          <TaskStatusChip status={task.status} />
        </div>
        {canStop && (
          <InlineConfirmButton
            className="button pm-button-danger"
            disabled={stop.isPending}
            pending={stop.isPending}
            onConfirm={() => stop.mutate()}
          >
            <Square size={13} aria-hidden />
            {stop.isPending ? t.taskPage.stopping : adopted ? t.taskPage.markEnded : t.taskPage.stop}
          </InlineConfirmButton>
        )}
      </div>
      <div className="pm-cockpit-meta">
        <span className="tabular">{fmtInt(task.tokens_used)} {t.common.tokens}</span>
        <span aria-hidden>·</span>
        <span>
          {task.status === "running"
            ? t.taskPage.startedAt(fmtRelative(task.started_at))
            : task.ended_at
              ? t.taskPage.endedAt(fmtRelative(task.ended_at))
              : t.taskPage.createdAt(fmtRelative(task.started_at))}
        </span>
        <span aria-hidden>·</span>
        <span className="pm-task-runid">
          {t.taskPage.runLabel} <code>{task.run_id}</code>
        </span>
      </div>
      {stop.isError && (
        <p className="pm-field-error" role="alert">
          {t.taskPage.stopFailed((stop.error as Error).message)}
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
  const { t } = useLang();
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
          <PmHeaderBar items={[{ label: t.common.projectManagement, to: "/pm" }, { label: t.taskPage.breadcrumbFallback }]} />
        </header>
        <SkeletonRows n={4} header />
      </section>
    );
  }

  if (task.error) {
    return (
      <section className="pm-page">
        <header className="page-header">
          <PmHeaderBar items={[{ label: t.common.projectManagement, to: "/pm" }, { label: t.taskPage.breadcrumbFallback }]} />
        </header>
        <ErrorState
          title={t.taskPage.errorTitle}
          error={task.error}
          onRetry={task.refetch}
        />
      </section>
    );
  }

  const taskData = task.data;
  const project = projects.data?.projects.find((p) => p.id === taskData.pm_project_id);
  const team = teams.data?.teams.find((x) => x.id === taskData.team_id);
  const eventList = events.data?.events ?? [];

  const spec = eventList.find((e) => e.step === "analyst" && e.event_type === "started")?.message;
  const errors = eventList.filter((e) => e.event_type === "error");
  const iterations = buildIterations(eventList);

  return (
    <section className="pm-page pm-task-page">
      <TaskHeader
        task={taskData}
        projectName={project?.name}
        teamBudget={team?.token_budget ?? null}
        projectBudget={project?.token_budget ?? null}
      />

      {spec && <SpecPanel spec={spec} />}

      {errors.length > 0 && (
        <div className="pm-task-errors" role="alert">
          {errors.map((e) => (
            <p key={e.id}>
              <strong>{t.taskPage.noteLabel}</strong> {e.message ?? t.taskPage.errorNoMessage} (
              {fmtRelative(e.created_at)})
            </p>
          ))}
        </div>
      )}

      <section className="pm-section" aria-labelledby="pm-iter-h">
        <h2 id="pm-iter-h" className="section-label">
          {t.taskPage.iterH2}
        </h2>
        {events.isPending ? (
          <SkeletonRows n={3} />
        ) : events.error ? (
          <ErrorState
            title={t.taskPage.iterErrorTitle}
            error={events.error}
            onRetry={events.refetch}
          />
        ) : iterations.length === 0 ? (
          <p className="pm-task-list-none">
            {t.taskPage.noIterations}
            {taskData.status === "running" ? t.taskPage.noIterationsRunning : "."}
          </p>
        ) : (
          <ol className="pm-iter-list">
            {iterations.map((it, idx) => (
              <IterationCard
                key={it.n}
                taskId={taskId}
                it={it}
                isLatest={idx === 0}
                running={taskData.status === "running"}
              />
            ))}
          </ol>
        )}
      </section>
    </section>
  );
}
