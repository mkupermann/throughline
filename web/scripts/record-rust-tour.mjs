#!/usr/bin/env node
/**
 * Record the Rust UI tour and render launch artwork against the fictional demo fixture.
 *
 * Real UI, fictional database only. Never intercept or fabricate API responses.
 * Expects a server already running against the throughline-readme-v1 fixture
 * (default http://127.0.0.1:8796) and fails loudly rather than photographing
 * whatever happens to be on the port.
 *
 *   node web/scripts/record-rust-tour.mjs
 *
 * Outputs (repo-relative, under docs/media/rust/):
 *   tour.webm            1440x900 captioned walkthrough
 *   tour-manifest.json   cues, duration, page errors
 *   hero.png             1280x960 launch artwork from Home
 *   social-preview.png   1280x640 social artwork from Timeline
 */
import { chromium } from 'playwright';
import { readFile, mkdir, rename, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const base = (process.env.THROUGHLINE_DEMO_URL || 'http://127.0.0.1:8796').replace(/\/$/, '');
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw Error('Loopback demo required');
const out = path.join(root, 'docs/media/rust');

// ── Fixture guard (identical to capture-readme.mjs) ──────────────────────
// Hard abort before any recording starts when the corpus or served UI does
// not match the expected fictional demo fixture.
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

await mkdir(out, { recursive: true });

const browser = await chromium.launch({ channel: process.env.THROUGHLINE_SCREENSHOT_BROWSER || undefined });
const errors = [];
const cues = [];
let start = 0;

/** Caption overlay + cue log, as in record-product-tour.mjs. */
const say = async (page, text, ms, action) => {
  const t0 = Date.now() - start;
  cues.push({ at: t0 / 1000, text });
  await page.evaluate(text => {
    let el = document.getElementById('tour-caption');
    if (!el) {
      el = document.createElement('div');
      el.id = 'tour-caption';
      document.body.append(el);
    }
    el.textContent = 'FICTIONAL DEMO · ' + text;
    Object.assign(el.style, {
      position: 'fixed', bottom: '0', left: '0', right: '0', zIndex: '10000',
      padding: '14px 28px', background: 'var(--surface-base)', color: 'var(--text-primary)',
      borderTop: '2px solid var(--accent)', font: '16px/1.5 system-ui,sans-serif',
    });
  }, text);
  if (action) await action();
  const spent = Date.now() - start - t0;
  await page.waitForTimeout(Math.max(400, ms - spent));
};

const view = async (page, name) => {
  await page.locator(`#nav [data-view="${name}"]`).click();
  await page.locator(`#view-${name}`).waitFor({ state: 'visible' });
  await page.waitForLoadState('networkidle');
};

// ── Video tour ───────────────────────────────────────────────────────────
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
  recordVideo: { dir: out, size: { width: 1440, height: 900 } },
  reducedMotion: 'reduce',
  locale: 'en-GB',
  timezoneId: 'UTC',
});
const page = await context.newPage();
page.setDefaultTimeout(20000);
page.on('pageerror', e => errors.push(e.message));
start = Date.now();

try {
  await page.goto(base);
  await page.waitForLoadState('networkidle');
  await page.locator('#m-conv').filter({ hasText: '18' }).waitFor();

  await say(page, 'Every coding session in one local memory store.', 3200);

  await say(page, 'Group the corpus into named project workspaces.', 2800, async () => {
    await view(page, 'projects');
    await page.locator('#projects-grid .card').first().waitFor();
  });

  await say(page, 'Open Atlas-demo history — every source session in one place.', 3200, async () => {
    await page.locator('[data-project="Atlas-demo"]').click();
    await page.locator('#view-history').waitFor({ state: 'visible' });
    await page.locator('#hist-tbody tr[data-id]').first().waitFor();
    await page.waitForLoadState('networkidle');
  });

  await say(page, 'Read the full conversation with every message expanded.', 3600, async () => {
    await page.locator('#hist-tbody tr[data-id]').first().click();
    await page.locator('#view-detail').waitFor({ state: 'visible' });
    await page.waitForLoadState('networkidle');
    await page.locator('#detail-expand').click();
    await page.locator('#detail-messages .msg.open').first().waitFor();
  });

  await say(page, 'Inspect tool calls with full request and response detail.', 3600, async () => {
    await view(page, 'browse');
    await page.locator('#browse-toolonly').click();
    await page.waitForLoadState('networkidle');
    await page.locator('#browse-list [data-msg]').first().click();
    await page.locator('#view-msg').waitFor({ state: 'visible' });
    await page.locator('#msg-tool-panel').waitFor({ state: 'visible' });
    await page.waitForLoadState('networkidle');
  });

  await say(page, 'Filter durable agent memory by category.', 3200, async () => {
    await view(page, 'memory');
    await page.locator('#memory-cat').selectOption('decision');
    await page.locator('#memory-go').click();
    await page.locator('#memory-list .mem-card').first().waitFor();
    await page.waitForLoadState('networkidle');
  });

  await say(page, 'Search the corpus with lexical mode.', 3200, async () => {
    await view(page, 'find');
    await page.locator('#find-mode').selectOption('lexical');
    await page.locator('#find-q').fill('decision');
    await page.locator('#find-go').click();
    await page.locator('#find-results [data-id]').first().waitFor();
    await page.waitForLoadState('networkidle');
  });

  await say(page, 'See activity by day and tool.', 2800, async () => {
    await view(page, 'timeline');
    await page.locator('#timeline-list .row').first().waitFor();
  });

  await say(page, 'Switch to a calendar view of the same activity.', 2800, async () => {
    await page.locator('#timeline-mode-cal').click();
    await page.locator('#timeline-calendar .cal-day[data-day]').first().waitFor();
  });

  await say(page, 'Run maintenance jobs like Status against the corpus.', 3200, async () => {
    await view(page, 'operate');
    await page.locator('[data-job="status"]').click();
    await page.locator('#operate-out').filter({ hasText: 'conversations=18' }).waitFor();
    await page.waitForLoadState('networkidle');
  });

  await say(page, 'Jump anywhere with the command palette.', 2800, async () => {
    await page.keyboard.press('Control+k');
    await page.locator('#palette.open').waitFor();
  });

  await say(page, 'Everything stays local — a fictional demo corpus.', 2800, async () => {
    await page.keyboard.press('Escape');
    await view(page, 'home');
  });
} finally {
  const video = page.video();
  await context.close();
  if (video) {
    const raw = await video.path();
    await rename(raw, path.join(out, 'tour.webm'));
  }
}

const duration = (Date.now() - start) / 1000;

// ── Launch artwork (composition per captureArtwork in capture-screenshots.mjs) ──
const captureArtwork = async ({ name, view: viewName, waitFor, sourceViewport, canvas, frame }) => {
  const sourceContext = await browser.newContext({
    viewport: sourceViewport,
    deviceScaleFactor: 1,
    reducedMotion: 'reduce',
    locale: 'en-GB',
    timezoneId: 'UTC',
  });
  const sourcePage = await sourceContext.newPage();
  sourcePage.on('pageerror', e => errors.push(`${name} source page error: ${e.message}`));
  try {
    await sourcePage.goto(base, { waitUntil: 'networkidle', timeout: 30_000 });
    await sourcePage.locator(`#nav [data-view="${viewName}"]`).click();
    await sourcePage.locator(`#view-${viewName}`).waitFor({ state: 'visible' });
    await sourcePage.locator(waitFor).first().waitFor({ timeout: 15_000 });
    await sourcePage.waitForLoadState('networkidle');
    await sourcePage.waitForTimeout(500);
    const interfacePng = await sourcePage.screenshot({ fullPage: false });
    const interfaceUrl = `data:image/png;base64,${interfacePng.toString('base64')}`;

    const artworkContext = await browser.newContext({
      viewport: canvas,
      deviceScaleFactor: 1,
    });
    const artworkPage = await artworkContext.newPage();
    await artworkPage.setContent(`
      <!doctype html>
      <html lang="en">
        <head>
          <meta charset="utf-8">
          <style>
            * { box-sizing: border-box; }
            html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; }
            body {
              display: grid;
              place-items: center;
              background:
                linear-gradient(133deg, transparent 0 10%, rgba(255,255,255,.11) 10% 17%, transparent 17% 37%, rgba(0,54,184,.2) 37% 44%, transparent 44% 68%, rgba(255,255,255,.08) 68% 75%, transparent 75%),
                linear-gradient(135deg, #0aa9f2 0%, #087cec 42%, #075bda 68%, #0644b8 100%);
            }
            body::before,
            body::after {
              content: "";
              position: absolute;
              width: 22%;
              height: 150%;
              background: rgba(0,58,180,.18);
              transform: rotate(42deg);
            }
            body::before { left: -10%; top: -45%; }
            body::after { right: -10%; bottom: -48%; background: rgba(0,38,143,.25); }
            .frame {
              position: relative;
              z-index: 1;
              width: ${frame.width}px;
              height: ${frame.height}px;
              overflow: hidden;
              border: 1px solid rgba(255,255,255,.58);
              border-radius: 3px;
              background: #f8fafc;
              box-shadow: 0 24px 60px rgba(0,31,111,.46), 0 5px 16px rgba(0,22,85,.26);
            }
            img {
              display: block;
              width: 100%;
              height: 100%;
              object-fit: cover;
              object-position: top left;
            }
          </style>
        </head>
        <body><div class="frame"><img alt="" src="${interfaceUrl}"></div></body>
      </html>
    `);
    await artworkPage.screenshot({
      path: path.join(out, `${name}.png`),
      fullPage: false,
    });
    await artworkContext.close();
  } catch (err) {
    errors.push(`${name}: ${err.message}`);
    await sourcePage.screenshot({ path: path.join(out, `${name}.FAILED.png`) }).catch(() => {});
  }
  await sourceContext.close();
};

// Hero follows the launch-artwork composition: Home view in a blue diagonal field.
await captureArtwork({
  name: 'hero',
  view: 'home',
  waitFor: '#m-conv',
  sourceViewport: { width: 1360, height: 1020 },
  canvas: { width: 1280, height: 960 },
  frame: { width: 1134, height: 850 },
});

// GitHub social preview is a 2:1 crop of the Timeline.
await captureArtwork({
  name: 'social-preview',
  view: 'timeline',
  waitFor: '#timeline-list .row',
  sourceViewport: { width: 1400, height: 700 },
  canvas: { width: 1280, height: 640 },
  frame: { width: 1120, height: 560 },
});

await browser.close();

await writeFile(path.join(out, 'tour-manifest.json'), JSON.stringify({
  captured_at: new Date().toISOString(),
  fictional_data: true,
  model_calls: false,
  duration_seconds: duration,
  viewport: { width: 1440, height: 900 },
  cues,
  page_errors: errors,
}, null, 2) + '\n');

if (errors.length) throw Error(errors.join('\n'));
console.log(`Tour ${duration.toFixed(1)}s, ${cues.length} cues; hero + social-preview written; no page errors.`);
