import {useState} from "react";
import {useMutation,useQuery,useQueryClient} from "@tanstack/react-query";
import {Link} from "react-router-dom";
import {request,operateApi} from "@/lib/api";
import {useAccess} from "@/features/access/AccessGate";
import {JobConsole} from "./JobConsole";
import {useLanguage} from "@/lib/language";
import {t} from "@/lib/ui";

type Data={active_job?:string|null;counts:{stored_conversations:number;visible_conversations:number;hidden_generated_conversations:number;stored_messages:number;stored_project_records:number;operations_projects:number};database:{database:string};extraction_pending:number;pending_capped:boolean;checkpoints:number;timings:{stage:string;model:string;completed:number;mean_seconds:number;empty_results:number}[];bindings:{purpose:string;cli:string|null;model:string;provider_id:number|null}[]};
export function ProcessingWorkbench() {
  useLanguage();const {admin}=useAccess();const qc=useQueryClient();
  const [actionError,setActionError]=useState("");
  const [project,setProject]=useState("");const [limit,setLimit]=useState(25);const [workers,setWorkers]=useState(1);const [job,setJob]=useState<string|null>(null);
  const q=useQuery({queryKey:["data-visibility"],queryFn:()=>request<Data>("/operate/data"),enabled:admin,refetchInterval:15000});
  const projects=useQuery({queryKey:["processing-projects"],queryFn:()=>request<{projects:{project:string;display_name?:string}[]}>("/projects/all"),enabled:admin});
  const start=useMutation({mutationFn:()=>request<{job_id:string}>("/operate/process-recent",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({project:project||null,limit,workers})}),onSuccess:r=>setJob(r.job_id)});
  const stop=useMutation({mutationFn:(id:string)=>operateApi.stop(id)});
  if(!admin)return null;
  const data=q.data;
  const activeJob=job||data?.active_job;
  return <section className="processing-workbench" aria-label={t("Faster processing and data visibility")}>
    <h2>{t("Process what matters next")}</h2>
    <p>{t("Enrich recent or project-specific conversations. Successful source versions are checkpointed, including empty results. Older records without checkpoints receive one initial pass.")}</p>
    <div className="processing-controls">
      <label>{t("Project")}<select value={project} onChange={e=>setProject(e.target.value)}><option value="">{t("All projects, newest first")}</option>{projects.data?.projects.map(p=><option key={p.project} value={p.project}>{p.display_name||p.project}</option>)}</select></label>
      <label>{t("Conversations per stage")}<select value={limit} onChange={e=>setLimit(Number(e.target.value))}>{[10,25,50,100].map(n=><option key={n}>{n}</option>)}</select></label>
      <label>{t("API workers")}<select value={workers} onChange={e=>setWorkers(Number(e.target.value))}>{[1,2,4].map(n=><option key={n}>{n}</option>)}</select></label>
      <button className="button is-primary" disabled={start.isPending||!!activeJob} onClick={()=>start.mutate()}>{t("Process recent conversations")}</button>
      {activeJob&&<button className="button" disabled={stop.isPending} onClick={()=>stop.mutate(activeJob!)}>{t("Stop")}</button>}
      <button className="button" disabled={!!activeJob} onClick={()=>void operateApi.run("embed").then(r=>setJob(r.job_id)).catch(e=>setActionError(e.message))}>{t("Embeddings first")}</button>
    </div>
    <p>{t("CLI processing stays serial to avoid bridge contention. API concurrency is limited to four. No provider is switched automatically.")}</p>
    {(start.error||stop.error||q.error||actionError)&&<p role="alert">{start.error?.message||stop.error?.message||q.error?.message||actionError}</p>}
    {activeJob&&<JobConsole jobId={activeJob!} onFinished={()=>{setJob(null);void qc.invalidateQueries({queryKey:["data-visibility"]});void qc.invalidateQueries({queryKey:["operate"]});}}/>}
    {data&&<>
      <details><summary>{t("Where is my data?")}</summary>
        <p>{t("Database")}: <strong>{data.database.database}</strong></p>
        <dl className="data-counts">
          <dt>{t("Stored conversations")}</dt><dd>{data.counts.stored_conversations.toLocaleString()}</dd>
          <dt>{t("Visible in the library")}</dt><dd>{data.counts.visible_conversations.toLocaleString()}</dd>
          <dt>{t("Generated conversations hidden by default")}</dt><dd>{data.counts.hidden_generated_conversations.toLocaleString()}</dd>
          <dt>{t("Stored messages")}</dt><dd>{data.counts.stored_messages.toLocaleString()}</dd>
          <dt>{t("Historical project records")}</dt><dd>{data.counts.stored_project_records}</dd>
          <dt>{t("Operations projects (separate)")}</dt><dd>{data.counts.operations_projects}</dd>
          <dt>{t("Source versions awaiting extraction")}</dt><dd>{data.pending_capped?"500+":data.extraction_pending}</dd>
        </dl>
        <p>{t("A hidden conversation is still stored. Operations projects and historical project records are different datasets.")}</p>
        <p>{t("Backup restore verification is managed outside this view; database health does not prove a backup is restorable.")}</p>
        <Link to="/conversations">{t("Browse conversations")}</Link>
      </details>
      <details><summary>{t("Actual model routes and measured speed")}</summary>
        <ul>{data.bindings.map(b=><li key={b.purpose}>{b.purpose}: {b.cli?`${b.cli} CLI`:`API #${b.provider_id}`} · {b.model||t("CLI default")}</li>)}</ul>
        {!data.timings.length&&<p>{t("Timing estimates appear after checkpointed processing. No speedup is assumed.")}</p>}
        <ul>{data.timings.map(x=><li key={`${x.stage}-${x.model}`}>{x.stage} · {x.model}: {Number(x.mean_seconds).toFixed(1)}s / {t("conversation")} · {x.completed} {t("completed")}, {x.empty_results} {t("empty results")}</li>)}</ul>
        <p>{t("Elapsed time includes AI and database work. Source lengths vary; this is not a quality score.")}</p><Link to="/settings/ai">{t("Choose and test AI settings")}</Link>
      </details>
    </>}
  </section>;
}
