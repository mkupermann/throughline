#!/usr/bin/env node
/** Record the real, fictional-data demo without model calls or agent launches.
 * Run with Playwright Chromium installed. Convert tour.webm using the commands
 * in docs/media/README.md. No API stubs or fabricated interface states.
 */
import { chromium } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const out = process.env.THROUGHLINE_MEDIA_DIR || path.join(root, 'docs/media');
const base = process.env.THROUGHLINE_DEMO_URL || 'http://127.0.0.1:8795';
const history = await fetch(base+'/api/story/Atlas%20(demo)/history').then(r=>r.json());
const overview = await fetch(base+'/api/overview').then(r=>r.json());
if(history.total!==4 || !history.paths.every(p=>p.path==='/fictional/Atlas (demo)') || overview.totals?.conversations!==44 || overview.totals?.messages!==249) throw Error('Refusing capture: expected isolated fictional demo corpus.');
await mkdir(out,{recursive:true});
const browser = await chromium.launch();
const context = await browser.newContext({viewport:{width:1440,height:960},recordVideo:{dir:out,size:{width:1440,height:960}},reducedMotion:'reduce',locale:'en-GB'});
await context.addInitScript(()=>{localStorage.setItem('pm-lang','en');localStorage.setItem('throughline-theme','light');});
const page=await context.newPage();page.setDefaultTimeout(15000);
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const start=Date.now();const cues=[];
const cue=async(text)=>{cues.push({at:(Date.now()-start)/1000,text});await page.evaluate(text=>{let el=document.getElementById('tour-caption');if(!el){el=document.createElement('div');el.id='tour-caption';document.body.append(el);}el.textContent='FICTIONAL DEMO · '+text;Object.assign(el.style,{position:'fixed',bottom:'0',left:'0',right:'0',zIndex:'10000',padding:'14px 28px',background:'var(--surface-base)',color:'var(--text-primary)',borderTop:'2px solid var(--accent)',font:'16px/1.5 system-ui,sans-serif'});},text);};
const pause=ms=>page.waitForTimeout(ms);
const go=async route=>{await page.goto(base+route);await page.waitForLoadState('networkidle');};
const shot=async name=>{await page.evaluate(()=>document.getElementById('tour-caption')?.remove());await page.screenshot({path:path.join(out,name+'.png')});};
await go('/project/Atlas%20(demo)');await page.getByRole('heading',{name:'Where we stand'}).waitFor();await shot('workspace');await cue('Recover a project’s goal, current position and next step.');await pause(3500);
await page.locator('#history-title').scrollIntoViewIfNeeded();await cue('Keep source conversations and exact evidence within reach.');await pause(3000);
await go('/pm');await shot('operations');await cue('See real project activity and the work that needs attention.');await pause(3500);
await go('/pm/templates');await page.getByRole('combobox',{name:'Category',exact:true}).selectOption('finance');await shot('templates');await cue('Browse reusable project, team and role templates by domain.');await pause(3000);
await page.locator('.template-card').first().click();await cue('Preview the brief, deliverables and acceptance criteria before use.');await pause(4000);
await page.getByRole('button',{name:'Close preview',exact:true}).click();await page.getByRole('combobox',{name:'Category',exact:true}).selectOption('science');await cue('Science workflows include reproducibility and independent review.');await pause(3000);
await page.getByRole('combobox',{name:'Category',exact:true}).selectOption('education');await cue('Education workflows turn learning goals into practical deliverables.');await pause(3000);
await page.getByRole('button',{name:/Team templates/}).click();await cue('Teams connect specialized roles. Choose members and models separately.');await pause(3000);
await page.getByRole('button',{name:/Role templates/}).click();await page.locator('.template-card').first().click();await cue('Role instructions define responsibilities and expected outputs.');await pause(3500);
await go('/pm/templates');await cue('Templates save configuration. Actual execution needs a compatible executor.');await pause(3500);
const end=(Date.now()-start)/1000;
const video=page.video();await context.close();await video.saveAs(path.join(out,'tour.webm'));
const mobile=await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'});const mp=await mobile.newPage();await mp.goto(base+'/project/Atlas%20(demo)');await mp.waitForLoadState('networkidle');await mp.screenshot({path:path.join(out,'mobile.png')});await mobile.close();await browser.close();
const stamp=n=>{const ms=Math.round(n*1000);return `${String(Math.floor(ms/3600000)).padStart(2,'0')}:${String(Math.floor(ms/60000)%60).padStart(2,'0')}:${String(Math.floor(ms/1000)%60).padStart(2,'0')}.${String(ms%1000).padStart(3,'0')}`;};
await writeFile(path.join(out,'throughline-tour.vtt'),'WEBVTT\n\n'+cues.map((c,i)=>`${i+1}\n${stamp(c.at)} --> ${stamp(cues[i+1]?.at??end)}\n${c.text}\n`).join('\n'));
await writeFile(path.join(out,'manifest.json'),JSON.stringify({captured_at:new Date().toISOString(),fictional_data:true,model_calls:false,agent_launches:false,duration_seconds:end,viewport:{width:1440,height:960},captions:cues,page_errors:errors},null,2)+'\n');
if(errors.length)throw Error(errors.join('\n'));console.log(JSON.stringify({out,duration:end,cues:cues.length}));
