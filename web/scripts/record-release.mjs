#!/usr/bin/env node
/** Record actual UI interactions against scripts/seed_demo_data.py only.
 * No response stubs, live imports, model calls, task launches or SQL writes.
 * Requires ffmpeg and Playwright Chromium. See docs/DEMO.md.
 */
import { chromium } from 'playwright';
import { mkdir, writeFile, unlink, readFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const out = path.join(root, 'docs/videos');
const base = process.env.THROUGHLINE_DEMO_URL || 'http://127.0.0.1:8794';
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) throw Error('Use a loopback demo server.');
const frontendSha256=createHash('sha256').update(await fetch(base).then(r=>r.text())).digest('hex');
const teamDemo=process.env.THROUGHLINE_DEMO_TEAM==='1';
let demoCookie='';
if(teamDemo){
 const login=await fetch(`${base}/api/auth/login`,{method:'POST',headers:{'Content-Type':'application/json',Origin:new URL(base).origin,'X-Throughline-Request':'1'},body:JSON.stringify({username:'admin',password:'fictional walkthrough password'})});
 if(!login.ok)throw Error('Fictional team demo login failed.');
 demoCookie=login.headers.get('set-cookie').split(';')[0];
}
const demoFetch=url=>fetch(url,{headers:demoCookie?{Cookie:demoCookie}:{}});
const history = await demoFetch(`${base}/api/story/Atlas%20(demo)/history`).then(r=>r.json());
if (history.total !== 4 || !history.sessions.some(s=>s.title?.startsWith('Counterexample:')) || !history.paths.every(p=>p.path==='/fictional/Atlas (demo)')) throw Error('The server is not the fictional project-story fixture.');
const total = await demoFetch(`${base}/api/overview`).then(r=>r.json());
if (total.totals?.conversations !== 44 || total.totals?.messages !== 249) throw Error('Unexpected corpus: refuse to record.');
await mkdir(out,{recursive:true});
const browser = await chromium.launch({headless:true});
const scenarios = [
 ['projects','/', 'Open a project and recover its current state.', async p=>{
   await p.getByRole('textbox',{name:'Find a project'}).fill('Atlas');
   await p.getByRole('link',{name:/Atlas \(demo\)/}).click();
   const state=p.locator('.story-position > .story-state-grid');
   await state.waitFor();
   await state.evaluate(el=>el.scrollIntoView({block:'center'}));
 }, 'Goal, current position, blocker and next step each link to a source.'],
 ['conversations','/conversations','Choose a project, then read one conversation at a time.',async p=>{
   await p.getByRole('combobox',{name:'Project',exact:true}).selectOption('Atlas (demo)');
   await p.getByRole('button',{name:/Counterexample: long documents/}).click();
   const output=p.locator('.output-disclosure').first();
   await output.locator('summary').click();
   await p.waitForTimeout(700);
   await output.locator('summary').click();
   await p.getByText('evaluation.md',{exact:false}).last().scrollIntoViewIfNeeded();
 },'Output starts collapsed. Expand it when needed; file references and exact times remain visible.'],
 ['find','/find?q=boundary','Search across imported records.',async p=>{
   await p.evaluate(()=>window.scrollBy(0,400));
 },'Results retain source links. Search does not require a generation model.'],
 ['timeline','/timeline','Compare conversation activity across tools and dates.',async p=>{
   const cells=p.getByRole('button',{name:/[1-9][0-9]* events/});
   if (await cells.count()) await cells.first().click();
   const group=p.locator('.timeline-project details').first();
   await group.waitFor();
   await group.locator('summary').click();
   await group.scrollIntoViewIfNeeded();
 },'Conversations expand within their recorded project context. Messages and memories stay in the conversation.'],
 ['review','/curate','Inspect knowledge that may need correction.',async p=>{
   await p.evaluate(()=>window.scrollBy(0,430));
 },'The fixture includes contradictory and superseded statements. A suggestion is not a verified fact.'],
 ['operate','/operate','Understand the import and processing stages.',async p=>{
   await p.evaluate(()=>window.scrollBy(0,350));
 },'Discovery, import, extraction and embeddings have separate states. No processing is started in this clip.'],
 ['processing','/timeline','One button starts a complete pass through all pending work.',async p=>{
   await p.getByRole('button',{name:'Steps and progress',exact:true}).click();
   const panel=p.locator('.process-all');
   await panel.locator('ol').scrollIntoViewIfNeeded();
 },'Eleven steps, visible failures and a Stop control while running. This clip does not start processing.'],
 ['ai-settings','/settings/ai','Choose a provider and model independently for each purpose.',async p=>{
   const answer=p.locator('.ai-purpose').first();
   await answer.getByRole('combobox',{name:'Provider or installed CLI'}).selectOption({label:'Hosted API (offline demo) · openai'});
   await answer.getByRole('combobox',{name:'Model',exact:true}).fill('example-hosted-model');
   await p.waitForTimeout(1600);
   const cli=answer.getByRole('combobox',{name:'Provider or installed CLI'}).locator('option[value="cli:codex"]');
   if(!await cli.isDisabled()) {
     await answer.getByRole('combobox',{name:'Provider or installed CLI'}).selectOption('cli:codex');
     await p.waitForTimeout(1600);
   }
   await p.locator('.ai-purpose').last().scrollIntoViewIfNeeded();
 },'Embeddings need a vector API, not a chat CLI. Form edits are unsaved; no model or connection test runs.'],
 ['console','/console' ,'Inspect the fictional corpus with read-only SQL.',async p=>{
   await p.getByRole('textbox',{name:'SQL query'}).fill('SELECT source_tool, count(*) AS conversations FROM conversations GROUP BY source_tool ORDER BY source_tool');
   await p.getByRole('button',{name:'Run',exact:true}).click();
 },'The query counts conversations by source tool. PostgreSQL enforces read-only execution.'],
 ['teams','/pm','Connect project history with optional AI-team operations.',async p=>{
   await p.getByRole('link',{name:/Acme Storefront Relaunch/}).first().click();
   await p.getByRole('heading',{name:'Tasks',exact:true}).scrollIntoViewIfNeeded();
   await p.evaluate(()=>window.scrollBy(0,350));
 },'Fictional tasks show passed, budget-exhausted and stopped states. No agent is launched.'],
 ['roles','/pm/roles','Define the responsibility and tool for a role.',async p=>{await p.evaluate(()=>window.scrollBy(0,300));},'The demo separates analysis, execution, testing and review.'],
 ['members','/pm/members','See the people and agents available to teams.',async p=>{await p.evaluate(()=>window.scrollBy(0,250));},'These identities are invented fixtures, not real team members.'],
 ['pipelines','/pm/teams','Inspect the roles and sequence in a team pipeline.',async p=>{await p.evaluate(()=>window.scrollBy(0,300));},'Team configuration is reusable across linked projects.'],
 ['models','/settings/providers','Inspect provider configuration separately from project history.',async p=>{await p.evaluate(()=>window.scrollBy(0,250));},'Provider entries are fictional. This recording performs no hosted generation.'],
 ['workspace-access','/settings/access','Personal accounts control access to one shared workspace.',async p=>{
   await p.getByRole('textbox',{name:'Username',exact:true}).fill('admin');
   await p.getByLabel('Password',{exact:true}).fill('fictional walkthrough password');
   await p.getByRole('button',{name:'Sign in',exact:true}).click();
   await p.getByRole('heading',{name:'Accounts',exact:true}).waitFor();
   await p.waitForTimeout(2000);
   await p.getByRole('heading',{name:'Change history',exact:true}).evaluate(el=>el.scrollIntoView({block:'start'}));
   await p.evaluate(()=>window.scrollBy(0,-60));
 },'Viewer, editor and administrator roles; changes retain actor and time. All members share the corpus.'],
 ['project-assignment','/','Create a named project and correct a conversation’s membership.',async p=>{
   await p.getByRole('textbox',{name:'Username',exact:true}).fill('editor');
   await p.getByLabel('Password',{exact:true}).fill('fictional walkthrough password');
   await p.getByRole('button',{name:'Sign in',exact:true}).click();
   await p.getByText('Create a project',{exact:true}).click();
   await p.getByRole('textbox',{name:'Project name',exact:true}).fill('Atlas reliability review (demo)');
   await p.getByRole('button',{name:'Create project',exact:true}).click();
   await p.getByRole('link',{name:'Open project: Atlas reliability review (demo)',exact:true}).waitFor();
   await p.waitForTimeout(1200);
   await p.getByRole('link',{name:/^Atlas \(demo\) 4 Conversations/}).click();
   await p.getByRole('button',{name:/Counterexample: long documents/}).click();
   await p.getByText('Project assignment and history',{exact:true}).click();
   await p.getByRole('combobox',{name:'Assign to project',exact:true}).selectOption({label:'Atlas reliability review (demo)'});
   await p.getByRole('button',{name:'Save assignment',exact:true}).click();
   await p.waitForTimeout(1200);
   await p.locator('.story-position > .story-state-grid').getByText('Source belongs to a different project scope now. Review this project state.',{exact:true}).waitFor();
   await p.getByRole('link',{name:'Back to projects',exact:true}).click();
   await p.getByRole('link',{name:/^Atlas reliability review \(demo\) 1 Conversations/}).click();
   await p.getByRole('button',{name:/Counterexample: long documents/}).click();
   await p.getByText('Project assignment and history',{exact:true}).click();
   await p.locator('.project-assignment').evaluate(el=>el.scrollIntoView({block:'center'}));
 },'The original folder and correction history remain visible. Earlier project notes flag a moved source.'],

];
const selected = teamDemo ? ['workspace-access','project-assignment'] : process.env.THROUGHLINE_DEMO_CLIPS?.split(',');
const manifest=selected ? JSON.parse(await readFile(path.join(out,'manifest.json'),'utf8')).clips.filter(c=>!selected.includes(c.name)) : [];
for (const [name,route,intro,act,outro] of scenarios) {
 if(selected && !selected.includes(name)) continue;
 if(!teamDemo && ['workspace-access','project-assignment'].includes(name))continue;
 const ctx=await browser.newContext({viewport:{width:1440,height:900},deviceScaleFactor:1,locale:'en-GB',timezoneId:'Europe/Amsterdam',recordVideo:{dir:out,size:{width:1440,height:900}},reducedMotion:'reduce'});
 await ctx.addInitScript(() => localStorage.setItem("pm-lang", "en"));
 const captureStart=Date.now();
 const cues=[];
 const page=await ctx.newPage();
 await page.goto(base+route);
 await page.waitForLoadState('networkidle');
 const caption=async text=>{
  cues.push({at:(Date.now()-captureStart)/1000,text});
  return page.evaluate(t=>{
  let el=document.getElementById('demo-caption');
  if(!el){el=document.createElement('div');el.id='demo-caption';document.body.appendChild(el);}
  el.textContent='FICTIONAL DEMO  ·  '+t;
  Object.assign(el.style,{position:'fixed',bottom:'0',left:'0',right:'0',zIndex:'10000',padding:'18px 40px',background:'#f3efe6',color:'#151b1e',font:'18px/1.5 Arial',borderTop:'3px solid #c55234'});
 },text);
 };
 await caption(intro); await page.waitForTimeout(3500);
 await act(page); await page.waitForLoadState('networkidle');
 await caption(outro); await page.waitForTimeout(5500);
 await page.screenshot({path:path.join(out,`${name}.png`)});
 const video=page.video(); await ctx.close();
 const raw=await video.path(); const mp4=path.join(out,`${name}.mp4`);
 const result=spawnSync(process.env.FFMPEG_BIN || 'ffmpeg',['-y','-i',raw,'-an','-c:v','libx264','-preset','fast','-crf','25','-pix_fmt','yuv420p','-movflags','+faststart',mp4],{encoding:'utf8'});
 if(result.status!==0) throw Error(result.stderr);
 const preview=spawnSync(process.env.FFMPEG_BIN || 'ffmpeg',['-y','-i',mp4,'-vf','fps=6,scale=800:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=96[p];[s1][p]paletteuse=dither=bayer','-loop','0',path.join(out,`${name}.gif`)],{encoding:'utf8'});
 if(preview.status!==0) throw Error(preview.stderr);
 await unlink(raw);
 const probe=spawnSync('ffprobe',['-v','error','-show_entries','format=duration','-of','csv=p=0',mp4],{encoding:'utf8'});
 const duration=Number(probe.stdout.trim());
 const stamp=value=>{const ms=Math.round(value*1000);return `${String(Math.floor(ms/3600000)).padStart(2,'0')}:${String(Math.floor(ms/60000)%60).padStart(2,'0')}:${String(Math.floor(ms/1000)%60).padStart(2,'0')}.${String(ms%1000).padStart(3,'0')}`;};
 const tracks=cues.map((cue,i)=>`${stamp(i===0?0:cue.at)} --> ${stamp(cues[i+1]?.at??duration)}\nFictional demo. ${cue.text}\n`).join('\n');
 await writeFile(path.join(out,`${name}.vtt`),`WEBVTT\n\n${tracks}`);
 manifest.push({name,route,intro,outro,mode:teamDemo?'team':'local',durationSeconds:duration});
 console.log(`Recorded ${name}: ${duration.toFixed(1)}s`);
}
await browser.close();
await writeFile(path.join(out,'manifest.json'),JSON.stringify({fixture:'scripts/seed_demo_data.py',frontendSha256,recordedAtUtc:new Date().toISOString(),corpus:{conversations:44,messages:249},clips:manifest},null,2)+'\n');
