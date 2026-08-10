import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Info, OctagonAlert, Play, Square, Terminal } from "lucide-react";

import { operateApi, type JobSummary } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { useToast } from "@/components/Toaster";

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
        <dl className="totals">
          {Object.entries(data.counts).map(([k, v]) => (
            <div key={k} className="total">
              <dt>{k}</dt>
              <dd className="tabular">{formatCount(v)}</dd>
            </div>
          ))}
        </dl>
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
