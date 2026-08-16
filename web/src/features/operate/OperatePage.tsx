import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Info, OctagonAlert, Play, Square, Terminal } from "lucide-react";

import { ApiError, operateApi, providersApi, type JobSummary, type ProviderCoverage } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { useToast } from "@/components/Toaster";

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

/**
 * Live job output.
 *
 * EventSource rather than polling: a job emits output in bursts and long
 * quiet stretches, and polling either lags the bursts or hammers the server
 * through the quiet. The stream replays the retained buffer on connect, so
 * opening this panel mid-run shows the whole run so far.
 */
function JobConsole({ jobId, onFinished }: { jobId: string; onFinished: () => void }) {
  const [lines, setLines] = useState<string[]>([]);
  const [done, setDone] = useState<string | null>(null);
  const boxRef = useRef<HTMLPreElement>(null);
  const pinned = useRef(true);

  useEffect(() => {
    setLines([]);
    setDone(null);
    const es = new EventSource(`/api/operate/job/${jobId}/stream`);
    es.addEventListener("line", (e) => setLines((l) => [...l, (e as MessageEvent).data]));
    es.addEventListener("done", (e) => {
      setDone((e as MessageEvent).data);
      es.close();
      onFinished();
    });
    es.onerror = () => es.close();
    return () => es.close();
  }, [jobId, onFinished]);

  // Follow the tail, but stop fighting the user the moment they scroll up.
  useEffect(() => {
    const el = boxRef.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <div className="console">
      <pre
        ref={boxRef}
        className="console-out"
        tabIndex={0}
        aria-label="Job output"
        onScroll={(e) => {
          const el = e.currentTarget;
          pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
      >
        {lines.join("\n")}
        {done && `\n\n— ${done}`}
      </pre>
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
  onFinished: () => void;
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
        {job.running && job.job_id ? (
          <button type="button" className="button is-danger" onClick={() => onStop(job.job_id!)}>
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
      {activeJobId && <JobConsole jobId={activeJobId} onFinished={onFinished} />}
    </div>
  );
}

export function OperatePage() {
  const qc = useQueryClient();
  const toast = useToast();
  const [activeJob, setActiveJob] = useState<{ name: string; id: string } | null>(null);

  const { data, isPending, error } = useQuery({
    queryKey: ["operate", "status"],
    queryFn: operateApi.status,
    // While a job runs the counts move underneath us; refresh gently.
    refetchInterval: activeJob ? 4000 : false,
  });

  // Same queryKey as ProviderBar — one shared cache entry, not a second
  // request for the same data.
  const { data: providersData, error: providersError } = useQuery({
    queryKey: ["providers"],
    queryFn: () => providersApi.list(),
    staleTime: 60_000,
  });

  const runJob = useMutation({
    mutationFn: (name: string) => operateApi.run(name),
    onSuccess: (res) => setActiveJob({ name: res.name, id: res.job_id }),
    onError: (e) => toast.push({ message: (e as Error).message, tone: "error", duration: 8000 }),
  });

  const stopJob = useMutation({
    mutationFn: (id: string) => operateApi.stop(id),
    onSuccess: () => toast.push({ message: "Stop requested." }),
  });

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

  return (
    <>
      <header className="page-header">
        <h1 className="page-title">Operate</h1>
        <p className="page-subtitle">Pipeline state, and the jobs that change it.</p>
      </header>

      {!data.extensions.pgvector_usable && (
        <div className="verdict verdict-broken">
          <OctagonAlert size={18} aria-hidden />
          <span>{data.extensions.note}</span>
        </div>
      )}

      <section>
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
        </dl>
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

      <section className="stack-top" aria-labelledby="coverage-h">
        <h2 id="coverage-h" className="section-label">
          Provider coverage
        </h2>
        {providersData ? (
          <ProvidersTable
            providers={providersData.providers}
            jobs={data.jobs}
            onIngest={(name) => runJob.mutate(`ingest_${name}`)}
          />
        ) : providersError ? (
          <div className="empty-state">
            <OctagonAlert size={22} aria-hidden />
            <h3>Provider coverage unavailable</h3>
            <p>{(providersError as ApiError).message}</p>
            {(providersError as ApiError).hint && (
              <p className="empty-hint">{(providersError as ApiError).hint}</p>
            )}
          </div>
        ) : (
          <div className="skeleton skeleton-row" />
        )}
      </section>

      <section className="stack-top">
        <h2 className="section-label">
          <Terminal size={13} aria-hidden style={{ verticalAlign: "-2px" }} /> Jobs
        </h2>
        <div className="jobs">
          {data.jobs.map((job) => (
            <JobCard
              key={job.name}
              job={job}
              activeJobId={activeJob?.name === job.name ? activeJob.id : job.running ? job.job_id : null}
              onRun={() => runJob.mutate(job.name)}
              onStop={(id) => stopJob.mutate(id)}
              onFinished={() => {
                qc.invalidateQueries({ queryKey: ["operate"] });
                qc.invalidateQueries({ queryKey: ["curate"] });
                qc.invalidateQueries({ queryKey: ["overview"] });
                // The job just cleared the server-side scan cache (jobs.py
                // _pump), but the client still holds the pre-ingest coverage
                // response until this query is told to refetch too.
                qc.invalidateQueries({ queryKey: ["providers"] });
              }}
            />
          ))}
        </div>
      </section>

      {data.ingestion.length > 0 && (
        <section className="stack-top">
          <h2 className="section-label">Recent ingestion</h2>
          <ul className="results">
            {data.ingestion.slice(0, 10).map((r, i) => (
              <li key={i} className="result">
                <div className="result-link">
                  <div className="result-meta">
                    <span>{String(r.file_path ?? "").split("/").slice(-2).join("/")}</span>
                    <span className="tabular">{String(r.record_count ?? "")} records</span>
                    <span>{String(r.ingested_at ?? "").slice(0, 16).replace("T", " ")}</span>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
