#!/usr/bin/env node
// UX E2E against the real Rust UI + fictional demo database.
// Never intercept or fabricate API responses (same posture as capture-readme.mjs).
// Checks: (a) copy handoff, (b) download handoff .md, (c) theme toggle + persist,
// (d) pin-as-note, (e) review forget. Exit 0 only when every check is green.
// Phase 2 runs the same UI against a migrate-only throwaway DB for empty states.
import { chromium } from 'playwright';
import { readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { execFileSync, spawn } from 'node:child_process';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const base = process.env.THROUGHLINE_DEMO_URL || 'http://127.0.0.1:8796';
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw Error('Loopback demo required');
const out = path.join(root, 'docs/media/rust/ux');
const json = async route => {
  const r = await fetch(base + route);
  if (!r.ok) throw Error(`${route}: HTTP ${r.status}`);
  return r.json();
};
const post = async (route, body) => {
  const r = await fetch(base + route, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
  const text = await r.text();
  let parsed = null;
  try { parsed = JSON.parse(text); } catch {}
  return { status: r.status, body: parsed, text };
};

// ─── Fixture guard (mirror capture-readme.mjs) ────────────────────────────
const status = await json('/api/status');
const history = await json('/api/conversations?limit=200');
const rows = history.conversations;
if (status.conversations !== 18 || status.messages !== 72 || status.memory_chunks !== 12 ||
    !Array.isArray(rows) || rows.length !== 18 ||
    !rows.every(r => ['Atlas-demo','Harbor-demo','Orchard-demo'].includes(r.project_name) &&
      r.session_id?.startsWith('d0000000-0000-4000-8000-'))) {
  throw Error('Refusing e2e: expected complete throughline-readme-v1 fixture');
}
const source = await readFile(path.join(root, 'rust/crates/throughline/web/index.html'), 'utf8');
const served = await fetch(base + '/').then(r => r.text());
const normalize = text => text.replace(/\r\n/g, '\n');
if (normalize(source) !== normalize(served)) throw Error('Served UI differs from checkout; rebuild demo image');

await mkdir(out, { recursive: true });

// ─── Contrast samples (main text/background variables, both themes) ───────
function lum(hex) {
  const h = hex.replace('#', '');
  const [r, g, b] = [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16) / 255);
  const f = v => v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
function contrast(a, b) {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
const themePairs = {
  dark: {
    'text/bg': ['#f3f7fc', '#0b0e14'],
    'text-2/bg': ['#b6c2d6', '#0b0e14'],
    'text-3/bg': ['#8b9bb3', '#0b0e14'],
    'text/bg-elevated': ['#f3f7fc', '#121822'],
    'text-2/bg-elevated': ['#b6c2d6', '#121822'],
  },
  light: {
    'text/bg': ['#0f172a', '#f4f6fa'],
    'text-2/bg': ['#334155', '#f4f6fa'],
    'text-3/bg': ['#5b6b80', '#f4f6fa'],
    'text/bg-elevated': ['#0f172a', '#ffffff'],
    'text-2/bg-elevated': ['#334155', '#ffffff'],
  },
};
const contrastReport = {};
for (const [theme, pairs] of Object.entries(themePairs)) {
  contrastReport[theme] = {};
  for (const [name, [fg, bg]] of Object.entries(pairs)) {
    const ratio = contrast(fg, bg);
    contrastReport[theme][name] = { fg, bg, ratio: Number(ratio.toFixed(2)), aa: ratio >= 4.5 };
    if (ratio < 4.5) throw Error(`Contrast ${theme} ${name} = ${ratio.toFixed(2)} < 4.5`);
  }
}

const results = {};
const fail = (name, msg) => { results[name] = { ok: false, detail: msg }; throw Error(`${name}: ${msg}`); };
const pass = (name, detail) => {
  results[name] = { ok: true, detail };
  console.log(`  ✓ ${name} — ${detail}`);
};

// ─── Browser: fixture demo ────────────────────────────────────────────────
const browser = await chromium.launch({ channel: process.env.THROUGHLINE_SCREENSHOT_BROWSER || undefined });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  deviceScaleFactor: 1,
  reducedMotion: 'reduce',
  locale: 'en-GB',
  timezoneId: 'UTC',
  permissions: ['clipboard-read', 'clipboard-write'],
});
const page = await context.newPage();
page.setDefaultTimeout(20000);
const pageErrors = [];
page.on('pageerror', e => pageErrors.push(e.message));

const view = async name => {
  await page.locator(`#nav [data-view="${name}"]`).click();
  await page.locator(`#view-${name}`).waitFor({ state: 'visible' });
  await page.waitForLoadState('networkidle');
};

try {
  await page.goto(base);
  await page.waitForLoadState('networkidle');
  await page.locator('#m-conv').filter({ hasText: '18' }).waitFor();

  // ── (a) Copy handoff puts non-empty text on the clipboard ───────────────
  await page.locator('#handoff-copy').click();
  await page.waitForTimeout(400);
  const clip = await page.evaluate(() => navigator.clipboard.readText());
  if (!clip || !clip.trim()) fail('a-copy-handoff', 'clipboard empty');
  if (!clip.includes('Throughline handoff')) fail('a-copy-handoff', `unexpected clipboard prefix: ${clip.slice(0, 80)}`);
  pass('a-copy-handoff', `${clip.length} chars, starts with handoff heading`);

  // ── (b) Download .md produces a file with content ───────────────────────
  const dlPromise = page.waitForEvent('download');
  await page.locator('#handoff-download').click();
  const download = await dlPromise;
  const dlName = download.suggestedFilename();
  if (!/^handoff-.+-.+\.md$/.test(dlName)) fail('b-download-handoff', `bad filename ${dlName}`);
  const dlPath = path.join(out, dlName);
  await download.saveAs(dlPath);
  const dlText = await readFile(dlPath, 'utf8');
  if (!dlText.trim() || !dlText.includes('Throughline handoff')) {
    fail('b-download-handoff', `download missing content (${dlText.length} bytes)`);
  }
  pass('b-download-handoff', `${dlName} with ${dlText.length} bytes`);

  // ── (c) Theme switcher flips data-theme and survives reload ─────────────
  const beforeTheme = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
  await page.locator('#theme-select').selectOption('light');
  await page.waitForTimeout(200);
  let nowTheme = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
  if (nowTheme !== 'light') fail('c-theme-toggle', `expected light after switch, got ${nowTheme}`);
  await page.screenshot({ path: path.join(out, 'home-light.png'), fullPage: true });
  await view('history');
  await page.screenshot({ path: path.join(out, 'history-light.png'), fullPage: true });
  await page.reload();
  await page.waitForLoadState('networkidle');
  nowTheme = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
  const stored = await page.evaluate(() => localStorage.getItem('th-theme'));
  if (nowTheme !== 'light') fail('c-theme-toggle', `theme lost after reload: ${nowTheme}`);
  if (stored !== 'light') fail('c-theme-toggle', `localStorage th-theme=${stored}`);
  await page.locator('#theme-select').selectOption('dark');
  await page.waitForTimeout(150);
  nowTheme = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
  if (nowTheme !== 'dark') fail('c-theme-toggle', `expected dark after switch, got ${nowTheme}`);
  await page.locator('#theme-select').selectOption('light');
  await page.waitForTimeout(150);
  pass('c-theme-toggle', `was ${beforeTheme}, light persists across reload, th-theme=${stored}`);

  // ── (d) Pin-as-note creates a memory note (count before/after) ──────────
  const memBefore = await json('/api/memory?limit=1');
  await view('find');
  await page.locator('#find-q').fill('decision');
  await page.locator('#find-mode').selectOption('lexical');
  await page.locator('#find-go').click();
  await page.locator('#find-results [data-id]').first().waitFor({ state: 'visible' });
  await page.locator('#find-results .pin-hit').first().click();
  await page.locator('#pin-dialog.open').waitFor({ state: 'visible' });
  const pinContent = await page.locator('#pin-content').inputValue();
  if (!pinContent.includes('Source: conversation #')) fail('d-pin-note', 'pin content missing source line');
  await page.locator('#pin-category').selectOption('insight');
  await page.locator('#pin-save').click();
  await page.locator('#toast').filter({ hasText: 'Pinned note' }).waitFor({ timeout: 10000 });
  const memAfter = await json('/api/memory?limit=1');
  if (!(memAfter.total > memBefore.total)) {
    fail('d-pin-note', `memory total ${memBefore.total} → ${memAfter.total}`);
  }
  pass('d-pin-note', `memory total ${memBefore.total} → ${memAfter.total}`);

  // ── (e) Review lists stale candidates; Forget removes the card ─────────
  // Produce a stale candidate without fabricating API data: write two identical
  // ui_pin notes via the real POST /api/memory, then run the real Reflect job,
  // which supersedes the duplicate (superseded_at = now()). /api/stale-digest
  // lists recently superseded chunks — the Review tab's data source.
  const twinBody = {
    content: 'UX-E2E review candidate: identical twin note created to exercise reflect supersede.',
    category: 'insight',
    project_name: 'Atlas-demo',
  };
  const t1 = await post('/api/memory', twinBody);
  const t2 = await post('/api/memory', twinBody);
  if (t1.status !== 200 || t2.status !== 200) fail('e-review-forget', `plant ${t1.status}/${t2.status}`);
  const reflect = await json('/api/operate?job=reflect');
  if (!reflect.message) fail('e-review-forget', 'reflect produced no message');
  const stale = await json('/api/stale-digest');
  const items = stale.items || [];
  if (!items.length) fail('e-review-forget', `stale-digest empty after reflect (${reflect.message})`);
  await view('memory');
  await page.locator('#memory-tab-review').click();
  await page.locator('#memory-review-panel').waitFor({ state: 'visible' });
  await page.locator('#review-list .review-card, #review-list .empty').first().waitFor({ state: 'visible', timeout: 15000 });
  await page.waitForLoadState('networkidle');
  const reviewCountText = (await page.locator('#review-count').textContent()) || '';
  const cardsBefore = await page.locator('#review-list .review-card').count();
  if (cardsBefore === 0) fail('e-review-forget', `count text "${reviewCountText}" but 0 cards`);
  if (!/\d+/.test(reviewCountText)) fail('e-review-forget', `counter missing digits: "${reviewCountText}"`);
  const firstId = await page.locator('#review-list .review-card').first().getAttribute('data-review-id');
  page.once('dialog', d => d.accept());
  await page.locator('#review-list [data-review-forget]').first().click();
  await page.locator('#review-list .undo-bar').waitFor({ state: 'visible', timeout: 5000 });
  await page.locator('#review-list .review-card').first().waitFor({ state: 'detached', timeout: 5000 }).catch(() => {});
  const cardsAfter = await page.locator('#review-list .review-card').count();
  if (!(cardsAfter < cardsBefore)) fail('e-review-forget', `cards ${cardsBefore} → ${cardsAfter}`);
  const forgot = await post(`/api/memory/${firstId}/forget`, {}); // idempotent; card already forgotten
  if (forgot.status === 404) fail('e-review-forget', `note ${firstId} vanished`);
  pass('e-review-forget', `counter "${reviewCountText}", listed ${cardsBefore}, forget removed card #${firstId} (${cardsBefore}→${cardsAfter})`);

  // ── Empty-state DOM excerpts via filters (fixture has data) ────────────
  await view('history');
  await page.locator('#hist-q').fill('zzz-no-such-session-zzz');
  await page.locator('#hist-apply').click();
  await page.locator('#hist-tbody .empty').waitFor({ state: 'visible' });
  const histEmpty = await page.locator('#hist-tbody .empty').innerHTML();
  if ((histEmpty.match(/<button/g) || []).length !== 1) fail('h-empty-filters', `history empty actions != 1`);
  await writeFile(path.join(out, 'empty-history.html'), histEmpty);
  await page.screenshot({ path: path.join(out, 'empty-history.png'), fullPage: true });
  await page.locator('#hist-reset').click();

  await view('find');
  await page.locator('#find-q').fill('zzz-no-match-zzz');
  await page.locator('#find-mode').selectOption('lexical');
  await page.locator('#find-go').click();
  await page.locator('#find-results .empty').waitFor({ state: 'visible' });
  const findEmpty = await page.locator('#find-results .empty').innerHTML();
  if ((findEmpty.match(/<button/g) || []).length !== 1) fail('h-empty-filters', `find empty actions != 1`);
  await writeFile(path.join(out, 'empty-find.html'), findEmpty);
  await page.screenshot({ path: path.join(out, 'empty-find.png'), fullPage: true });

  await view('browse');
  await page.locator('#browse-q').fill('zzz-no-match-zzz');
  await page.locator('#browse-apply').click();
  await page.locator('#browse-list .empty').waitFor({ state: 'visible' });
  const browseEmpty = await page.locator('#browse-list .empty').innerHTML();
  if ((browseEmpty.match(/<button/g) || []).length !== 1) fail('h-empty-filters', `browse empty actions != 1`);
  await writeFile(path.join(out, 'empty-browse.html'), browseEmpty);
  await page.screenshot({ path: path.join(out, 'empty-browse.png'), fullPage: true });

  await view('memory');
  await page.locator('#memory-tab-notes').click();
  await page.locator('#memory-q').fill('zzz-no-match-zzz');
  await page.locator('#memory-go').click();
  await page.locator('#memory-list .empty').waitFor({ state: 'visible' });
  const memEmpty = await page.locator('#memory-list .empty').innerHTML();
  if ((memEmpty.match(/<button/g) || []).length !== 1) fail('h-empty-filters', `memory empty actions != 1`);
  await writeFile(path.join(out, 'empty-memory.html'), memEmpty);
  await page.screenshot({ path: path.join(out, 'empty-memory.png'), fullPage: true });
  pass('h-empty-filters', 'History/Find/Browse/Memory empty cards each expose exactly one action');

  // ── Responsive evidence (criterion 6) ──────────────────────────────────
  await page.setViewportSize({ width: 1280, height: 720 });
  await view('home');
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(out, 'home-1280x720.png'), fullPage: true });
  await view('operate');
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(out, 'operate-1280x720.png'), fullPage: true });
  for (const sel of ['#btn-hide-empty', '#btn-browse', '#theme-select', '#open-palette']) {
    const box = await page.locator(sel).boundingBox();
    if (!box) fail('f-responsive', `${sel} not rendered`);
    if (box.x < 0 || box.y < 0 || box.x + box.width > 1281 || box.y + box.height > 721) {
      fail('f-responsive', `${sel} clipped: ${JSON.stringify(box)}`);
    }
  }
  await page.setViewportSize({ width: 1920, height: 1080 });
  await view('home');
  await page.screenshot({ path: path.join(out, 'home-1920x1080.png'), fullPage: true });
  pass('f-responsive', 'core controls visible at 1280x720 and 1920x1080');

  // ── (j) Language toggle: DE labels, persist across reload, back to EN ──
  {
    const histBtn = page.locator('#nav [data-view="history"]');
    const enLabel = (await histBtn.textContent()) || '';
    if (!enLabel.includes('History')) fail('j-lang-toggle', `expected English "History" before switch, got "${enLabel.trim()}"`);
    await page.locator('#lang-select').selectOption('de');
    await page.waitForTimeout(200);
    const deLabel = (await histBtn.textContent()) || '';
    if (!deLabel.includes('Verlauf')) fail('j-lang-toggle', `expected German "Verlauf" after switch, got "${deLabel.trim()}"`);
    const storedLang = await page.evaluate(() => localStorage.getItem('th-lang'));
    if (storedLang !== 'de') fail('j-lang-toggle', `localStorage th-lang=${storedLang}, expected de`);
    await page.reload();
    await page.waitForLoadState('networkidle');
    const afterReload = await page.evaluate(() => localStorage.getItem('th-lang'));
    if (afterReload !== 'de') fail('j-lang-toggle', `th-lang lost after reload: ${afterReload}`);
    const deLabel2 = (await page.locator('#nav [data-view="history"]').textContent()) || '';
    if (!deLabel2.includes('Verlauf')) fail('j-lang-toggle', `DE labels lost after reload: "${deLabel2.trim()}"`);
    await page.locator('#lang-select').selectOption('en');
    await page.waitForTimeout(200);
    const enLabel2 = (await histBtn.textContent()) || '';
    if (!enLabel2.includes('History')) fail('j-lang-toggle', `expected English "History" after switch back, got "${enLabel2.trim()}"`);
    pass('j-lang-toggle', `History↔Verlauf live, th-lang persists across reload, back to EN ok`);
  }

  // ── Page error gate (criterion 5) ──────────────────────────────────────
  if (pageErrors.length) fail('g-no-pageerror', pageErrors.join(' | '));
  pass('g-no-pageerror', '0 pageerror events (fixture phase)');
} finally {
  await browser.close();
}

// ─── Phase 2: empty throwaway DB (migrate only) ───────────────────────────
// Creates throughline_ux_empty in the demo postgres, migrates it, serves the
// same UI on 127.0.0.1:8797, and captures empty-state DOM/screenshots.
const EMPTY_DB = 'throughline_ux_empty';
const EMPTY_URL = process.env.THROUGHLINE_EMPTY_URL || 'http://127.0.0.1:8797';
const compose = ['docker', 'compose', '-f', 'docs/demo/rust/compose.yml'];
const composeRun = (args, opts = {}) =>
  execFileSync(compose[0], [...compose.slice(1), ...args], {
    cwd: root,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    ...opts,
  });

let emptyProc = null;
try {
  console.log('Phase 2: empty DB at', EMPTY_URL);
  // Terminate leftover sessions (from a prior run) before DROP.
  try {
    composeRun(['exec', '-T', 'postgres', 'psql', '-U', 'demo', '-d', 'postgres', '-c',
      `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${EMPTY_DB}' AND pid <> pg_backend_pid();`]);
  } catch {}
  composeRun(['exec', '-T', 'postgres', 'psql', '-U', 'demo', '-d', 'postgres', '-c',
    `DROP DATABASE IF EXISTS ${EMPTY_DB};`]);
  composeRun(['exec', '-T', 'postgres', 'psql', '-U', 'demo', '-d', 'postgres', '-c',
    `CREATE DATABASE ${EMPTY_DB};`]);
  composeRun(['run', '--rm', '-T', '-e', `PGDATABASE=${EMPTY_DB}`, 'migrate', 'throughline', 'migrate']);
  // Serve the empty DB on 8797 using the same image/service definition.
  emptyProc = spawn(
    compose[0],
    [...compose.slice(1), 'run', '--rm', '-T', '--name', 'throughline-ux-empty-web',
      '-e', `PGDATABASE=${EMPTY_DB}`,
      '-p', '127.0.0.1:8797:8787',
      'web', 'throughline', 'serve', '--host', '0.0.0.0', '--port', '8787'],
    { cwd: root, stdio: 'ignore', detached: true }
  );
  // Wait for health (120 s; cold docker can take a while to pull/start)
  let healthy = false;
  for (let i = 0; i < 240; i++) {
    try {
      const r = await fetch(EMPTY_URL + '/api/health');
      if (r.ok) { healthy = true; break; }
    } catch {}
    await new Promise(r => setTimeout(r, 500));
  }
  if (!healthy) throw Error('empty instance did not become healthy on ' + EMPTY_URL);

  const browser2 = await chromium.launch({ channel: process.env.THROUGHLINE_SCREENSHOT_BROWSER || undefined });
  const ctx2 = await browser2.newContext({
    viewport: { width: 1440, height: 1000 },
    reducedMotion: 'reduce',
    locale: 'en-GB',
    timezoneId: 'UTC',
  });
  const p2 = await ctx2.newPage();
  p2.setDefaultTimeout(20000);
  const emptyErrors = [];
  p2.on('pageerror', e => emptyErrors.push(e.message));
  await p2.goto(EMPTY_URL);
  await p2.waitForLoadState('networkidle');
  // Home: three-step card
  await p2.locator('#home-quickstart').waitFor({ state: 'visible' });
  const quick = await p2.locator('#home-quickstart-body').innerHTML();
  if (!/three steps/i.test(quick) && !/Three steps/i.test(quick)) fail('i-empty-db', 'home quickstart missing three-step text');
  const quickActions = await p2.locator('#home-quickstart-body .empty-action button').count();
  if (quickActions !== 1) fail('i-empty-db', `home quickstart actions=${quickActions}`);
  await writeFile(path.join(out, 'empty-db-home.html'), quick);
  await p2.screenshot({ path: path.join(out, 'empty-db-home.png'), fullPage: true });

  const view2 = async name => {
    await p2.locator(`#nav [data-view="${name}"]`).click();
    await p2.locator(`#view-${name}`).waitFor({ state: 'visible' });
    await p2.waitForLoadState('networkidle');
  };
  const emptyStates = {
    history: '#hist-tbody .empty, #hist-list .empty',
    find: '#find-results .empty',
    browse: '#browse-list .empty',
    memory: '#memory-list .empty',
  };
  for (const [name, sel] of Object.entries(emptyStates)) {
    await view2(name);
    await p2.locator(sel).first().waitFor({ state: 'visible', timeout: 10000 });
    const html = await p2.locator(sel).first().innerHTML();
    const actions = (html.match(/<button/g) || []).length;
    if (actions !== 1) fail('i-empty-db', `${name} empty actions=${actions}`);
    await writeFile(path.join(out, `empty-db-${name}.html`), html);
    await p2.screenshot({ path: path.join(out, `empty-db-${name}.png`), fullPage: true });
  }
  if (emptyErrors.length) fail('i-empty-db', 'pageerror: ' + emptyErrors.join(' | '));
  pass('i-empty-db', 'Home 3-step card + History/Find/Browse/Memory empty states with exactly one action');
  await browser2.close();
} finally {
  if (emptyProc && emptyProc.pid) {
    try { process.kill(-emptyProc.pid, 'SIGTERM'); } catch {
      try { emptyProc.kill('SIGTERM'); } catch {}
    }
  }
  // Best-effort cleanup of the throwaway database (not the fixture).
  try {
    composeRun(['exec', '-T', 'postgres', 'psql', '-U', 'demo', '-d', 'postgres', '-c',
      `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${EMPTY_DB}' AND pid <> pg_backend_pid();`]);
    composeRun(['exec', '-T', 'postgres', 'psql', '-U', 'demo', '-d', 'postgres', '-c',
      `DROP DATABASE IF EXISTS ${EMPTY_DB};`]);
  } catch {}
  // Always remove the fixed-name phase-2 serve container, even on error.
  try {
    execFileSync('docker', ['rm', '-f', 'throughline-ux-empty-web'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  } catch {}
}

const summary = {
  fixture: 'docs/demo/rust/seed.sql',
  capturedAt: new Date().toISOString(),
  uiSha256: createHash('sha256').update(normalize(source)).digest('hex'),
  fictionalData: true,
  mockedResponses: false,
  checks: results,
  contrast: contrastReport,
  pageErrors,
  screenshots: [
    'home-light.png', 'history-light.png',
    'empty-history.png', 'empty-find.png', 'empty-browse.png', 'empty-memory.png',
    'empty-db-home.png', 'empty-db-history.png', 'empty-db-find.png', 'empty-db-browse.png', 'empty-db-memory.png',
    'home-1280x720.png', 'operate-1280x720.png', 'home-1920x1080.png',
  ],
};
await writeFile(path.join(out, 'ux-report.json'), JSON.stringify(summary, null, 2) + '\n');

const failed = Object.entries(results).filter(([, v]) => !v.ok);
if (failed.length) {
  console.error('FAILED:', failed.map(([k, v]) => `${k}: ${v.detail}`).join('; '));
  process.exit(1);
}
console.log(`All ${Object.keys(results).length} UX checks green. Report: docs/media/rust/ux/ux-report.json`);
