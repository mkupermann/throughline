import {useEffect,useState} from "react";
import {useQuery,useMutation,useQueryClient} from "@tanstack/react-query";
import {Link} from "react-router-dom";
import {operateApi,request} from "@/lib/api";
import {t} from "@/lib/ui";
import {useLanguage} from "@/lib/language";

interface Progress {
  steps:{name:string;title:string}[];
  job:null|{id:string;running:boolean;returncode:number|null;lines:string[];stages:Record<string,{state:string;detail:string}>};
}
export function ProcessAll() {
  useLanguage();
  const qc=useQueryClient();
  const [opened,setOpened]=useState(false);
  const q=useQuery({queryKey:["process-all"],queryFn:()=>request<Progress>("/operate/all"),refetchInterval:3000});
  const start=useMutation({mutationFn:()=>operateApi.run("process-all"),onSuccess:()=>{setOpened(true);void qc.invalidateQueries({queryKey:["process-all"]});}});
  const stop=useMutation({mutationFn:(id:string)=>operateApi.stop(id),onSuccess:()=>void qc.invalidateQueries({queryKey:["process-all"]})});
  const job=q.data?.job;
  useEffect(()=>{
    if(job && (!job.running || job.stages?.["project-names"]?.state === "finished")) {
      for(const key of ["project-library","story","conversations","operate","overview"]) void qc.invalidateQueries({queryKey:[key]});
    }
  },[job?.id,job?.running,job?.stages?.["project-names"]?.state,qc]);
  return <section className="process-all" aria-label={t("Complete processing")}>
    <div className="process-all-head">
      <button className="button" disabled={start.isPending||q.isPending||!!q.error||job?.running} onClick={()=>start.mutate()}>{t(job?.running?"Processing everything…":"Process everything")}</button>
      {job?.running&&<button className="button" disabled={stop.isPending} onClick={()=>stop.mutate(job.id)}>{t("Stop")}</button>}
      <Link to="/settings/ai">{t("AI settings")}</Link>
      <button className="linkbutton" onClick={()=>setOpened(!opened)} aria-expanded={opened}>{t("Steps and progress")}</button>
    </div>
    <p>{t("All configured sources and all pending items, in sequence. Uses the AI selected for each purpose.")}</p>
    {(start.error||stop.error||q.error)&&<p role="alert">{(start.error||stop.error||q.error)?.message}</p>}
    {job&&!job.running&&<p role="status">{t(job.returncode===0?"Processing pass finished. Review remaining pending items and findings.":"Processing was stopped or incomplete. See failed or blocked steps.")}</p>}
    {opened&&<div>
      <p>{t("Import → skills and prompts → project names → titles → knowledge → entities → reflection → embeddings → audit → diagnostics. Exports need a destination and remain separate.")}</p>
      <p>{t("Reflection produces review suggestions; it does not automatically merge or confirm knowledge.")}</p>
      <p>{t("You can close this page while processing continues. A server restart or the 24-hour time limit stops the run; completed database work is retained.")}</p>
      <ol>{q.data?.steps.map(step=><li key={step.name}>{t(step.title)} — {t(job?.stages?.[step.name]?.state??"pending")}{job?.stages?.[step.name]?.detail&&<span>: {job.stages[step.name].detail}</span>}</li>)}</ol>
      {job&&<details><summary>{t("Technical output")}</summary><pre className="console-out">{job.lines.filter(line=>!line.startsWith("::stage ")).join("\n")}</pre></details>}
    </div>}
  </section>;
}
