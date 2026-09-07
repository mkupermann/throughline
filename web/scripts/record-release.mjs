#!/usr/bin/env node
/** Record actual UI interactions against scripts/seed_demo_data.py only.
 * No response stubs, live imports, model calls, task launches or SQL writes.
 * Requires ffmpeg and Playwright Chromium. See docs/DEMO.md.
 */
import { chromium } from 'playwright';
import { mkdir, writeFile, unlink, readFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const out = path.join(root, 'docs/videos');
const base = process.env.THROUGHLINE_DEMO_URL || 'http://127.0.0.1:8794';
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw Error('Use a loopback demo server.');
const history = await fetch(`${base}/api/story/Atlas%20(demo)/history`).then(r=>r.json());
if (history.total !== 4 || !history.sessions.some(s=>s.title?.startsWith('Counterexample:')) || !history.paths.every(p=>p.path==='/fictional/Atlas (demo)')) throw Error('The server is not the fictional project-story fixture.');
const total = await fetch(`${base}/api/overview`).then(r=>r.json());
if (total.totals?.conversations !== 44 || total.totals?.messages !== 249) throw Error('Unexpected corpus: refuse to record.');
await mkdir(out,{recursive:true});
const browser = await chromium.launch({headless:true});
const scenarios = [
 ['projects','/', 'Open a project and recover its current state.', async p=>{
   await p.getByRole('textbox',{name:'Find a project'}).fill('Atlas');
   await p.getByRole('link',{name:/Atlas \(demo\)/}).click();
 }, 'Goal, current position, blocker and next step each link to a source.'],
 ['conversations','/conversations','Conversations are organised by project.',async p=>{
   await p.getByRole('link',{name:/Atlas \(demo\)/}).click();
   await p.getByRole('button',{name:/Counterexample: long documents/}).click();
   await p.getByText('evaluation.md',{exact:false}).last().scrollIntoViewIfNeeded();
 },'Prompt, answer and recorded output stay inside their conversation. Times include seconds and timezone.'],
 ['find','/find?q=boundary','Search across imported records.',async p=>{
   await p.evaluate(()=>window.scrollBy(0,400));
 },'Results retain source links. Search does not require a generation model.'],
 ['timeline','/timeline','Compare conversation activity across tools and dates.',async p=>{
   const cells=p.getByRole('button',{name:/[1-9][0-9]* events/});
   if (await cells.count()) await cells.first().click();
   await p.evaluate(()=>window.scrollBy(0,350));
 },'Open a dated bucket to inspect its conversations. Messages and memories are not separate events.'],
 ['review','/curate','Inspect knowledge that may need correction.',async p=>{
   await p.evaluate(()=>window.scrollBy(0,430));
 },'The fixture includes contradictory and superseded statements. A suggestion is not a verified fact.'],
 ['operate','/operate','Understand the import and processing stages.',async p=>{
   await p.evaluate(()=>window.scrollBy(0,350));
 },'Discovery, import, extraction and embeddings have separate states. No processing is started in this clip.'],
 ['console','/console','Inspect the fictional corpus with read-only SQL.',async p=>{
   await p.getByRole('textbox',{name:'SQL query'}).fill('SELECT source_tool, count(*) AS conversations FROM conversations GROUP BY source_tool ORDER BY source_tool');
   await p.getByRole('button',{name:'Run',exact:true}).click();
 },'The query counts conversations by source tool. PostgreSQL enforces read-only execution.'],
 ['teams','/pm','Connect project history with optional AI-team operations.',async p=>{
   await p.getByRole('link',{name:/Acme Storefront Relaunch/}).first().click();
   await p.getByRole('heading',{name:'Tasks',exact:true}).scrollIntoViewIfNeeded();
   await p.evaluate(()=>window.scrollBy(0,350));
 },'Fictional tasks show completed, blocked and running states. No agent is launched.'],
 ['roles','/pm/roles','Define the responsibility and tool for a role.',async p=>{await p.evaluate(()=>window.scrollBy(0,300));},'The demo separates analysis, execution, testing and review.'],
 ['members','/pm/members','See the people and agents available to teams.',async p=>{await p.evaluate(()=>window.scrollBy(0,250));},'These identities are invented fixtures, not real team members.'],
 ['pipelines','/pm/teams','Inspect the roles and sequence in a team pipeline.',async p=>{await p.evaluate(()=>window.scrollBy(0,300));},'Team configuration is reusable across linked projects.'],
 ['models','/pm/models','Inspect provider configuration separately from project history.',async p=>{await p.evaluate(()=>window.scrollBy(0,250));},'Provider entries are fictional. This recording performs no hosted generation.'],
];
const selected = process.env.THROUGHLINE_DEMO_CLIPS?.split(',');
const manifest=selected ? JSON.parse(await readFile(path.join(out,'manifest.json'),'utf8')).clips.filter(c=>!selected.includes(c.name)) : [];
for (const [name,route,intro,act,outro] of scenarios) {
 if(selected && !selected.includes(name)) continue;
 const ctx=await browser.newContext({viewport:{width:1440,height:900},deviceScaleFactor:1,locale:'en-GB',timezoneId:'Europe/Amsterdam',recordVideo:{dir:out,size:{width:1440,height:900}},reducedMotion:'reduce'});
 const page=await ctx.newPage();
 await page.goto(base+route);
 await page.waitForLoadState('networkidle');
 const caption=async text=>page.evaluate(t=>{
  let el=document.getElementById('demo-caption');
  if(!el){el=document.createElement('div');el.id='demo-caption';document.body.appendChild(el);}
  el.textContent='FICTIONAL DEMO  ·  '+t;
  Object.assign(el.style,{position:'fixed',bottom:'0',left:'0',right:'0',zIndex:'10000',padding:'18px 40px',background:'#f3efe6',color:'#151b1e',font:'18px/1.5 Arial',borderTop:'3px solid #c55234'});
 },text);
 await caption(intro); await page.waitForTimeout(3500);
 await act(page); await page.waitForLoadState('networkidle');
 await caption(outro); await page.waitForTimeout(5500);
 await page.screenshot({path:path.join(out,`${name}.png`)});
 const video=page.video(); await ctx.close();
 const raw=await video.path(); const mp4=path.join(out,`${name}.mp4`);
 const result=spawnSync(process.env.FFMPEG_BIN || 'ffmpeg',['-y','-i',raw,'-an','-c:v','libx264','-preset','fast','-crf','25','-pix_fmt','yuv420p','-movflags','+faststart',mp4],{encoding:'utf8'});
 if(result.status!==0) throw Error(result.stderr);
 await unlink(raw);
 const probe=spawnSync('ffprobe',['-v','error','-show_entries','format=duration','-of','csv=p=0',mp4],{encoding:'utf8'});
 const duration=Number(probe.stdout.trim());
 await writeFile(path.join(out,`${name}.vtt`),`WEBVTT\n\n00:00.000 --> 00:04.000\nFictional demo. ${intro}\n\n00:04.000 --> 00:${duration.toFixed(3).padStart(6,'0')}\n${outro}\n`);
 manifest.push({name,route,intro,outro,durationSeconds:duration});
 console.log(`Recorded ${name}: ${duration.toFixed(1)}s`);
}
await browser.close();
await writeFile(path.join(out,'manifest.json'),JSON.stringify({fixture:'scripts/seed_demo_data.py',corpus:{conversations:44,messages:249},clips:manifest},null,2)+'\n');
