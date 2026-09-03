import { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Download, Info, OctagonAlert, Play, Square, Terminal } from "lucide-react";

import { operateApi, type JobSummary, type OperateStatus, type ProviderCoverage } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { useToast } from "@/components/Toaster";
import { ExportPanel } from "./ExportPanel";
import { JobConsole, type JobCompletion } from "./JobConsole";
import { Pipeline } from "./Pipeline";

/** Text label per status — colour alone never carries the meaning. */
const STATUS_LABEL: Record<ProviderCoverage["status"], string> = {
  ok: "OK",
  pending: "Pending",
  not_ingested: "Not ingested",
  no_data: "No data",
  unknown: "Unknown",
};

/** When this tool last had a row written or refreshed — not when a job last ran.
 *
 * The distinction matters: a run that finds nothing changed does not move this,
 * and a long-lived session keeps the start time it opened with. The column was
 * headed "Last run" over a value that was neither.
 */
function fmtLastImport(v: string | null): string {
  return v ? v.slice(0, 16).replace("T", " ") : "—";
}

/**
 * The file name a person recognizes, not the raw stored path.
 *
 * `ingestion_log.file_path` is whatever the adapter recorded, which on
 * Windows is backslash-separated and on the demo fixture arrives with every
 * separator already flattened to a dash — splitting on `/` alone left either
 * one unshortened. Only the name is the useful part here; the full value is
 * still reachable as a tooltip for anyone who needs the whole path.
 */
function fmtIngestPath(raw: string): { label: string; full: string } {
  const full = raw;
  const parts = raw.split(/[\\/]+/).filter(Boolean);
  const label = parts.length ? parts[parts.length - 1] : raw;
  return { label, full };
}

/**
 * Coverage per source: what's on disk against what's imported (spec §4.3).
 *
 * 8,453 messages once sat on disk, fully parseable, one command away, and
 * nothing in the product said so. This table is the fix for Operate; the
 * Overview attention item is the fix for the headline surface.
 *
 * The Ingest button only appears where a matching `ingest_<name>` job is
 * registered — the "(unattributed)" pseudo-row coverage() adds has no such
 * job, and offering a control guaranteed to 404 is the same mistake
 * `check_requirement`'s docstring warns against for the regular job list.
 */
function ProvidersTable({
  providers,
  jobs,
  onIngest,
}: {
  providers: ProviderCoverage[];
  jobs: JobSummary[];
  onIngest: (name: string) => void;
}) {
  const jobByName = new Map(jobs.map((j) => [j.name, j]));
  return (
    <div className="table-wrap scroll-x">
      <table className="sqltable providers-table">
        <caption className="sr-only">
          Coverage per source: files on disk, pending, excluded and imported, with an ingest
          action for each.
        </caption>
        <thead>
          <tr>
            <th scope="col">Provider</th>
            <th scope="col">On disk</th>
            <th scope="col">Pending</th>
            <th scope="col" title="Discovered but not ingested (subagent transcripts).">
              Excluded
            </th>
            <th scope="col">Ingested</th>
            <th scope="col">Last import</th>
            <th scope="col">Status</th>
            <th scope="col">
              <span className="sr-only">Action</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {providers.map((p) => {
            const job = jobByName.get(`ingest_${p.name}`);
            return (
              <tr key={p.name}>
                <th scope="row">{p.label}</th>
                <td className="tabular">{formatCount(p.on_disk)}</td>
                <td className="tabular">{formatCount(p.pending)}</td>
                <td
                  className="tabular"
                  title="Discovered but not ingested (subagent transcripts)."
                >
                  {formatCount(p.excluded)}
                </td>
                <td className="tabular">{formatCount(p.ingested)}</td>
                <td className="tabular">{fmtLastImport(p.last_run)}</td>
                <td>
                  <span className={`status-pill status-${p.status}`}>
                    {STATUS_LABEL[p.status] ?? p.status}
                  </span>
                </td>
                <td>
                  {job ? (
                    <button
                      type="button"
                      className="button is-small"
                      onClick={() => onIngest(p.name)}
                      disabled={job.running}
                    >
                      <Play size={12} aria-hidden />
                      {job.running ? "Running…" : "Ingest"}
                    </button>
                  ) : (
                    <span aria-hidden>—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function JobCard({
  job,
  activeJobId,
  onRun,
  onStop,
  onFinished,
}: {
  job: JobSummary;
  activeJobId: string | null;
  onRun: () => void;
  onStop: (id: string) => void;
  onFinished: (jobId: string, completion: JobCompletion) => void;
}) {
  // `activeJobId` is already resolved by the parent from either the local
  // "I just started this" state or the server's view. Re-checking job.running
  // here required the status query to have refetched first, so the console
  // never appeared for a job you had only just launched.
  return (
    <div className="job">
      <div className="job-head">
        <div className="job-text">
          <h3 className="job-title">{job.title}</h3>
          <p className="job-desc">{job.description}</p>
        </div>
        {activeJobId ? (
          <button type="button" className="button is-danger" onClick={() => onStop(activeJobId)}>
            <Square size={13} aria-hidden />
            Stop
          </button>
        ) : (
          <button
            type="button"
            className="button"
            onClick={onRun}
            disabled={Boolean(job.unavailable)}
            title={job.unavailable ?? undefined}
          >
            <Play size={13} aria-hidden />
            Run
          </button>
        )}
      </div>
      {job.unavailable && (
        <p className="job-unavailable">
          <Info size={13} aria-hidden />
          <span>{job.unavailable}</span>
        </p>
      )}
      {activeJobId && (
        <JobConsole
          jobId={activeJobId}
          onFinished={(completion) => onFinished(activeJobId, completion)}
        />
      )}
    </div>
  );
}

export function OperatePage() {
  const qc = useQueryClient();
  const toast = useToast();
  const [activeJob, setActiveJob] = useState<{ name: string; id: string } | null>(null);
  const [jobAnnouncement, setJobAnnouncement] = useState("");

  const { data, isPending, error } = useQuery({
    queryKey: ["operate", "status"],
    queryFn: operateApi.status,
    // While a job runs the counts move underneath us; refresh gently.
    refetchInterval: (query) =>
      activeJob || (query.state.data as OperateStatus | undefined)?.jobs?.some((job) => job.running)
        ? 4000
        : false,
  });

  const runJob = useMutation({
    mutationFn: (name: string) => operateApi.run(name),
    onSuccess: (res) => {
      setActiveJob({ name: res.name, id: res.job_id });
      setJobAnnouncement(`${res.name} started.`);
    },
    onError: (e) => toast.push({ message: (e as Error).message, tone: "error", duration: 8000 }),
  });

  const stopJob = useMutation({
    mutationFn: (id: string) => operateApi.stop(id),
    onSuccess: () => {
      setJobAnnouncement("Stop requested.");
      toast.push({ message: "Stop requested." });
    },
    onError: (e) => toast.push({ message: (e as Error).message, tone: "error", duration: 8000 }),
  });

  const finishJob = useCallback(
    (jobId: string, name: string, completion: JobCompletion) => {
      setActiveJob((current) => (current?.id === jobId ? null : current));
      void qc.invalidateQueries({ queryKey: ["operate"] });
      void qc.invalidateQueries({ queryKey: ["curate"] });
      void qc.invalidateQueries({ queryKey: ["overview"] });
      // The job just cleared the server-side scan cache (jobs.py _pump), but
      // the other surfaces can still hold their pre-ingest coverage response.
      void qc.invalidateQueries({ queryKey: ["providers"] });
      if (completion.ok) {
        setJobAnnouncement(`${name} completed. Pipeline status refreshed.`);
      } else {
        setJobAnnouncement(`${name} failed. Pipeline status refreshed.`);
        toast.push({
          message: `${name} failed. It may have completed part of the work. Check the refreshed pipeline state before retrying.`,
          tone: "error",
          duration: 8000,
        });
      }
    },
    [qc, toast],
  );

  if (error) {
    return (
      <>
        <header className="page-header">
          <h1 className="page-title">Operate</h1>
        </header>
        <div className="empty-state">
          <OctagonAlert size={22} aria-hidden />
          <h2>Cannot load pipeline state</h2>
          <p>{(error as Error).message}</p>
        </div>
      </>
    );
  }

  // `isPending` alone does not narrow `data` — a settled-but-failed query
  // leaves it undefined, so both are checked.
  if (isPending || !data) {
    return (
      <>
        <header className="page-header">
          <h1 className="page-title">Operate</h1>
        </header>
        <div className="skeleton skeleton-row" />
      </>
    );
  }

  const cov = data.embedding.coverage;
  const covPct = cov.total ? Math.round((100 * cov.embedded) / cov.total) : 100;
  const pipelineStages = data.pipeline ?? [];
  const hasPipeline = pipelineStages.length > 0;
  const pipelineJobNames = new Set(
    pipelineStages.flatMap((stage) => (stage.job_name ? [stage.job_name] : [])),
  );
  const hasIngestStage = pipelineStages.some((stage) => stage.key === "ingest");
  const advancedJobs = hasPipeline
    ? data.jobs.filter(
        (job) =>
          !pipelineJobNames.has(job.name) &&
          !(hasIngestStage && (job.name === "ingest" || job.name.startsWith("ingest_"))),
      )
    : data.jobs;

  return (
    <>
      <header className="page-header">
        <h1 className="page-title">Operate</h1>
        <p className="page-subtitle">Pipeline state, and the jobs that change it.</p>
      </header>
      <p className="sr-only" role="status" aria-live="polite">
        {jobAnnouncement}
      </p>

      {!data.extensions.pgvector_usable && (
        <div className="verdict verdict-broken">
          <OctagonAlert size={18} aria-hidden />
          <span>{data.extensions.note}</span>
        </div>
      )}

      <Pipeline
        stages={pipelineStages}
        activeJob={activeJob}
        startingJob={runJob.isPending ? runJob.variables : null}
        onRun={(name) => runJob.mutate(name)}
        onStop={(id) => stopJob.mutate(id)}
        onFinished={finishJob}
      />

      <section className="stack-top">
        <h2 className="section-label">Environment</h2>
        <dl className="totals">
          <div className="total">
            <dt>database</dt>
            <dd>{String(data.database.dbname ?? "—")}</dd>
          </div>
          <div className="total">
            <dt>host</dt>
            <dd className="tabular">
              {String(data.database.host ?? "—")}:{String(data.database.port ?? "")}
            </dd>
          </div>
          <div className="total">
            <dt>pgvector</dt>
            <dd className={data.extensions.pgvector_usable ? "ok" : "bad"}>
              {data.extensions.pgvector_usable ? "usable" : "broken"}
            </dd>
          </div>
          <div className="total">
            <dt>embedding backend</dt>
            <dd>{data.embedding.backend}</dd>
          </div>
          <div className="total">
            <dt>embedding coverage</dt>
            <dd className="tabular">{covPct}%</dd>
          </div>
          {/* Which model generates decides whether transcripts leave the
              machine. The page showed the embedding model and not this one,
              so the fact was only reachable from `throughline doctor`. */}
          <div className="total">
            <dt>generation backend</dt>
            {data.generation?.available ? (
              <dd>
                {data.generation.backend}/{data.generation.model}
                <span className={data.generation.local ? "ok" : "bad"} style={{ marginLeft: "var(--space-2)" }}>
                  {data.generation.local ? "runs locally" : "leaves this machine"}
                </span>
              </dd>
            ) : (
              <dd className="bad">no model available</dd>
            )}
          </div>
        </dl>
        {data.generation && !data.generation.available && data.generation.detail && (
          <div className="disclosure" style={{ marginTop: "var(--space-2)" }}>
            <OctagonAlert size={15} aria-hidden />
            <div>{data.generation.detail}</div>
          </div>
        )}
        {!data.embedding.available && data.embedding.reason && (
          <div className="disclosure" style={{ marginTop: "var(--space-2)" }}>
            <OctagonAlert size={15} aria-hidden />
            <div>{data.embedding.reason}</div>
          </div>
        )}
      </section>

      <section className="stack-top">
        <h2 className="section-label">Inventory</h2>
        <dl className="totals totals--metric">
          {Object.entries(data.counts).map(([k, v]) => (
            <div key={k} className="total">
              <dt>{k}</dt>
              <dd className="tabular">{formatCount(v)}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section id="provider-coverage" className="stack-top" aria-labelledby="coverage-h">
        <h2 id="coverage-h" className="section-label">
          Provider coverage
        </h2>
        {data.providers ? (
          <ProvidersTable
            providers={data.providers}
            jobs={data.jobs}
            onIngest={(name) => runJob.mutate(`ingest_${name}`)}
          />
        ) : (
          <div className="empty-state">
            <OctagonAlert size={22} aria-hidden />
            <h3>Provider coverage unavailable</h3>
            <p>This server did not return source coverage. Refresh after the backend is updated.</p>
          </div>
        )}
      </section>

      {/* Export gets its own section, ahead of the jobs. It is not a
          one-click job — it takes a destination — and at the end of fourteen
          Run buttons it was there and nobody found it. */}
      <section className="stack-top">
        <h2 className="section-label">
          <Download size={13} aria-hidden style={{ verticalAlign: "-2px" }} /> Export and portability
        </h2>
        <div className="jobs">
          <ExportPanel />
        </div>
      </section>

      {advancedJobs.length > 0 && (
        <section className="stack-top" aria-labelledby="maintenance-heading">
          <h2 id="maintenance-heading" className="section-label">
            {hasPipeline ? "Maintenance" : "Jobs"}
          </h2>
          <details className="advanced-operations" open={!hasPipeline ? true : undefined}>
            <summary>
              <span className="advanced-operations-title">
                <ChevronRight className="advanced-operations-chevron" size={14} aria-hidden />
                <Terminal size={14} aria-hidden />
                {hasPipeline ? "Advanced maintenance" : "Available jobs"}
              </span>
              <span className="advanced-operations-count">
                {advancedJobs.length} action{advancedJobs.length === 1 ? "" : "s"}
              </span>
            </summary>
            <div className="advanced-operations-body">
              <p>Low-frequency repair, metadata and diagnostic jobs.</p>
              <div className="jobs">
                {advancedJobs.map((job) => (
                  <JobCard
                    key={job.name}
                    job={job}
                    activeJobId={
                      activeJob?.name === job.name ? activeJob.id : job.running ? job.job_id : null
                    }
                    onRun={() => runJob.mutate(job.name)}
                    onStop={(id) => stopJob.mutate(id)}
                    onFinished={(jobId, completion) => finishJob(jobId, job.name, completion)}
                  />
                ))}
              </div>
            </div>
          </details>
        </section>
      )}

      {data.ingestion.length > 0 && (
        <section className="stack-top">
          <h2 className="section-label">Recent ingestion</h2>
          <ul className="results">
            {data.ingestion.slice(0, 10).map((r, i) => {
              const { label, full } = fmtIngestPath(String(r.file_path ?? ""));
              return (
                <li key={i} className="result">
                  <div className="result-link">
                    <div className="result-meta">
                      <span title={full}>{label}</span>
                      <span className="tabular">{String(r.record_count ?? "")} records</span>
                      <span>{String(r.ingested_at ?? "").slice(0, 16).replace("T", " ")}</span>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </>
  );
}
