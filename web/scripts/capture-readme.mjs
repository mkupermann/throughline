#!/usr/bin/env node
// Real Rust UI, fictional database only. Never intercept or fabricate API responses.
import { chromium } from 'playwright';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const base = process.env.THROUGHLINE_DEMO_URL || 'http://127.0.0.1:8796';
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw Error('Loopback demo required');
const out = path.join(root, 'docs/media/rust');
const json = async route => {
  const r = await fetch(base + route);
  if (!r.ok) throw Error(`${route}: HTTP ${r.status}`);
  return r.json();
};
const status = await json('/api/status');
const history = await json('/api/conversations?limit=200');
const rows = history.conversations;
if (status.conversations !== 18 || status.messages !== 72 || status.memory_chunks !== 12 ||
    !Array.isArray(rows) || rows.length !== 18 ||
    !rows.every(r => ['Atlas-demo','Harbor-demo','Orchard-demo'].includes(r.project_name) &&
      r.session_id?.startsWith('d0000000-0000-4000-8000-'))) {
  throw Error('Refusing capture: expected complete throughline-readme-v1 fixture');
}
const source = await readFile(path.join(root, 'rust/crates/throughline/web/index.html'), 'utf8');
const served = await fetch(base + '/').then(r => r.text());
const normalize = text => text.replace(/\r\n/g, '\n');
if (normalize(source) !== normalize(served)) throw Error('Served UI differs from checkout; rebuild demo image');
await mkdir(out, {recursive:true});
const browser = await chromium.launch({channel:process.env.THROUGHLINE_SCREENSHOT_BROWSER || undefined});
const page = await browser.newPage({viewport:{width:1440,height:1000}, deviceScaleFactor:1, reducedMotion:'reduce',locale:'en-GB',timezoneId:'UTC'});
page.setDefaultTimeout(20000);
const errors = [];
const shots = [];
page.on('pageerror', e => errors.push(e.message));
const shot = async name => {
  await page.waitForTimeout(350);
  await page.screenshot({path:path.join(out, name + '.png'), fullPage:true});
  shots.push(name);
};
const view = async name => {
  await page.locator(`#nav [data-view="${name}"]`).click();
  await page.locator(`#view-${name}`).waitFor({state:'visible'});
  await page.waitForLoadState('networkidle');
};
try {
  await page.goto(base);
  await page.waitForLoadState('networkidle');
  await page.locator('#m-conv').filter({hasText:'18'}).waitFor();
  await shot('home');
  await view('history'); await shot('history');
  await page.locator('#hist-cards-btn').click(); await shot('history-cards');
  await page.locator('#hist-list [data-id]').first().click();
  await page.locator('#view-detail').waitFor({state:'visible'});
  await page.waitForLoadState('networkidle');
  await page.locator('#detail-expand').click(); await shot('conversation');
  await view('browse'); await shot('browse');
  await page.locator('#browse-toolonly').click();
  await page.waitForLoadState('networkidle');
  await page.locator('#browse-list [data-msg]').first().click();
  await page.locator('#view-msg').waitFor({state:'visible'});
  await page.waitForLoadState('networkidle'); await shot('message');
  await view('memory'); await shot('memory');
  await view('find');
  await page.locator('#find-q').fill('decision');
  await page.locator('#find-mode').selectOption('lexical');
  await page.locator('#find-go').click();
  await page.locator('#find-results [data-id]').first().waitFor({state:'visible'});
  await page.waitForLoadState('networkidle'); await shot('find');
  await view('projects'); await shot('projects');
  await view('timeline'); await shot('timeline');
  await page.locator('#timeline-mode-cal').click(); await shot('calendar');
  await view('ask');
  await page.locator('#ask-q').fill('What did we decide about retrieval?');
  // Capture the input state, not a fabricated answer or a model request.
  await shot('ask');
  await view('operate'); await shot('operate');
  await page.locator('[data-job="status"]').click();
  await page.locator('#operate-out').filter({hasText:'conversations=18'}).waitFor();
  await page.locator('#operate-out').scrollIntoViewIfNeeded();
  await shot('operate-status');
  await view('home');
  await page.locator('#open-palette').click(); await shot('palette');
} finally {
  await browser.close();
}
await writeFile(path.join(out, 'manifest.json'), JSON.stringify({
  fixture:'docs/demo/rust/seed.sql', capturedAt:new Date().toISOString(),
  uiSha256:createHash('sha256').update(normalize(source)).digest('hex'),
  fictionalData:true, mockedResponses:false, modelCalls:false, agentLaunches:false,
  corpus:status, viewport:{width:1440,height:1000}, screenshots:shots, pageErrors:errors,
},null,2) + '\n');
if (errors.length) throw Error(errors.join('\n'));
console.log(`Captured ${shots.length} screenshots; served UI matches source; no page errors.`);
