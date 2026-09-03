import { Ban, CheckCircle2, CircleAlert, Clock3, LoaderCircle, Play, Square } from "lucide-react";
import { Link } from "react-router-dom";

import type { PipelineStage, PipelineStageState } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { JobConsole, type JobCompletion } from "./JobConsole";

const STATE: Record<
  PipelineStageState,
  { label: string; Icon: typeof CheckCircle2 }
> = {
  healthy: { label: "Current", Icon: CheckCircle2 },
  due: { label: "Action needed", Icon: Clock3 },
  running: { label: "Running", Icon: LoaderCircle },
  failed: { label: "Failed", Icon: CircleAlert },
  blocked: { label: "Blocked", Icon: Ban },
};

function PipelineAction({
  stage,
  runningJobId,
  starting,
  onRun,
  onStop,
}: {
  stage: PipelineStage;
  runningJobId: string | null;
  starting: boolean;
  onRun: (name: string) => void;
  onStop: (id: string) => void;
}) {
  if (runningJobId) {
    return (
      <button
        type="button"
        className="button is-danger pipeline-action"
        onClick={() => onStop(runningJobId)}
        aria-label={`Stop ${stage.label.toLowerCase()}`}
      >
        <Square size={13} aria-hidden />
        Stop
      </button>
    );
  }

  if (stage.action_href) {
    const content = <>{stage.action_label ?? "Open"}</>;
    return stage.action_href.startsWith("#") ? (
      <a className="button pipeline-action" href={stage.action_href}>{content}</a>
    ) : (
      <Link className="button pipeline-action" to={stage.action_href}>{content}</Link>
    );
  }

  if (!stage.job_name || stage.state === "healthy") return null;
  const jobName = stage.job_name;
  return (
    <button
      type="button"
      className="button pipeline-action"
      onClick={() => onRun(jobName)}
      disabled={stage.state === "blocked" || starting}
    >
      <Play size={13} aria-hidden />
      {starting ? "Starting…" : stage.action_label}
    </button>
  );
}

export function Pipeline({
  stages,
  activeJob,
  startingJob,
  onRun,
  onStop,
  onFinished,
}: {
  stages: PipelineStage[];
  activeJob: { name: string; id: string } | null;
  startingJob: string | null;
  onRun: (name: string) => void;
  onStop: (id: string) => void;
  onFinished: (jobId: string, name: string, completion: JobCompletion) => void;
}) {
  const runningJob = (stage: PipelineStage) => {
    const activeMatches =
      activeJob &&
      (activeJob.name === stage.job_name ||
        (stage.key === "ingest" &&
          (activeJob.name === "ingest" || activeJob.name.startsWith("ingest_"))));
    if (activeMatches) return activeJob;
    return stage.job_id && stage.job_name
      ? { id: stage.job_id, name: stage.job_name }
      : null;
  };

  return (
    <section className="pipeline-section" aria-labelledby="pipeline-heading">
      <div className="pipeline-heading-row">
        <div>
          <h2 id="pipeline-heading" className="section-label">Knowledge pipeline</h2>
          <p>Move stored sessions from discovery to trusted, reusable knowledge.</p>
        </div>
      </div>
      {stages.length === 0 ? (
        <div className="disclosure">
          <CircleAlert size={15} aria-hidden />
          <div>Pipeline state is not available from this server.</div>
        </div>
      ) : (
        <ol className="pipeline" aria-label="Knowledge pipeline">
          {stages.map((stage, index) => {
            const activeStageJob = runningJob(stage);
            const renderedState = activeStageJob ? "running" : stage.state;
            const { label: stateLabel, Icon } = STATE[renderedState];
            return (
              <li key={stage.key} className={`pipeline-stage state-${renderedState}`}>
                <div className="pipeline-stage-top">
                  <span className="pipeline-step" aria-hidden>{index + 1}</span>
                  <span className="pipeline-state">
                    <Icon
                      size={14}
                      aria-hidden
                      className={renderedState === "running" ? "spin" : undefined}
                    />
                    {stateLabel}
                  </span>
                </div>
                <h3>{stage.label}</h3>
                <p className="pipeline-detail">
                  {renderedState === "running" ? `${stage.label} is running now.` : stage.detail}
                </p>
                {stage.blocked_reason && renderedState !== "running" && (
                  <p className="pipeline-reason">{stage.blocked_reason}</p>
                )}
                {stage.last_success && (
                  <p className="pipeline-last">
                    Last success <time dateTime={stage.last_success}>{formatDateTime(stage.last_success)}</time>
                  </p>
                )}
                <PipelineAction
                  stage={stage}
                  runningJobId={activeStageJob?.id ?? null}
                  starting={startingJob === stage.job_name}
                  onRun={onRun}
                  onStop={onStop}
                />
              </li>
            );
          })}
        </ol>
      )}
      {stages.map((stage) => {
        const job = runningJob(stage);
        if (!job) return null;
        return (
          <div className="pipeline-console" key={`${stage.key}-${job.id}`}>
            <h3>{stage.label} output</h3>
            <JobConsole
              jobId={job.id}
              onFinished={(completion) => onFinished(job.id, job.name, completion)}
            />
          </div>
        );
      })}
    </section>
  );
}
