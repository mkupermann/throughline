import {useState} from "react";
import {useQuery} from "@tanstack/react-query";
import {Link} from "react-router-dom";
import {request} from "@/lib/api";
import {t} from "@/lib/ui";
import {useLanguage} from "@/lib/language";

type Brief = {project:string; markdown:string; truncated:boolean; empty:boolean; sources:{kind:string;id:number;href:string}[]};
export function ContinueProject({project}:{project:string}) {
  useLanguage();
  const [opened,setOpened]=useState(false);
  const q=useQuery({queryKey:["continuation",project],queryFn:()=>request<Brief>(`/projects/${encodeURIComponent(project)}/continue`),enabled:opened});
  function download() {
    if(!q.data)return;
    const markdown=q.data.markdown.replace(/\]\((\/(?:c|m)\/\d+)\)/g, (_,path:string)=>`](${window.location.origin}${path})`);
    const url=URL.createObjectURL(new Blob([markdown],{type:"text/markdown;charset=utf-8"}));
    const a=document.createElement("a");a.href=url;a.download="throughline-continuation.md";a.click();URL.revokeObjectURL(url);
  }
  return <section className="continuation-panel" aria-label={t("Continue this project")}>
    <button className="button is-primary" aria-expanded={opened} onClick={()=>setOpened(!opened)}>{t("Continue this project")}</button>
    {opened && <div>
      <p>{t("A source-linked brief for your next agent. Built locally; no AI call.")}</p>
      {q.isPending && <p role="status">{t("Loading…")}</p>}
      {q.error && <p role="alert">{q.error.message}</p>}
      {q.data && <>
        {q.data.empty ? <p>{t("No source evidence yet. Import conversations for this project first.")}</p> : <>
          <button className="button" onClick={download}>{t("Download continuation brief")}</button>
          <p>{t("Historical excerpts need verification before action. Repository state is not inspected.")}</p>
          {q.data.truncated && <p>{t("Bounded preview: follow the sources for the full history.")}</p>}
          <textarea className="continuation-text" aria-label={t("Continuation brief")} readOnly value={q.data.markdown} rows={14}/>
          <details><summary>{t("Source evidence")}</summary><ul>{q.data.sources.map(s=><li key={`${s.kind}-${s.id}`}><Link to={s.href}>{s.kind} #{s.id}</Link></li>)}</ul></details>
        </>}
      </>}
    </div>}
  </section>;
}
