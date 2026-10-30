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
const del = async route => {
  const r = await fetch(base + route, { method: 'DELETE' });
  return r.status;
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
    'text/bg': ['#ebebeb', '#0b0b0a'],
    'text-2/bg': ['#cacaca', '#0b0b0a'],
    'text-3/bg': ['#a4a4a4', '#0b0b0a'],
    'text/bg-elevated': ['#ebebeb', '#121210'],
    'text-2/bg-elevated': ['#cacaca', '#121210'],
  },
  light: {
    'text/bg': ['#0d0d0d', '#fefdfb'],
    'text-2/bg': ['#292929', '#fefdfb'],
    'text-3/bg': ['#525252', '#fefdfb'],
    'text/bg-elevated': ['#0d0d0d', '#ffffff'],
    'text-2/bg-elevated': ['#292929', '#ffffff'],
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

// ─── Demo-stack helpers (fixture cleanup + Phase 2) ───────────────────────
const compose = ['docker', 'compose', '-f', 'docs/demo/rust/compose.yml'];
const composeRun = (args, opts = {}) =>
  execFileSync(compose[0], [...compose.slice(1), ...args], {
    cwd: root,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    ...opts,
  });
/** Delete fixture-test rows so /api/status memory_chunks returns to baseline. */
const deleteMemoryIds = (ids) => {
  if (!ids.length) return;
  const list = ids.join(',');
  composeRun(['exec', '-T', 'postgres', 'psql', '-U', 'demo', '-d', 'throughline_readme_demo', '-c',
    `DELETE FROM memory_chunks WHERE id IN (${list});`]);
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
  if (!pinContent.includes('Source: session #')) fail('d-pin-note', 'pin content missing source line');
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
    if (!enLabel.includes('Sessions')) fail('j-lang-toggle', `expected English "Sessions" before switch, got "${enLabel.trim()}"`);
    await page.locator('#lang-select').selectOption('de');
    await page.waitForTimeout(200);
    const deLabel = (await histBtn.textContent()) || '';
    if (!deLabel.includes('Sitzungen')) fail('j-lang-toggle', `expected German "Sitzungen" after switch, got "${deLabel.trim()}"`);
    const storedLang = await page.evaluate(() => localStorage.getItem('th-lang'));
    if (storedLang !== 'de') fail('j-lang-toggle', `localStorage th-lang=${storedLang}, expected de`);
    await page.reload();
    await page.waitForLoadState('networkidle');
    const afterReload = await page.evaluate(() => localStorage.getItem('th-lang'));
    if (afterReload !== 'de') fail('j-lang-toggle', `th-lang lost after reload: ${afterReload}`);
    const deLabel2 = (await page.locator('#nav [data-view="history"]').textContent()) || '';
    if (!deLabel2.includes('Sitzungen')) fail('j-lang-toggle', `DE labels lost after reload: "${deLabel2.trim()}"`);
    await page.locator('#lang-select').selectOption('en');
    await page.waitForTimeout(200);
    const enLabel2 = (await histBtn.textContent()) || '';
    if (!enLabel2.includes('Sessions')) fail('j-lang-toggle', `expected English "Sessions" after switch back, got "${enLabel2.trim()}"`);
    pass('j-lang-toggle', `Sessions↔Sitzungen live, th-lang persists across reload, back to EN ok`);
  }

  // ── (k) Supersede: UI replaces a note, old leaves active list ───────────
  // Create via API, supersede via the Memory-card action, assert the chain in
  // the detail line, then delete both test rows so memory_chunks is unchanged.
  {
    const marker = 'UX-E2E k-supersede marker ' + Date.now();
    const beforeStatus = await json('/api/status');
    const beforeChunks = beforeStatus.memory_chunks;
    const created = await post('/api/memory', {
      content: marker + ' — first version of the fact.',
      category: 'insight',
      project_name: 'Atlas-demo',
    });
    if (created.status !== 200 || !created.body?.id) fail('k-supersede', `create ${created.status} ${created.text}`);
    const oldId = created.body.id;
    const testIds = [oldId];
    try {
      await view('memory');
      await page.locator('#memory-tab-notes').click();
      await page.locator('#memory-q').fill(marker);
      await page.locator('#memory-go').click();
      await page.locator('#memory-list [data-mem-id="' + oldId + '"]').waitFor({ state: 'visible', timeout: 15000 });
      // Active card exposes the Supersede action.
      await page.locator('[data-mem-supersede="' + oldId + '"]').click();
      await page.locator('#pin-dialog.open').waitFor({ state: 'visible' });
      const prefill = await page.locator('#pin-content').inputValue();
      if (!prefill.includes(marker)) fail('k-supersede', `dialog prefill missing old text: ${prefill.slice(0, 80)}`);
      const newBody = marker + ' — second version of the fact (replacement).';
      await page.locator('#pin-content').fill(newBody);
      await page.locator('#pin-save').click();
      await page.locator('#toast').filter({ hasText: 'Superseded note' }).waitFor({ timeout: 10000 });

      // Old note is gone from the active list.
      await page.locator('#memory-list [data-mem-id="' + oldId + '"]').waitFor({ state: 'detached', timeout: 10000 });
      const activeIds = await page.locator('#memory-list [data-mem-id]').evaluateAll(
        els => els.map(e => e.getAttribute('data-mem-id')));
      if (activeIds.includes(String(oldId))) fail('k-supersede', `old #${oldId} still in active list`);

      // API confirms the chain and hands back the successor id.
      const oldRec = await json('/api/memory/' + oldId);
      if (oldRec.status !== 'superseded' || !oldRec.superseded_by) {
        fail('k-supersede', `old record not superseded: ${JSON.stringify(oldRec).slice(0, 200)}`);
      }
      const newId = oldRec.superseded_by;
      testIds.push(newId);

      // New note carries "Replaced #oldId" in its detail chain line.
      await page.locator('#memory-q').fill(marker);
      await page.locator('#memory-go').click();
      await page.locator('#memory-list [data-mem-id="' + newId + '"]').waitFor({ state: 'visible', timeout: 10000 });
      const chainText = await page.locator('#memory-list [data-mem-id="' + newId + '"] [data-mem-chain="supersedes"]').textContent();
      if (!chainText || !chainText.includes('#' + oldId)) {
        fail('k-supersede', `new #${newId} chain line missing old ref, got "${chainText}"`);
      }

      // History filter shows the superseded note with "Replaced by #newId".
      await page.locator('#memory-status').selectOption('history');
      await page.locator('#memory-list [data-mem-id="' + oldId + '"]').waitFor({ state: 'visible', timeout: 10000 });
      const replacedBy = await page.locator('#memory-list [data-mem-id="' + oldId + '"] [data-mem-chain="superseded"]').textContent();
      if (!replacedBy || !replacedBy.includes('#' + newId)) {
        fail('k-supersede', `old #${oldId} history line missing new ref, got "${replacedBy}"`);
      }
      // Back to active so the next checks start from the default filter.
      await page.locator('#memory-status').selectOption('active');
      await page.locator('#memory-q').fill('');
      await page.locator('#memory-go').click();

      pass('k-supersede',
        `created #${oldId}, UI supersede → #${newId}; old left active list, new shows "Replaced #${oldId}", history shows "Replaced by #${newId}"`);
    } finally {
      // Always restore the fixture: drop both test rows.
      try {
        deleteMemoryIds(testIds);
      } catch (e) {
        console.error('k-supersede cleanup failed:', e.message);
      }
    }
    const afterStatus = await json('/api/status');
    if (afterStatus.memory_chunks !== beforeChunks) {
      fail('k-supersede', `memory_chunks ${beforeChunks} → ${afterStatus.memory_chunks} (cleanup incomplete)`);
    }
    pass('k-supersede-count', `memory_chunks unchanged at ${afterStatus.memory_chunks}`);
  }

  // ── Page error gate (criterion 5) ──────────────────────────────────────
  if (pageErrors.length) fail('g-no-pageerror', pageErrors.join(' | '));
  pass('g-no-pageerror', '0 pageerror events (fixture phase)');

  // ── New checks: timeline range/calendar + projects (criteria 4-12) ─────
  const demoSql = (sql) => composeRun(['exec', '-T', 'postgres', 'psql', '-U', 'demo', '-d', 'throughline_readme_demo', '-c', sql]);

  // ── (l) Calendar month change fetches the month (since/until) and caches it
  {
    await view('timeline');
    await page.locator('#timeline-mode-cal').click();
    await page.locator('#timeline-calendar-panel').waitFor({ state: 'visible' });
    // January 2026 lies outside the 90-day window at fixture time.
    const monthsBack = await page.evaluate(() => {
      const now = new Date();
      return (now.getFullYear() - 2026) * 12 + now.getMonth();
    });
    if (monthsBack < 3) fail('l-calendar-month-fetch', `test clock too close to Jan 2026 (${monthsBack} months back)`);
    const janReq = page.waitForRequest((r) =>
      r.url().includes('/api/timeline') && r.url().includes('since=2026-01-01') && r.url().includes('until=2026-01-31'),
      { timeout: 20000 });
    for (let i = 0; i < monthsBack; i++) await page.locator('#cal-prev').click();
    const req = await janReq;
    const url = new URL(req.url());
    if (url.searchParams.get('since') !== '2026-01-01') fail('l-calendar-month-fetch', `since=${url.searchParams.get('since')}`);
    if (url.searchParams.get('until') !== '2026-01-31') fail('l-calendar-month-fetch', `until=${url.searchParams.get('until')}`);
    await page.screenshot({ path: path.join(out, 'timeline-calendar-jan2026.png'), fullPage: true });
    // Cached month: leaving and returning does not fetch it again.
    let janFetches = 0;
    const countJan = (r) => { if (r.url().includes('since=2026-01-01')) janFetches++; };
    page.on('request', countJan);
    await page.locator('#cal-next').click();
    await page.locator('#cal-prev').click();
    await page.waitForTimeout(800);
    page.off('request', countJan);
    if (janFetches !== 0) fail('l-calendar-month-fetch', `cached January refetched ${janFetches}×`);
    await page.locator('#timeline-mode-chart').click(); // back to list mode
    pass('l-calendar-month-fetch', `month change → GET since=2026-01-01&until=2026-01-31; revisit used the month cache (0 refetches)`);
  }

  // ── (m) Timeline rows show localized dates; data-day stays ISO ─────────
  {
    await page.locator('#lang-select').selectOption('de');
    await page.waitForTimeout(200);
    await view('timeline');
    await page.locator('#timeline-range').selectOption('all');
    await page.waitForLoadState('networkidle');
    await page.locator('#timeline-list .row[data-day]').first().waitFor({ state: 'visible', timeout: 15000 });
    const firstRow = page.locator('#timeline-list .row[data-day]').first();
    const dayIso = await firstRow.getAttribute('data-day');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dayIso)) fail('m-timeline-dates', `data-day not ISO: "${dayIso}"`);
    const rowText = ((await firstRow.textContent()) || '').trim();
    const monthDe = await page.evaluate((iso) =>
      new Date(iso + 'T12:00:00').toLocaleDateString('de-DE', { month: 'long' }), dayIso);
    if (!rowText.includes(monthDe)) fail('m-timeline-dates', `row "${rowText.slice(0, 80)}" missing German month "${monthDe}"`);
    if (rowText.includes(dayIso)) fail('m-timeline-dates', 'row text still shows the raw ISO date');
    await page.locator('#timeline-range').selectOption('90');
    await page.locator('#lang-select').selectOption('en');
    await page.waitForTimeout(200);
    // Drilldown hands the ISO date to the History filter.
    await firstRow.click();
    await page.locator('#view-history').waitFor({ state: 'visible' });
    const histFrom = await page.locator('#hist-from').inputValue();
    const histTo = await page.locator('#hist-to').inputValue();
    if (histFrom !== dayIso || histTo !== dayIso) {
      fail('m-timeline-dates', `drilldown passed ${histFrom}/${histTo} instead of ${dayIso}`);
    }
    pass('m-timeline-dates', `data-day=${dayIso} kept ISO; row shows localized "${monthDe}" instead of ${dayIso}; drilldown → History ${histFrom}/${histTo}`);
  }

  // ── (n) Range select 30/90/365/All, default 90, 365 → since=today-365 ──
  {
    await page.reload();
    await page.waitForLoadState('networkidle');
    await view('timeline');
    const rangeSel = page.locator('#timeline-range');
    const values = await rangeSel.locator('option').evaluateAll((opts) => opts.map((o) => o.value));
    if (values.join(',') !== '30,90,365,all') fail('n-timeline-range', `options ${values.join(',')}`);
    const def = await rangeSel.inputValue();
    if (def !== '90') fail('n-timeline-range', `default range "${def}"`);
    const expectedSince = await page.evaluate(() => {
      const d = new Date();
      d.setDate(d.getDate() - 365);
      return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    });
    const reqP = page.waitForRequest((r) =>
      r.url().includes('/api/timeline') && r.url().includes('since=' + expectedSince), { timeout: 15000 });
    await rangeSel.selectOption('365');
    const req = await reqP;
    const sinceGot = new URL(req.url()).searchParams.get('since');
    if (sinceGot !== expectedSince) fail('n-timeline-range', `since=${sinceGot} want ${expectedSince}`);
    pass('n-timeline-range', `options 30/90/365/all with default 90; select 365 → GET since=${expectedSince}`);
  }

  // ── (o) Provider dropdown = /api/status tools, even outside the window ──
  {
    const marker = 'zz-outside-tool';
    demoSql(`INSERT INTO conversations (session_id, project_path, started_at, message_count, source_tool) VALUES (gen_random_uuid(), '/fictional/zz-outside-tool', TIMESTAMPTZ '2020-01-01 12:00:00+00', 1, '${marker}')`);
    try {
      const statusP = page.waitForResponse((r) => r.url().includes('/api/status'), { timeout: 15000 }).catch(() => null);
      await page.locator('#timeline-range').selectOption('30'); // triggers a fresh loadTimeline
      const statusRes = await statusP;
      if (!statusRes) fail('o-timeline-providers', 'no /api/status request after the range change — loadTimeline did not run');
      const pageTools = ((await statusRes.json()).tools || []).map((t) => t.source_tool || 'unknown').sort();
      await page.waitForLoadState('networkidle');
      await page.locator('#timeline-list .row[data-day]').first().waitFor({ state: 'visible', timeout: 15000 });
      const status = await json('/api/status');
      const apiTools = (status.tools || []).map((t) => t.source_tool || 'unknown').sort();
      const opts = await page.locator('#timeline-tool option').evaluateAll((o) => o.map((x) => x.value).filter((v) => v !== ''));
      const optsSorted = [...opts].sort();
      if (JSON.stringify(optsSorted) !== JSON.stringify(apiTools)) {
        fail('o-timeline-providers', `dropdown ${optsSorted} != status.tools ${apiTools} | page-status-tools ${pageTools} | range=${await page.locator('#timeline-range').inputValue()}`);
      }
      if (!opts.includes(marker)) fail('o-timeline-providers', `tool "${marker}" missing from dropdown`);
      const listText = (await page.locator('#timeline-list').textContent()) || '';
      if (listText.includes(marker)) fail('o-timeline-providers', 'out-of-window tool leaked into the 30-day list');
      pass('o-timeline-providers', `dropdown lists all ${apiTools.length} status tools incl. "${marker}" although its conversations are outside the loaded window`);
    } finally {
      demoSql(`DELETE FROM conversations WHERE source_tool = '${marker}'`);
    }
  }

  // ── (p) Projects: count line, empty hidden by default, Show empty toggle ─
  {
    await page.locator('#lang-select').selectOption('de');
    await page.waitForTimeout(150);
    await view('projects');
    await page.locator('#proj-count').waitFor({ state: 'visible' });
    await page.locator('#projects-grid .card').first().waitFor({ state: 'visible' });
    let countText = ((await page.locator('#proj-count').textContent()) || '').trim();
    if (!countText.includes('3 von 3')) fail('p-projects-empty-toggle', `count "${countText}" lacks "3 von 3"`);
    const fixtureCards = await page.locator('#projects-grid .card').count();
    if (fixtureCards !== 3) fail('p-projects-empty-toggle', `expected 3 fixture cards, got ${fixtureCards}`);
    const projApi = await json('/api/projects');
    for (const pr of projApi.projects) {
      for (const f of ['name', 'description', 'conversations', 'status']) {
        if (!(f in pr)) fail('p-projects-empty-toggle', `existing field ${f} missing on ${pr.name}`);
      }
      if (!('last_started_at' in pr)) fail('p-projects-empty-toggle', `last_started_at missing on ${pr.name}`);
      if (pr.conversations > 0 && typeof pr.last_started_at !== 'string') {
        fail('p-projects-empty-toggle', `last_started_at not an ISO string on ${pr.name}: ${JSON.stringify(pr.last_started_at)}`);
      }
    }
    await page.screenshot({ path: path.join(out, 'projects-count-de.png'), fullPage: true });
    const name = 'zz-empty-test-proj';
    demoSql(`INSERT INTO projects (name, description) VALUES ('${name}', NULL)`);
    try {
      await view('home');
      await view('projects');
      await page.locator('#projects-grid .card').first().waitFor({ state: 'visible' });
      const hiddenNames = await page.locator('#projects-grid .card').evaluateAll((els) => els.map((e) => e.getAttribute('data-project')));
      if (hiddenNames.includes(name) || hiddenNames.length !== 3) {
        fail('p-projects-empty-toggle', `default must hide empty projects, got ${hiddenNames}`);
      }
      countText = ((await page.locator('#proj-count').textContent()) || '').trim();
      if (!countText.includes('3 von 4')) fail('p-projects-empty-toggle', `count "${countText}" lacks "3 von 4"`);
      const api2 = await json('/api/projects');
      const emptyPr = api2.projects.find((pr) => pr.name === name);
      if (!emptyPr || emptyPr.conversations !== 0 || emptyPr.last_started_at !== null) {
        fail('p-projects-empty-toggle', `empty project contract: ${JSON.stringify(emptyPr)}`);
      }
      await page.locator('#proj-show-empty').click();
      const shownNames = await page.locator('#projects-grid .card').evaluateAll((els) => els.map((e) => e.getAttribute('data-project')));
      if (!shownNames.includes(name)) fail('p-projects-empty-toggle', 'toggle did not reveal the empty project');
      countText = ((await page.locator('#proj-count').textContent()) || '').trim();
      if (!countText.includes('4 von 4')) fail('p-projects-empty-toggle', `count "${countText}" lacks "4 von 4"`);
      await page.locator('#proj-show-empty').click(); // restore default
      pass('p-projects-empty-toggle', `count "3 von 3"; empty test project hidden by default ("3 von 4"), toggle shows it ("4 von 4")`);
    } finally {
      demoSql(`DELETE FROM projects WHERE name = '${name}'`);
    }
    await page.locator('#lang-select').selectOption('en');
    await page.waitForTimeout(150);
  }

  // ── (q) Projects sort: default activity desc; Name is case-insensitive ──
  {
    await view('home');
    await view('projects');
    await page.locator('#projects-grid .card').first().waitFor({ state: 'visible' });
    const defSort = await page.locator('#proj-sort').inputValue();
    if (defSort !== 'activity') fail('q-projects-sort', `default sort "${defSort}"`);
    const api = await json('/api/projects');
    const expected = api.projects
      .filter((pr) => pr.conversations > 0)
      .sort((a, b) => {
        const av = a.last_started_at, bv = b.last_started_at;
        if (av && bv) return av < bv ? 1 : av > bv ? -1 : 0;
        if (av && !bv) return -1;
        if (!av && bv) return 1;
        return 0;
      })
      .map((pr) => pr.name);
    const domNames = await page.locator('#projects-grid .card').evaluateAll((els) => els.map((e) => e.getAttribute('data-project')));
    if (JSON.stringify(domNames) !== JSON.stringify(expected)) {
      fail('q-projects-sort', `activity-desc DOM ${domNames} != API order ${expected}`);
    }
    const n1 = 'coachingschool-demo', n2 = 'Coachingschool-demo';
    demoSql(`INSERT INTO projects (name, description) VALUES ('${n1}', NULL), ('${n2}', NULL)`);
    try {
      await view('home');
      await view('projects');
      await page.locator('#proj-show-empty').click();
      await page.locator('#proj-sort').selectOption('name');
      const names = await page.locator('#projects-grid .card').evaluateAll((els) => els.map((e) => e.getAttribute('data-project')));
      const i1 = names.indexOf(n1), i2 = names.indexOf(n2);
      if (i1 < 0 || i2 < 0) fail('q-projects-sort', `twins missing in ${names.join(' | ')}`);
      if (Math.abs(i1 - i2) !== 1) fail('q-projects-sort', `twins not adjacent in ${names.join(' | ')}`);
      const iAtlas = names.indexOf('Atlas-demo');
      if (!(iAtlas < i1 && iAtlas < i2)) fail('q-projects-sort', `case-insensitive order broken: ${names.join(' | ')}`);
      await page.locator('#proj-sort').selectOption('activity');
      await page.locator('#proj-show-empty').click(); // restore default
      pass('q-projects-sort', `default activity desc (${domNames.join(' → ')}); name sort keeps "coachingschool-demo" adjacent to "Coachingschool-demo"`);
    } finally {
      demoSql(`DELETE FROM projects WHERE name IN ('${n1}', '${n2}')`);
    }
  }

  // ── (r) Project search filters live on the name (case-insensitive) ─────
  {
    await view('home');
    await view('projects');
    await page.locator('#projects-grid .card').first().waitFor({ state: 'visible' });
    await page.locator('#proj-q').fill('atlas');
    const names = await page.locator('#projects-grid .card').evaluateAll((els) => els.map((e) => e.getAttribute('data-project')));
    if (names.length !== 1 || names[0] !== 'Atlas-demo') fail('r-projects-search', `search "atlas" → ${names.join(' | ')}`);
    await page.locator('#proj-q').fill('');
    pass('r-projects-search', 'input "atlas" shows exactly Atlas-demo (live, case-insensitive)');
  }

  // ── (s) Long names clamp to 2 lines with title; no description placeholder
  {
    const longName = ('zz error sentence ' + 'more failing words '.repeat(8)).slice(0, 120);
    if (longName.length !== 120) fail('s-projects-card', `name length ${longName.length} != 120`);
    demoSql(`INSERT INTO projects (name, description) VALUES ('${longName}', NULL)`);
    try {
      await view('home');
      await view('projects');
      await page.locator('#proj-show-empty').click();
      const card = page.locator('#projects-grid .card[data-project="' + longName + '"]');
      await card.waitFor({ state: 'visible', timeout: 10000 });
      const h3 = card.locator('h3.proj-name');
      const title = await h3.getAttribute('title');
      if (title !== longName) fail('s-projects-card', `title "${title}" != full 120-char name`);
      const pCount = await card.locator('p').count();
      if (pCount !== 0) fail('s-projects-card', 'empty description rendered a <p> paragraph');
      const cardText = (await card.textContent()) || '';
      if (/No description|Keine Beschreibung/.test(cardText)) fail('s-projects-card', 'placeholder text in card');
      const styles = await h3.evaluate((el) => {
        const cs = getComputedStyle(el);
        return { clamp: cs.webkitLineClamp, overflow: cs.overflow, clipped: el.scrollHeight > el.clientHeight + 1 };
      });
      if (styles.clamp !== '2' || styles.overflow !== 'hidden') fail('s-projects-card', `line-clamp=${styles.clamp} overflow=${styles.overflow}`);
      if (!styles.clipped) fail('s-projects-card', 'long name is not actually clamped');
      const h3Box = await h3.boundingBox();
      const cardBox = await card.boundingBox();
      if (h3Box.width > cardBox.width + 1 || h3Box.x < cardBox.x - 1) {
        fail('s-projects-card', `name box escapes card: ${JSON.stringify(h3Box)} vs ${JSON.stringify(cardBox)}`);
      }
      await page.locator('#proj-show-empty').click(); // restore default
      pass('s-projects-card', '120-char name: clamped to 2 lines, title holds the full name, no placeholder, stays inside the card');
    } finally {
      demoSql(`DELETE FROM projects WHERE name = '${longName}'`);
    }
  }

  // ── (t) Every new UI string translates on the de/en switch ─────────────
  {
    const readUi = () => page.evaluate(() => {
      const fieldLabel = (sel) => {
        const el = document.querySelector(sel);
        const lab = el && el.parentElement && el.parentElement.querySelector('.field-label');
        return lab ? lab.textContent.trim() : '';
      };
      const allOpt = Array.from(document.querySelectorAll('#timeline-range option')).find((o) => o.value === 'all');
      return {
        rangeLabel: fieldLabel('#timeline-range'),
        rangeAll: allOpt ? allOpt.textContent.trim() : '',
        showEmpty: (document.querySelector('#proj-show-empty') || {}).textContent || '',
        count: (document.querySelector('#proj-count') || {}).textContent || '',
        placeholder: (document.querySelector('#proj-q') || {}).placeholder || '',
        sortLabels: Array.from(document.querySelectorAll('#proj-sort option')).map((o) => o.textContent.trim()),
      };
    });
    await view('projects');
    await page.locator('#lang-select').selectOption('de');
    await page.locator('#proj-count').filter({ hasText: 'von' }).waitFor({ timeout: 5000 });
    const de = await readUi();
    if (de.rangeLabel !== 'Zeitraum') fail('t-ui-i18n', `de range label "${de.rangeLabel}"`);
    if (de.rangeAll !== 'Alles') fail('t-ui-i18n', `de range all "${de.rangeAll}"`);
    if (de.showEmpty.trim() !== 'Leere anzeigen') fail('t-ui-i18n', `de show-empty "${de.showEmpty}"`);
    if (!de.count.includes('von')) fail('t-ui-i18n', `de count "${de.count}"`);
    if (de.placeholder !== 'Nach Projektname filtern…') fail('t-ui-i18n', `de placeholder "${de.placeholder}"`);
    if (de.sortLabels.join('|') !== 'Neu zu Alt|Alt zu Neu|Name|Sitzungen') fail('t-ui-i18n', `de sort labels ${de.sortLabels}`);
    await page.locator('#lang-select').selectOption('en');
    await page.locator('#proj-count').filter({ hasText: 'of' }).waitFor({ timeout: 5000 });
    const en = await readUi();
    if (en.rangeLabel !== 'Range') fail('t-ui-i18n', `en range label "${en.rangeLabel}"`);
    if (en.rangeAll !== 'All') fail('t-ui-i18n', `en range all "${en.rangeAll}"`);
    if (en.showEmpty.trim() !== 'Show empty') fail('t-ui-i18n', `en show-empty "${en.showEmpty}"`);
    if (!en.count.includes('of')) fail('t-ui-i18n', `en count "${en.count}"`);
    if (en.placeholder !== 'Filter by project name…') fail('t-ui-i18n', `en placeholder "${en.placeholder}"`);
    if (en.sortLabels.join('|') !== 'New to old|Old to new|Name|Sessions') fail('t-ui-i18n', `en sort labels ${en.sortLabels}`);
    pass('t-ui-i18n', 'Range/Zeitraum, Show empty/Leere anzeigen, count of/von, search placeholder and sort labels all switch with the language');
  }

  // ── No page errors from the new checks ─────────────────────────────────
  if (pageErrors.length) fail('u-no-pageerror-ui', pageErrors.join(' | '));
  pass('u-no-pageerror-ui', '0 pageerror events (timeline/projects checks)');

  // ── (v) Drill day: timeline row click opens History with that day ──────
  {
    await view('timeline');
    await page.locator('#timeline-range').selectOption('all');
    await page.waitForLoadState('networkidle');
    await page.locator('#timeline-list .row[data-day]').first().waitFor({ state: 'visible', timeout: 15000 });
    const firstRow = page.locator('#timeline-list .row[data-day]').first();
    const dayIso = await firstRow.getAttribute('data-day');
    await firstRow.click();
    await page.locator('#view-history').waitFor({ state: 'visible' });
    await page.waitForLoadState('networkidle');
    await page.waitForFunction(() => {
      const tb = document.querySelector('#hist-tbody');
      return tb && !tb.querySelector('.skeleton');
    }, { timeout: 30000 }).catch(() => {});
    const rows = await page.locator('table.sessions tbody tr[data-id]').count();
    if (rows !== 1) {
      const dbg = await page.evaluate(() => ({
        from: (document.querySelector('#hist-from') || {}).value,
        to: (document.querySelector('#hist-to') || {}).value,
        tbodyLen: ((document.querySelector('#hist-tbody') || {}).innerHTML || '').length,
        trCount: (document.querySelector('#hist-tbody') || {}).querySelectorAll?.('tr')?.length,
        hasDataId: (document.querySelector('#hist-tbody') || {}).querySelectorAll?.('tr[data-id]')?.length,
        histCount: (document.querySelector('#hist-count') || {}).textContent,
      }));
      fail('v-drill-day', `expected 1 table row, got ${rows}; dbg=${JSON.stringify(dbg)}`);
    }
    const histFrom = await page.locator('#hist-from').inputValue();
    const histTo = await page.locator('#hist-to').inputValue();
    if (histFrom !== dayIso || histTo !== dayIso) {
      fail('v-drill-day', `hist-from="${histFrom}" hist-to="${histTo}" want "${dayIso}"`);
    }
    pass('v-drill-day', `row click → History ${histFrom}/${histTo}, 1 row (no Apply)`);
  }

  // ── (w) Drill project clears dates ─────────────────────────────────────
  {
    await view('projects');
    await page.locator('#projects-grid .card').first().waitFor({ state: 'visible' });
    await page.locator('#projects-grid .card[data-project="Atlas-demo"]').click();
    await page.locator('#view-history').waitFor({ state: 'visible' });
    await page.waitForLoadState('networkidle');
    await page.waitForFunction(() => {
      const tb = document.querySelector('#hist-tbody');
      return tb && !tb.querySelector('.skeleton');
    }, { timeout: 30000 }).catch(() => {});
    const rows = await page.locator('table.sessions tbody tr[data-id]').count();
    if (rows !== 6) fail('w-drill-project-clears-dates', `expected 6 table rows, got ${rows}`);
    const histFrom = await page.locator('#hist-from').inputValue();
    const histTo = await page.locator('#hist-to').inputValue();
    if (histFrom !== '' || histTo !== '') {
      fail('w-drill-project-clears-dates', `dates not cleared: from="${histFrom}" to="${histTo}"`);
    }
    const histProject = await page.locator('#hist-project').inputValue();
    if (histProject !== 'Atlas-demo') {
      fail('w-drill-project-clears-dates', `hist-project="${histProject}" want "Atlas-demo"`);
    }
    pass('w-drill-project-clears-dates', `Atlas-demo card → 6 rows, dates cleared, project="Atlas-demo"`);
  }

  // ── (x) Drill day clears project ───────────────────────────────────────
  {
    await view('timeline');
    await page.locator('#timeline-list .row[data-day]').first().waitFor({ state: 'visible', timeout: 15000 });
    await page.locator('#timeline-list .row[data-day]').first().click();
    await page.locator('#view-history').waitFor({ state: 'visible' });
    await page.waitForLoadState('networkidle');
    await page.waitForFunction(() => {
      const tb = document.querySelector('#hist-tbody');
      return tb && !tb.querySelector('.skeleton');
    }, { timeout: 30000 }).catch(() => {});
    const rows = await page.locator('table.sessions tbody tr[data-id]').count();
    if (rows !== 1) fail('x-drill-day-clears-project', `expected 1 table row, got ${rows}`);
    const histProject = await page.locator('#hist-project').inputValue();
    if (histProject !== '') {
      fail('x-drill-day-clears-project', `hist-project="${histProject}" want empty`);
    }
    pass('x-drill-day-clears-project', `row click → 1 row, project cleared`);
  }

  // ── (y) Drill carries provider ─────────────────────────────────────────
  {
    await view('timeline');
    await page.locator('#timeline-tool').selectOption('aider');
    await page.waitForLoadState('networkidle');
    await page.locator('#timeline-list .row[data-day]').first().waitFor({ state: 'visible', timeout: 15000 });
    await page.locator('#timeline-list .row[data-day]').first().click();
    await page.locator('#view-history').waitFor({ state: 'visible' });
    await page.waitForLoadState('networkidle');
    await page.waitForFunction(() => {
      const tb = document.querySelector('#hist-tbody');
      return tb && !tb.querySelector('.skeleton');
    }, { timeout: 30000 }).catch(() => {});
    const histTool = await page.locator('#hist-tool').inputValue();
    if (histTool !== 'aider') fail('y-drill-carries-provider', `hist-tool="${histTool}" want "aider"`);
    const rows = await page.locator('table.sessions tbody tr[data-id]').count();
    if (rows !== 1) fail('y-drill-carries-provider', `expected 1 table row, got ${rows}`);
    pass('y-drill-carries-provider', `tool=aider → hist-tool="aider", 1 row`);
  }

  // ── (z) Home drills: activity bar and tool chip ────────────────────────
  {
    await page.evaluate(() => { const s = document.getElementById('timeline-tool'); if (s) s.value = ''; });
    await view('home');
    await page.locator('#activity-chart .chart-bar[data-day]').first().waitFor({ state: 'visible', timeout: 15000 });
    const barDay = await page.evaluate(() => {
      for (const bar of document.querySelectorAll('#activity-chart .chart-bar[data-day]')) {
        if (bar.style.height !== '3%') return bar.getAttribute('data-day');
      }
      return null;
    });
    if (!barDay) fail('z-home-drills', 'no activity bar with data in the 90-day window');
    await page.locator(`#activity-chart .chart-bar[data-day="${barDay}"]`).click();
    await page.locator('#view-history').waitFor({ state: 'visible' });
    await page.waitForLoadState('networkidle');
    await page.waitForFunction(() => {
      const tb = document.querySelector('#hist-tbody');
      return tb && !tb.querySelector('.skeleton');
    }, { timeout: 30000 }).catch(() => {});
    const barFrom = await page.locator('#hist-from').inputValue();
    if (barFrom !== barDay) fail('z-home-drills', `bar → hist-from="${barFrom}" want "${barDay}"`);
    const barRows = await page.locator('table.sessions tbody tr[data-id]').count();
    if (barRows !== 1) fail('z-home-drills', `bar → ${barRows} rows, want 1`);
    await view('home');
    await page.locator('#tool-legend [data-tool="aider"]').click();
    await page.locator('#view-history').waitFor({ state: 'visible' });
    await page.waitForLoadState('networkidle');
    await page.waitForFunction(() => {
      const tb = document.querySelector('#hist-tbody');
      return tb && !tb.querySelector('.skeleton');
    }, { timeout: 30000 }).catch(() => {});
    const chipTool = await page.locator('#hist-tool').inputValue();
    if (chipTool !== 'aider') fail('z-home-drills', `chip → hist-tool="${chipTool}" want "aider"`);
    const chipFrom = await page.locator('#hist-from').inputValue();
    const chipTo = await page.locator('#hist-to').inputValue();
    if (chipFrom !== '' || chipTo !== '') fail('z-home-drills', `chip → dates not empty: "${chipFrom}"/"${chipTo}"`);
    const chipRows = await page.locator('table.sessions tbody tr[data-id]').count();
    if (chipRows !== 3) fail('z-home-drills', `chip → ${chipRows} rows, want 3`);
    pass('z-home-drills', `bar→${barDay} (1 row); chip aider→3 rows, dates empty`);
  }

  // ── (aa) Calendar label toggles with the mode ──────────────────────────
  {
    await view('timeline');
    const labelList = ((await page.locator('#timeline-cal-label').textContent()) || '').trim();
    if (labelList !== '') fail('aa-cal-label-mode', `label in list mode: "${labelList}"`);
    await page.locator('#timeline-mode-cal').click();
    await page.locator('#timeline-calendar-panel').waitFor({ state: 'visible' });
    const labelCal = ((await page.locator('#timeline-cal-label').textContent()) || '').trim();
    if (labelCal === '') fail('aa-cal-label-mode', 'label empty in calendar mode');
    await page.locator('#timeline-mode-chart').click();
    await page.locator('#timeline-calendar-panel').waitFor({ state: 'hidden' });
    const labelBack = ((await page.locator('#timeline-cal-label').textContent()) || '').trim();
    if (labelBack !== '') fail('aa-cal-label-mode', `label after switching back: "${labelBack}"`);
    pass('aa-cal-label-mode', `list:"" → cal:"${labelCal}" → list:""`);
  }

  // ── (ab) Project status localized: "6 Sitzungen aktiv" / "6 sessions active"
  {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await view('projects');
    await page.locator('#lang-select').selectOption('de');
    await page.waitForFunction(() => {
      const meta = document.querySelector('#projects-grid .card[data-project="Atlas-demo"] .card-meta');
      if (!meta) return false;
      const foot = Array.from(meta.querySelectorAll('span')).map((s) => (s.textContent || '').trim()).join(' ');
      return foot === '6 Sitzungen aktiv';
    }, { timeout: 10000 });
    const spansDe = await page.locator('#projects-grid .card[data-project="Atlas-demo"] .card-meta span').allTextContents();
    const deFoot = spansDe.map((s) => s.trim()).join(' ');
    if (deFoot !== '6 Sitzungen aktiv') fail('ab-sitzungen-aktiv', `de card foot "${deFoot}", want "6 Sitzungen aktiv"`);
    await page.locator('#lang-select').selectOption('en');
    await page.waitForFunction(() => {
      const meta = document.querySelector('#projects-grid .card[data-project="Atlas-demo"] .card-meta');
      if (!meta) return false;
      const foot = Array.from(meta.querySelectorAll('span')).map((s) => (s.textContent || '').trim()).join(' ');
      return foot === '6 sessions active';
    }, { timeout: 10000 });
    const spansEn = await page.locator('#projects-grid .card[data-project="Atlas-demo"] .card-meta span').allTextContents();
    const enFoot = spansEn.map((s) => s.trim()).join(' ');
    if (enFoot !== '6 sessions active') fail('ab-sitzungen-aktiv', `en card foot "${enFoot}", want "6 sessions active"`);
    if (/\bactive\b/.test(deFoot)) fail('ab-sitzungen-aktiv', `raw "active" leaked into the DE foot: "${deFoot}"`);
    pass('ab-sitzungen-aktiv', `de "${deFoot}"; en "${enFoot}"`);
  }

  // ── (ac) Handoff headline built from stats + i18n; copy export matches ─
  {
    await view('home');
    await page.locator('#lang-select').selectOption('de');
    await page.waitForFunction(() => {
      const el = document.querySelector('#handoff-brief > div');
      return el && el.textContent.trim().startsWith('Stand:');
    }, { timeout: 15000 });
    const deHead = ((await page.locator('#handoff-brief > div').first().textContent()) || '').trim();
    if (!deHead.includes('8 Sitzungen, 72 Nachrichten')) fail('ac-handoff-headline', `de headline "${deHead}" lacks "8 Sitzungen, 72 Nachrichten"`);
    await page.locator('#handoff-copy').click();
    await page.waitForTimeout(400);
    const clipDe = await page.evaluate(() => navigator.clipboard.readText());
    if (!clipDe.includes(deHead)) fail('ac-handoff-headline', 'markdown export lacks the DE headline');
    await page.locator('#lang-select').selectOption('en');
    await page.waitForFunction(() => {
      const el = document.querySelector('#handoff-brief > div');
      return el && el.textContent.trim().startsWith('Status:');
    }, { timeout: 15000 });
    const enHead = ((await page.locator('#handoff-brief > div').first().textContent()) || '').trim();
    if (!enHead.includes('8 sessions, 72 messages')) fail('ac-handoff-headline', `en headline "${enHead}" lacks "8 sessions, 72 messages"`);
    await page.locator('#handoff-copy').click();
    await page.waitForTimeout(400);
    const clipEn = await page.evaluate(() => navigator.clipboard.readText());
    if (!clipEn.includes(enHead)) fail('ac-handoff-headline', 'markdown export lacks the EN headline');
    pass('ac-handoff-headline', `de "${deHead}"; en "${enHead}"; both present in the copied markdown`);
  }

  // ── (ad) Duration column shows minutes ("24 min"), not raw seconds ─────
  {
    await view('history');
    await page.locator('#hist-reset').click();
    await page.waitForFunction(() => {
      const tb = document.querySelector('#hist-tbody');
      return tb && !tb.querySelector('.skeleton') && tb.querySelector('tr[data-id]');
    }, { timeout: 30000 });
    const rows = await page.locator('#hist-tbody tr[data-id]').count();
    if (rows !== 18) fail('ad-duration-min', `expected 18 fixture rows after reset, got ${rows}`);
    const durs = (await page.locator('#hist-tbody td:nth-child(3) span').allTextContents()).map((s) => s.trim());
    if (!durs.length) fail('ad-duration-min', 'no duration spans rendered in the Ended column');
    const bad = durs.filter((d) => d !== '24 min');
    if (bad.length) fail('ad-duration-min', `durations not "24 min": ${bad.join(' | ')}`);
    const tbodyText = (await page.locator('#hist-tbody').textContent()) || '';
    if (tbodyText.includes('1440s')) fail('ad-duration-min', 'old "1440s" format still present');
    pass('ad-duration-min', `${durs.length} fixture durations render as "24 min" (was "1440s")`);
  }

  // ── (ae) Needs-attention label+hint in full; no duplicate nav icon ─────
  {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await view('home');
    await page.locator('#health-recovery').waitFor({ state: 'visible' });
    await page.locator('#health-recovery .row-title').filter({ hasText: 'Start Ollama embeddings' }).waitFor({ timeout: 10000 });
    const row = page.locator('#health-recovery .row-single').first();
    const titleText = ((await row.locator('.row-title').textContent()) || '').trim();
    const hintText = ((await row.locator('.row-meta').textContent()) || '').trim();
    if (titleText !== 'Start Ollama embeddings') fail('ae-attention-full', `title "${titleText}"`);
    if (hintText !== 'ollama serve && ollama pull nomic-embed-text') fail('ae-attention-full', `hint "${hintText}"`);
    const fit = await row.evaluate((el) => {
      const t = el.querySelector('.row-title');
      const h = el.querySelector('.row-meta');
      return { tf: t.scrollWidth <= t.clientWidth, hf: h.scrollWidth <= h.clientWidth, tsw: t.scrollWidth, tcw: t.clientWidth, hsw: h.scrollWidth, hcw: h.clientWidth };
    });
    if (!fit.tf) fail('ae-attention-full', `title clipped: scrollWidth ${fit.tsw} > clientWidth ${fit.tcw}`);
    if (!fit.hf) fail('ae-attention-full', `hint clipped: scrollWidth ${fit.hsw} > clientWidth ${fit.hcw}`);
    const icons = (await page.locator('#nav .icon').allTextContents()).map((s) => s.trim()).filter(Boolean);
    const seen = new Set();
    const dups = icons.filter((g) => (seen.has(g) ? true : (seen.add(g), false)));
    if (dups.length) fail('ae-attention-full', `duplicate nav icon(s) ${dups.join(',')} in [${icons.join(' ')}]`);
    if (icons.length !== 10) fail('ae-attention-full', `expected 10 nav icons, got ${icons.length}: [${icons.join(' ')}]`);
    pass('ae-attention-full', `label+hint unclipped (title ${fit.tsw}/${fit.tcw}, hint ${fit.hsw}/${fit.hcw}); 10 unique nav icons`);
  }

  // ── (af) Mobile 390px: collapsible sidebar, no horizontal overflow ────
  {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(300);
    const over = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: window.innerWidth }));
    if (over.sw > over.iw) fail('af-mobile-nav', `horizontal overflow at 390px: scrollWidth ${over.sw} > innerWidth ${over.iw}`);
    if (!(await page.locator('#nav-toggle').isVisible())) fail('af-mobile-nav', '#nav-toggle not visible at 390px');
    if (await page.locator('#nav').isVisible()) fail('af-mobile-nav', '#nav visible before toggle at 390px');
    await page.locator('#nav-toggle').click();
    await page.locator('#nav').waitFor({ state: 'visible' });
    if ((await page.locator('#nav-toggle').getAttribute('aria-expanded')) !== 'true') fail('af-mobile-nav', 'aria-expanded not "true" after opening');
    await page.locator('#nav [data-view="timeline"]').click();
    await page.locator('#view-timeline').waitFor({ state: 'visible' });
    await page.locator('#nav').waitFor({ state: 'hidden' });
    if ((await page.locator('#nav-toggle').getAttribute('aria-expanded')) !== 'false') fail('af-mobile-nav', 'aria-expanded not "false" after picking a view');
    const tlH1 = ((await page.locator('#view-timeline h1').textContent()) || '').trim();
    if (!['Zeitachse', 'Timeline'].includes(tlH1)) fail('af-mobile-nav', `timeline h1 "${tlH1}"`);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.waitForTimeout(300);
    if (await page.locator('#nav-toggle').isVisible()) fail('af-mobile-nav', '#nav-toggle still visible at 1440px');
    if (!(await page.locator('#nav').isVisible())) fail('af-mobile-nav', '#nav not visible at 1440px');
    pass('af-mobile-nav', `390px: no overflow (≤ ${over.iw}px), drawer opens/collapses around the "${tlH1}" pick; 1440px: toggle hidden, nav visible`);
  }

  // ── (ag) Deep-link #timeline on boot; nav sets the hash; reload keeps ──
  {
    await page.goto('about:blank');
    await page.goto(base + '/#timeline');
    await page.waitForLoadState('networkidle');
    await page.locator('#view-timeline').waitFor({ state: 'visible' });
    const tlH1 = ((await page.locator('#view-timeline h1').textContent()) || '').trim();
    if (!['Zeitachse', 'Timeline'].includes(tlH1)) fail('ag-deeplink', `h1 "${tlH1}" after boot on #timeline`);
    if (await page.locator('#view-home').isVisible()) fail('ag-deeplink', 'home view still visible after boot on #timeline');
    await page.locator('#nav [data-view="projects"]').click();
    await page.waitForFunction(() => location.hash === '#projects', { timeout: 5000 });
    await page.reload();
    await page.waitForLoadState('networkidle');
    await page.locator('#view-projects').waitFor({ state: 'visible' });
    const hash = await page.evaluate(() => location.hash);
    if (hash !== '#projects') fail('ag-deeplink', `hash after reload "${hash}", want "#projects"`);
    if (await page.locator('#view-projects').isHidden()) fail('ag-deeplink', 'projects view hidden after reload');
    pass('ag-deeplink', `boot on #timeline opens "${tlH1}"; nav click → ${hash}; reload keeps the projects view`);
  }

  // ── (ah) Assistants view (imported source): tools, counter, skills, mask ─
  {
    await fetch(base + '/api/operate?job=import-config');
    await view('assistants');
    const toolBtns = page.locator('#asst-tools button.asst-tool-btn');
    await toolBtns.first().waitFor({ state: 'visible', timeout: 10000 });
    const toolCount = await toolBtns.count();
    const toolNames = (await toolBtns.locator('strong').allTextContents()).map(s => s.trim());
    if (toolCount < 3) fail('ah-assistants-view', `expected at least three tool buttons, got ${toolCount}: ${JSON.stringify(toolNames)}`);
    for (const want of ['claude_code', 'codex', 'cursor']) {
      if (!toolNames.includes(want)) fail('ah-assistants-view', `tool "${want}" missing from ${JSON.stringify(toolNames)}`);
    }
    // claude_code counter line: 2 Skills, 1 Agent
    const claudeBtn = page.locator('#asst-tools button.asst-tool-btn[data-tool="claude_code"]');
    const claudeText = (await claudeBtn.textContent()) || '';
    if (!/2\s+Skills/i.test(claudeText)) fail('ah-assistants-view', `claude_code counter missing "2 Skills": "${claudeText}"`);
    if (!/1\s+Agents?/i.test(claudeText)) fail('ah-assistants-view', `claude_code counter missing "1 Agent": "${claudeText}"`);
    // Instructions tab: open the project CLAUDE.md, both chips must stay hidden
    const instrFileResp = page.waitForResponse((r) => r.url().includes('/api/assistants/imported/'));
    await page.locator('#asst-list [data-id]').filter({ hasText: 'Atlas-demo' }).first().click();
    await instrFileResp;
    await page.waitForFunction(() => (document.querySelector('#asst-content') || {}).textContent?.includes('Atlas Demo Project'), null, { timeout: 10000 });
    if (await page.locator('#asst-chip-redacted').isVisible()) fail('ah-assistants-view', 'redacted chip visible on a plain CLAUDE.md');
    if (await page.locator('#asst-chip-truncated').isVisible()) fail('ah-assistants-view', 'truncated chip visible on a 193 B file');
    // Skills tab lists release-notes with description (client-side filter)
    await page.locator('#asst-tab-skills').click();
    await page.waitForFunction(() => Array.from(document.querySelectorAll('#asst-list [data-id]')).some((el) => el.textContent.includes('release-notes')), null, { timeout: 10000 });
    const skillEntries = page.locator('#asst-list [data-id]');
    const skillTexts = (await skillEntries.allTextContents()).join('\n');
    if (!skillTexts.includes('release-notes')) fail('ah-assistants-view', `skills list missing "release-notes": "${skillTexts.slice(0, 200)}"`);
    if (!skillTexts.includes('Draft release notes from merged pull requests.')) fail('ah-assistants-view', `skills list missing description: "${skillTexts.slice(0, 200)}"`);
    // Config tab of codex: config.toml shows api_key masked (client-side filter)
    await page.locator('#asst-tools button.asst-tool-btn[data-tool="codex"]').click();
    await page.locator('#asst-tab-config').click();
    await page.waitForFunction(() => Array.from(document.querySelectorAll('#asst-list [data-id]')).some((el) => el.textContent.includes('config.toml')), null, { timeout: 10000 });
    const fileResp = page.waitForResponse((r) => r.url().includes('/api/assistants/imported/'));
    await page.locator('#asst-list [data-id]').filter({ hasText: 'config.toml' }).first().click();
    await fileResp;
    await page.waitForFunction(() => (document.querySelector('#asst-content') || {}).textContent?.includes('api_key = "•••"'), null, { timeout: 10000 });
    const contentText = (await page.locator('#asst-content').textContent()) || '';
    if (!contentText.includes('api_key = "•••"')) fail('ah-assistants-view', `config.toml content missing masked api_key: "${contentText.slice(0, 200)}"`);
    const redChip = page.locator('#asst-chip-redacted');
    if (await redChip.isVisible()) fail('ah-assistants-view', 'redacted chip visible although the imported source carries no redaction flag');
    if (await page.locator('#asst-chip-truncated').isVisible()) fail('ah-assistants-view', 'truncated chip visible on config.toml');
    // Sort options remain
    const sortOpts = (await page.locator('#asst-sort option').allTextContents()).map(s => s.trim());
    const wantEN = ['New to old', 'Old to new', 'Name'];
    if (JSON.stringify(sortOpts) !== JSON.stringify(wantEN)) fail('ah-assistants-view', `EN sort options ${JSON.stringify(sortOpts)}, want ${JSON.stringify(wantEN)}`);
    pass('ah-assistants-view', `imported source, tools [${toolNames.join(', ')}]; claude_code counter has "2 Skills" and "1 Agent"; skills list has release-notes with description; codex config.toml api_key masked in imported content; sort options correct`);
  }

  // ── (ai) Assistants file endpoint: 404 for unknown id and path param ──
  {
    const r1 = await fetch(base + '/api/assistants/file?id=0000000000000000');
    if (r1.status !== 404) fail('ai-assistants-file-404', `id=0000000000000000: HTTP ${r1.status}, want 404`);
    const b1 = await r1.json();
    if (!b1.error) fail('ai-assistants-file-404', `id=0000000000000000: no error field in ${JSON.stringify(b1)}`);
    const r2 = await fetch(base + '/api/assistants/file?id=x&path=/etc/passwd');
    if (r2.status !== 404) fail('ai-assistants-file-404', `id=x&path=/etc/passwd: HTTP ${r2.status}, want 404`);
    const b2 = await r2.json();
    if (!b2.error) fail('ai-assistants-file-404', `id=x&path=/etc/passwd: no error field in ${JSON.stringify(b2)}`);
    pass('ai-assistants-file-404', 'unknown id and path injection both return 404 with error field');
  }

  // ── (aj) Messages sort: order=asc flips list, persists across reload ──
  {
    await view('browse');
    await page.locator('#browse-list [data-msg]').first().waitFor({ state: 'visible', timeout: 10000 });
    const firstIdBefore = await page.locator('#browse-list [data-msg]').first().getAttribute('data-msg');
    const respP = page.waitForResponse((r) => r.url().includes('/api/messages') && r.url().includes('order=asc'));
    await page.locator('#browse-sort').selectOption('asc');
    const resp = await respP;
    const body = await resp.json();
    const msgs = body.messages || [];
    if (msgs.length < 2) fail('aj-sort-messages', `asc response has ${msgs.length} messages, need ≥2`);
    await page.waitForFunction((prev) => {
      const el = document.querySelector('#browse-list [data-msg]');
      return el && el.getAttribute('data-msg') !== prev;
    }, firstIdBefore, { timeout: 10000 });
    const firstIdAfter = await page.locator('#browse-list [data-msg]').first().getAttribute('data-msg');
    const d0 = new Date(msgs[0].created_at).getTime();
    const d1 = new Date(msgs[1].created_at).getTime();
    if (d0 > d1) fail('aj-sort-messages', `asc broken: ${msgs[0].created_at} > ${msgs[1].created_at}`);
    await page.reload();
    await page.waitForLoadState('networkidle');
    const persisted = await page.evaluate(() => (document.getElementById('browse-sort') || {}).value);
    if (persisted !== 'asc') fail('aj-sort-messages', `after reload browse-sort="${persisted}", expected "asc"`);
    await page.locator('#browse-sort').selectOption('desc');
    pass('aj-sort-messages', `first #${firstIdBefore}→#${firstIdAfter}, ${msgs[0].created_at.slice(0, 10)}≤${msgs[1].created_at.slice(0, 10)}, persists after reload`);
  }

  // ── (ak) Memory sort: order=asc flips list, persists across reload ──
  {
    await view('memory');
    await page.locator('#memory-list [data-mem-id]').first().waitFor({ state: 'visible', timeout: 10000 });
    const firstIdBefore = await page.locator('#memory-list [data-mem-id]').first().getAttribute('data-mem-id');
    const respP = page.waitForResponse((r) => r.url().includes('/api/memory') && r.url().includes('order=asc'));
    await page.locator('#memory-sort').selectOption('asc');
    const resp = await respP;
    const body = await resp.json();
    const chunks = body.chunks || [];
    if (chunks.length < 2) fail('ak-sort-memory', `asc response has ${chunks.length} chunks, need ≥2`);
    await page.waitForFunction((prev) => {
      const el = document.querySelector('#memory-list [data-mem-id]');
      return el && el.getAttribute('data-mem-id') !== prev;
    }, firstIdBefore, { timeout: 10000 });
    const firstIdAfter = await page.locator('#memory-list [data-mem-id]').first().getAttribute('data-mem-id');
    const d0 = new Date(chunks[0].created_at).getTime();
    const d1 = new Date(chunks[1].created_at).getTime();
    if (d0 > d1) fail('ak-sort-memory', `asc broken: ${chunks[0].created_at} > ${chunks[1].created_at}`);
    await page.reload();
    await page.waitForLoadState('networkidle');
    const persisted = await page.evaluate(() => (document.getElementById('memory-sort') || {}).value);
    if (persisted !== 'asc') fail('ak-sort-memory', `after reload memory-sort="${persisted}", expected "asc"`);
    await page.locator('#memory-sort').selectOption('desc');
    pass('ak-sort-memory', `first #${firstIdBefore}→#${firstIdAfter}, ${chunks[0].created_at.slice(0, 10)}≤${chunks[1].created_at.slice(0, 10)}, persists after reload`);
  }

  // ── (al) Detail sort: default desc, asc reverses message order ──
  {
    await page.goto(base);
    await page.evaluate(() => { try { localStorage.removeItem('th-sort-detail'); } catch {} });
    await page.reload();
    await page.waitForLoadState('networkidle');
    await page.locator('#handoff-brief .row[data-id]').first().waitFor({ state: 'visible', timeout: 10000 });
    await page.locator('#handoff-brief .row[data-id]').first().click();
    await page.locator('#detail-messages .msg').first().waitFor({ state: 'visible', timeout: 10000 });
    const dates = await page.locator('#detail-messages .msg').evaluateAll(
      els => els.map(e => e.getAttribute('data-created-at'))
    );
    if (dates.length < 2) fail('al-sort-detail', `need ≥2 messages in detail, got ${dates.length}`);
    const firstT = new Date(dates[0]).getTime();
    const lastT = new Date(dates[dates.length - 1]).getTime();
    if (firstT < lastT) fail('al-sort-detail', `desc broken: first ${dates[0]} < last ${dates[dates.length - 1]}`);
    const respP = page.waitForResponse((r) => r.url().includes('/api/conversations/') && r.url().includes('order=asc'));
    await page.locator('#detail-sort').selectOption('asc');
    await respP;
    await page.waitForFunction((prev) => {
      const el = document.querySelector('#detail-messages .msg');
      return el && el.getAttribute('data-created-at') !== prev;
    }, dates[0], { timeout: 10000 });
    const datesAsc = await page.locator('#detail-messages .msg').evaluateAll(
      els => els.map(e => e.getAttribute('data-created-at'))
    );
    const firstAscT = new Date(datesAsc[0]).getTime();
    const lastAscT = new Date(datesAsc[datesAsc.length - 1]).getTime();
    if (firstAscT > lastAscT) fail('al-sort-detail', `asc broken: first ${datesAsc[0]} > last ${datesAsc[datesAsc.length - 1]}`);
    pass('al-sort-detail', `desc first≥last (${dates[0].slice(0, 10)}≥${dates[dates.length - 1].slice(0, 10)}); asc reversed (${datesAsc[0].slice(0, 10)}≤${datesAsc[datesAsc.length - 1].slice(0, 10)})`);
  }

  // ── (am) Home sort: asc puts oldest started_at first in handoff list ──
  {
    await page.goto(base);
    await page.waitForLoadState('networkidle');
    await page.locator('#handoff-brief .row[data-id]').first().waitFor({ state: 'visible', timeout: 10000 });
    const respP = page.waitForResponse((r) => r.url().includes('/api/handoff'));
    await page.locator('#home-sort').selectOption('asc');
    const resp = await respP;
    const body = await resp.json();
    const recent = (body.recent_sessions || []).slice(0, 4);
    if (recent.length < 2) fail('am-sort-home', `need ≥2 recent sessions, got ${recent.length}`);
    await page.locator('#handoff-brief .row[data-id]').first().waitFor({ state: 'visible' });
    const sorted = recent.slice().sort((a, b) => new Date(a.started_at) - new Date(b.started_at));
    const expectedId = String(sorted[0].id);
    const domId = await page.locator('#handoff-brief .row[data-id]').first().getAttribute('data-id');
    if (domId !== expectedId) fail('am-sort-home', `asc first row #${domId}, expected #${expectedId} (oldest started_at ${sorted[0].started_at})`);
    await page.locator('#home-sort').selectOption('desc');
    pass('am-sort-home', `asc first=#${domId} (oldest ${sorted[0].started_at.slice(0, 10)}), back to desc`);
  }

  // ── (an) Find sort: asc oldest first, desc newest, relevance default ──
  {
    await view('find');
    await page.locator('#find-mode').selectOption('lexical');
    const searchP = page.waitForResponse((r) => r.url().includes('/api/search'));
    await page.locator('#find-q').fill('decision');
    const searchResp = await searchP;
    const searchBody = await searchResp.json();
    const hits = searchBody.hits || [];
    if (hits.length < 2) fail('an-sort-find', `only ${hits.length} hits for "decision", need ≥2`);
    await page.locator('#find-results .row[data-id]').first().waitFor({ state: 'visible', timeout: 15000 });
    await page.locator('#find-sort').selectOption('asc');
    await page.waitForFunction(() => {
      const rows = document.querySelectorAll('#find-results .row[data-created-at]');
      return rows.length >= 2 && rows[0].getAttribute('data-created-at') <= rows[1].getAttribute('data-created-at');
    }, null, { timeout: 10000 });
    const ascDates = await page.locator('#find-results .row[data-created-at]').evaluateAll((els) => els.slice(0, 2).map((e) => e.getAttribute('data-created-at')));
    if (ascDates[0] > ascDates[1]) fail('an-sort-find', `asc broken: ${ascDates[0]} > ${ascDates[1]}`);
    await page.locator('#find-sort').selectOption('desc');
    await page.waitForFunction(() => {
      const rows = document.querySelectorAll('#find-results .row[data-created-at]');
      return rows.length >= 2 && rows[0].getAttribute('data-created-at') >= rows[1].getAttribute('data-created-at');
    }, null, { timeout: 10000 });
    const descDates = await page.locator('#find-results .row[data-created-at]').evaluateAll((els) => els.slice(0, 2).map((e) => e.getAttribute('data-created-at')));
    if (descDates[0] < descDates[1]) fail('an-sort-find', `desc broken: ${descDates[0]} < ${descDates[1]}`);
    await page.locator('#find-sort').selectOption('relevance');
    pass('an-sort-find', `asc ${ascDates[0].slice(0, 10)}≤${ascDates[1].slice(0, 10)}, desc ${descDates[0].slice(0, 10)}≥${descDates[1].slice(0, 10)}, back to relevance`);
  }

  // ── (ao) Projects sort: activity-asc oldest first, activity newest ────
  {
    await view('projects');
    await page.locator('#projects-grid .card[data-project]').first().waitFor({ state: 'visible', timeout: 15000 });
    const projData = await json('/api/projects');
    const projects = (projData.projects || []).filter((p) => Number(p.conversations || 0) > 0 && p.last_started_at);
    if (projects.length < 2) fail('ao-sort-projects', `only ${projects.length} projects with activity, need ≥2`);
    const byOldest = projects.slice().sort((a, b) => (a.last_started_at < b.last_started_at ? -1 : 1));
    const byNewest = projects.slice().sort((a, b) => (a.last_started_at > b.last_started_at ? -1 : 1));
    await page.locator('#proj-sort').selectOption('activity-asc');
    await page.locator('#projects-grid .card[data-project]').first().waitFor({ state: 'visible' });
    const firstAsc = await page.locator('#projects-grid .card[data-project]').first().getAttribute('data-project');
    if (firstAsc !== byOldest[0].name) fail('ao-sort-projects', `activity-asc first "${firstAsc}", expected "${byOldest[0].name}" (oldest ${byOldest[0].last_started_at})`);
    await page.locator('#proj-sort').selectOption('activity');
    await page.locator('#projects-grid .card[data-project]').first().waitFor({ state: 'visible' });
    const firstDesc = await page.locator('#projects-grid .card[data-project]').first().getAttribute('data-project');
    if (firstDesc !== byNewest[0].name) fail('ao-sort-projects', `activity first "${firstDesc}", expected "${byNewest[0].name}" (newest ${byNewest[0].last_started_at})`);
    pass('ao-sort-projects', `asc first "${firstAsc}" (${byOldest[0].last_started_at.slice(0, 10)}), desc first "${firstDesc}" (${byNewest[0].last_started_at.slice(0, 10)})`);
  }

  // ── (ap) Timeline sort: asc smallest day first, desc largest ──────────
  {
    await view('timeline');
    await page.locator('#timeline-list .row[data-day]').first().waitFor({ state: 'visible', timeout: 15000 });
    const days = await page.locator('#timeline-list .row[data-day]').evaluateAll((els) => els.map((e) => e.getAttribute('data-day')));
    if (days.length < 2) fail('ap-sort-timeline', `only ${days.length} day rows, need ≥2`);
    const minDay = days.slice().sort()[0];
    const maxDay = days.slice().sort().reverse()[0];
    await page.locator('#timeline-sort').selectOption('asc');
    await page.waitForFunction((d) => {
      const el = document.querySelector('#timeline-list .row[data-day]');
      return el && el.getAttribute('data-day') === d;
    }, minDay, { timeout: 10000 });
    await page.locator('#timeline-sort').selectOption('desc');
    await page.waitForFunction((d) => {
      const el = document.querySelector('#timeline-list .row[data-day]');
      return el && el.getAttribute('data-day') === d;
    }, maxDay, { timeout: 10000 });
    pass('ap-sort-timeline', `asc first day ${minDay}, desc first day ${maxDay}`);
  }

  // ── (aq) History sort labels EN/DE + persistence of hist-sort ────────
  {
    await view('history');
    await page.locator('#lang-select').selectOption('en');
    await page.waitForTimeout(200);
    const enOpts = (await page.locator('#hist-sort option').allTextContents()).map((s) => s.trim());
    const wantEN = ['New to old', 'Old to new', 'Most messages'];
    if (JSON.stringify(enOpts) !== JSON.stringify(wantEN)) fail('aq-sort-history', `EN options ${JSON.stringify(enOpts)}, want ${JSON.stringify(wantEN)}`);
    await page.locator('#lang-select').selectOption('de');
    await page.waitForTimeout(200);
    const deOpts = (await page.locator('#hist-sort option').allTextContents()).map((s) => s.trim());
    const wantDE = ['Neu zu Alt', 'Alt zu Neu', 'Meiste Nachrichten'];
    if (JSON.stringify(deOpts) !== JSON.stringify(wantDE)) fail('aq-sort-history', `DE options ${JSON.stringify(deOpts)}, want ${JSON.stringify(wantDE)}`);
    await page.locator('#lang-select').selectOption('en');
    await page.waitForTimeout(200);
    await page.locator('#hist-sort').selectOption('old');
    await page.reload();
    await page.waitForLoadState('networkidle');
    const persisted = await page.evaluate(() => (document.getElementById('hist-sort') || {}).value);
    if (persisted !== 'old') fail('aq-sort-history', `after reload hist-sort="${persisted}", expected "old"`);
    await page.locator('#hist-sort').selectOption('recent');
    pass('aq-sort-history', `EN [${enOpts.join(', ')}], DE [${deOpts.join(', ')}], "old" persists across reload`);
  }

  // ── (ar) Assistants imported source: job, toggle, stand line, tools ────
  {
    const imp = await (await fetch(base + '/api/assistants/imported')).json();
    if (!imp.last_import) fail('ar-asst-imported', `last_import is not set (${JSON.stringify(imp.last_import)}); expected the config-import to have run`);
    await view('assistants');
    const impPressed = await page.locator('#asst-src-imported').getAttribute('aria-pressed');
    if (impPressed !== 'true') fail('ar-asst-imported', `asst-src-imported aria-pressed="${impPressed}"`);
    const standEnOk = await page.waitForFunction(
      () => ((document.querySelector('#asst-stand') || {}).textContent || '').includes('As of'),
      null, { timeout: 10000 }
    ).then(() => true).catch(() => false);
    const standEn = (await page.locator('#asst-stand').textContent()) || '';
    if (!standEnOk || !standEn.includes('As of')) fail('ar-asst-imported', `stand line "${standEn}" missing "As of"`);
    const claudeBtn = page.locator('#asst-tools button.asst-tool-btn[data-tool="claude_code"]');
    await claudeBtn.waitFor({ state: 'visible', timeout: 10000 });
    const claudeText = (await claudeBtn.textContent()) || '';
    if (!/2\s+skills/i.test(claudeText)) fail('ar-asst-imported', `claude_code missing "2 skills": "${claudeText}"`);
    await page.locator('#lang-select').selectOption('de');
    await page.waitForTimeout(200);
    await view('assistants');
    const standDeOk = await page.waitForFunction(
      () => ((document.querySelector('#asst-stand') || {}).textContent || '').includes('Stand'),
      null, { timeout: 10000 }
    ).then(() => true).catch(() => false);
    const standDe = (await page.locator('#asst-stand').textContent()) || '';
    if (!standDeOk || !standDe.includes('Stand')) fail('ar-asst-imported', `DE stand line "${standDe}" missing "Stand"`);
    await page.locator('#lang-select').selectOption('en');
    await page.waitForTimeout(200);
    await view('assistants');
    await page.waitForFunction(
      () => ((document.querySelector('#asst-stand') || {}).textContent || '').includes('As of'),
      null, { timeout: 10000 }
    );
    await claudeBtn.click();
    await page.locator('#asst-tab-skills').click();
    await page.waitForFunction(() => Array.from(document.querySelectorAll('#asst-list [data-id]')).some((el) => el.textContent.includes('release-notes')), null, { timeout: 10000 });
    const fileRespP = page.waitForResponse((r) => r.url().includes('/api/assistants/imported/'));
    await page.locator('#asst-list [data-id]').filter({ hasText: 'release-notes' }).first().click();
    const fileResp = await fileRespP;
    if (!/\/api\/assistants\/imported\/\d+/.test(fileResp.url())) fail('ar-asst-imported', `file load URL ${fileResp.url()}`);
    await page.waitForFunction(() => ((document.querySelector('#asst-content') || {}).textContent || '').includes('release-notes'), null, { timeout: 10000 });
    // Job button in Import and status shows the import-config result line
    await view('operate');
    const btnExists = await page.locator('#view-operate [data-job="import-config"]').count();
    if (btnExists !== 1) fail('ar-asst-imported', `data-job="import-config" buttons in operate view: ${btnExists}`);
    await page.locator('#view-operate [data-job="import-config"]').click();
    await page.waitForFunction(() => ((document.querySelector('#operate-out') || {}).textContent || '').includes('import-config: seen='), null, { timeout: 20000 });
    const outText = (await page.locator('#operate-out').textContent()) || '';
    if (!outText.includes('import-config: seen=')) fail('ar-asst-imported', `operate-out missing "import-config: seen=": "${outText.slice(0, 200)}"`);
    if (!outText.includes('unchanged=12')) fail('ar-asst-imported', `operate-out missing "unchanged=12": "${outText.slice(0, 200)}"`);
    pass('ar-asst-imported', `last_import ${imp.last_import}, ${Array.isArray(imp.entries) ? imp.entries.length : 0} entries, toggle Importiert, stand "${standEn.trim()}" and "${standDe.trim()}", claude_code has "2 skills", skills list has release-notes, content via ${fileResp.url().replace(base, '')}, operate button shows "unchanged=12"`);
  }

  // ── (as) Assistants source toggle: live per-tool API, persists, back ───
  {
    await view('assistants');
    const liveResp = page.waitForResponse((r) => r.url().includes('/api/assistants/claude_code') && r.url().includes('kind='));
    await page.locator('#asst-src-live').click();
    const liveR = await liveResp;
    if (!liveR.url().includes('/api/assistants/claude_code?kind=')) fail('as-asst-live-toggle', `live list loaded from ${liveR.url()}`);
    const livePressed = await page.locator('#asst-src-live').getAttribute('aria-pressed');
    if (livePressed !== 'true') fail('as-asst-live-toggle', `asst-src-live aria-pressed="${livePressed}" after switch`);
    await page.reload();
    await page.waitForLoadState('networkidle');
    await page.locator('#view-assistants').waitFor({ state: 'visible' });
    const afterReload = await page.locator('#asst-src-live').getAttribute('aria-pressed');
    if (afterReload !== 'true') fail('as-asst-live-toggle', `asst-src-live aria-pressed="${afterReload}" after reload, want "true"`);
    await page.locator('#asst-src-imported').click();
    const backPressed = await page.locator('#asst-src-imported').getAttribute('aria-pressed');
    if (backPressed !== 'true') fail('as-asst-live-toggle', `asst-src-imported aria-pressed="${backPressed}" after switching back`);
    pass('as-asst-live-toggle', `Live loads ${liveR.url().replace(base, '')}, stays Live after reload, back to Importiert`);
  }

  // ── (at) Search hit kind config: Configuration chip, opens imported ────
  {
    await view('find');
    await page.locator('#find-mode').selectOption('lexical');
    const searchP = page.waitForResponse((r) => r.url().includes('/api/search'));
    await page.locator('#find-q').fill('release-notes');
    await searchP;
    const cfgRows = page.locator('#find-results .row-config');
    await cfgRows.first().waitFor({ state: 'visible', timeout: 15000 });
    const cfgCount = await cfgRows.count();
    let chipOk = false;
    for (let i = 0; i < cfgCount; i++) {
      const chips = (await cfgRows.nth(i).locator('.chip').allTextContents()).map((s) => s.trim());
      if (chips.includes('Configuration')) chipOk = true;
    }
    if (!chipOk) fail('at-search-config', `no config hit with chip "Configuration" among ${cfgCount} config rows`);
    await cfgRows.filter({ hasText: 'release-notes' }).first().click();
    await page.locator('#view-assistants').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForFunction(() => ((document.querySelector('#asst-content') || {}).textContent || '').includes('release-notes'), null, { timeout: 10000 });
    const content = (await page.locator('#asst-content').textContent()) || '';
    if (!content.includes('release-notes')) fail('at-search-config', `loaded content missing "release-notes": "${content.slice(0, 200)}"`);
    pass('at-search-config', `chip "Configuration" on ${cfgCount} config hit(s), click opened #view-assistants with release-notes content`);
  }
  // ── (au) Memory cards show source time; extracted chip only if >60 s ──
  // Zeitgrundsatz: the displayed date is source_at (fallback created_at); the
  // extraction time shows as an extra "extracted" chip only when source_at and
  // created_at differ by more than 60 s. Fixture rows carry source_at (session
  // end for the conversation notes, created_at − 2 days for the notes without
  // a conversation), so this proves the displayed date equals source_at, the
  // extracted chip rule over the list (at least one card shows the chip), the
  // EN/DE chip wording and the mem.extracted dictionary entries.
  {
    await page.goto(base);
    await page.waitForLoadState('networkidle');
    await page.locator('#lang-select').selectOption('en');
    await page.waitForTimeout(200);
    await view('memory');
    await page.locator('#memory-list .mem-card').first().waitFor({ state: 'visible', timeout: 10000 });
    const api1 = await json('/api/memory?limit=1');
    const ch = (api1.chunks || [])[0];
    if (!ch) fail('au-memory-source-date', '/api/memory?limit=1 returned no chunks');
    const apiSrc = ch.source_at || ch.created_at;
    const diffMs = ch.source_at ? Math.abs(new Date(ch.source_at) - new Date(ch.created_at)) : 0;
    const expectExtracted = diffMs > 60000;
    const card = page.locator('#memory-list .mem-card').first();
    const ds = await card.getAttribute('data-source-at');
    const dc = await card.getAttribute('data-created-at');
    if (ds !== apiSrc) fail('au-memory-source-date', `data-source-at "${ds}" != API source_at "${ch.source_at}" (srcIso "${apiSrc}")`);
    if (dc !== ch.created_at) fail('au-memory-source-date', `data-created-at "${dc}" != API created_at "${ch.created_at}"`);
    const fmtSrc = await page.evaluate((iso) => new Date(iso).toLocaleString('en-GB'), apiSrc);
    const fmtCreated = await page.evaluate((iso) => new Date(iso).toLocaleString('en-GB'), ch.created_at);
    const enLabel = ch.source_at ? 'source from ' : 'recorded ';
    const deLabel = ch.source_at ? 'Quelle vom ' : 'erfasst ';
    const enChips = (await card.locator('.chip').allTextContents()).map((s) => s.trim());
    if (!enChips.includes(enLabel + fmtSrc)) fail('au-memory-source-date', `EN chips ${JSON.stringify(enChips)} lack "${enLabel}${fmtSrc}"`);
    const enExt = enChips.find((x) => x.startsWith('extracted ')) || null;
    if (expectExtracted !== !!enExt) fail('au-memory-source-date', `EN extracted chip ${enExt ? 'shown' : 'absent'} but source_at/created_at diff ${diffMs}ms`);
    if (expectExtracted && enExt !== 'extracted ' + fmtCreated) fail('au-memory-source-date', `EN extracted chip "${enExt}" != "extracted ${fmtCreated}"`);
    const extractedCards = await page.locator('#memory-list .mem-card').evaluateAll((els) => els.filter((e) =>
      Array.from(e.querySelectorAll('.chip')).some((x) => /^extracted /.test(x.textContent || ''))).length);
    if (extractedCards < 1) fail('au-memory-source-date', 'no visible card shows the "extracted" chip although the fixture carries notes with source_at = created_at − 2 days');
    await page.locator('#lang-select').selectOption('de');
    await page.waitForFunction(() => Array.from(document.querySelectorAll('#memory-list .mem-card .chip'))
      .some((x) => /^(erfasst|Quelle vom) /.test((x.textContent || '').trim())), null, { timeout: 10000 });
    const fmtSrcDe = await page.evaluate((iso) => new Date(iso).toLocaleString('de-DE'), apiSrc);
    const fmtCreatedDe = await page.evaluate((iso) => new Date(iso).toLocaleString('de-DE'), ch.created_at);
    const deChips = (await card.locator('.chip').allTextContents()).map((s) => s.trim());
    if (!deChips.includes(deLabel + fmtSrcDe)) fail('au-memory-source-date', `DE chips ${JSON.stringify(deChips)} lack "${deLabel}${fmtSrcDe}"`);
    const deExt = deChips.find((x) => x.startsWith('extrahiert ')) || null;
    if (expectExtracted !== !!deExt) fail('au-memory-source-date', `DE extracted chip ${deExt ? 'shown' : 'absent'} but diff ${diffMs}ms`);
    if (expectExtracted && deExt !== 'extrahiert ' + fmtCreatedDe) fail('au-memory-source-date', `DE extracted chip "${deExt}" != "extrahiert ${fmtCreatedDe}"`);
    const cardRule = await page.locator('#memory-list .mem-card').evaluateAll((els) => els.map((e) => {
      const src = e.getAttribute('data-source-at');
      const created = e.getAttribute('data-created-at');
      const d = src && created ? Math.abs(new Date(src) - new Date(created)) : 0;
      const shown = Array.from(e.querySelectorAll('.chip')).some((x) => /^(extracted |extrahiert )/.test(x.textContent || ''));
      return (d > 60000) !== shown ? `card src=${src} created=${created}: extracted ${shown} but diff ${d}ms` : null;
    }).filter(Boolean));
    if (cardRule.length) fail('au-memory-source-date', cardRule.join('; '));
    const dictVals = [...source.matchAll(/"mem\.extracted":\s*"([^"]*)"/g)].map((m) => m[1]);
    if (dictVals.length !== 2 || !dictVals.includes('extracted ') || !dictVals.includes('extrahiert ')) {
      fail('au-memory-source-date', `mem.extracted EN/DE dictionary: ${JSON.stringify(dictVals)}`);
    }
    if (dictVals.some((v) => /[–—;]/.test(v))) fail('au-memory-source-date', `mem.extracted value contains –, — or ;: ${JSON.stringify(dictVals)}`);
    pass('au-memory-source-date', `first #${ch.id} data-source-at=${ds} (API source_at ${ch.source_at || 'null'}), recorded chip "${fmtSrc}", extracted ${expectExtracted ? 'shown' : 'absent'} (diff ${diffMs}ms), ${extractedCards} card(s) show the extracted chip; DE "erfasst"/"extrahiert" + mem.extracted dictionary ok`);
  }

  // ── (av) Favorites in session list ─────────────────────────────────────
  {
    await view('history');
    await page.locator('#hist-tbody .fav-star').first().waitFor({ state: 'visible', timeout: 10000 });
    const star = page.locator('#hist-tbody .fav-star').first();
    const convRow = page.locator('#hist-tbody tr[data-id]').first();
    const convId = await convRow.getAttribute('data-id');
    await star.click();
    const pressed = await star.getAttribute('aria-pressed');
    if (pressed !== 'true') fail('av-fav-history', `aria-pressed="${pressed}" after click`);
    const favs = await json('/api/favorites?kind=conversation');
    const ref = (favs.favorites || []).find(f => f.ref === String(convId));
    if (!ref) fail('av-fav-history', `conversation ${convId} not in /api/favorites?kind=conversation`);
    await page.reload();
    await page.waitForLoadState('networkidle');
    await view('history');
    await page.locator('#hist-tbody .fav-star').first().waitFor({ state: 'visible', timeout: 10000 });
    const starAfter = page.locator(`#hist-tbody tr[data-id="${convId}"] .fav-star`).first();
    const symAfter = await starAfter.textContent();
    if (symAfter.trim() !== '★') fail('av-fav-history', `after reload symbol="${symAfter.trim()}" want ★`);
    await page.locator('#history-fav-only').click();
    await page.waitForLoadState('networkidle');
    await page.locator('#hist-tbody tr[data-id]').first().waitFor({ state: 'visible', timeout: 10000 });
    const rows = await page.locator('#hist-tbody tr[data-id]').count();
    const rowId = await page.locator('#hist-tbody tr[data-id]').first().getAttribute('data-id');
    if (rowId !== String(convId)) fail('av-fav-history', `fav-only shows row ${rowId}, want ${convId}`);
    if (rows !== 1) fail('av-fav-history', `fav-only shows ${rows} rows, want 1`);
    await page.locator('#history-fav-only').click();
    await del(`/api/favorites/conversation/${convId}`);
    pass('av-fav-history', `star on #${convId} → aria-pressed true, in API, ★ after reload, fav-only shows exactly 1 marked row`);
  }

  // ── (aw) Favorites in message list ─────────────────────────────────────
  {
    await view('browse');
    await page.locator('#browse-list .fav-star').first().waitFor({ state: 'visible', timeout: 10000 });
    const star = page.locator('#browse-list .fav-star').first();
    const msgRow = page.locator('#browse-list [data-msg]').first();
    const msgId = await msgRow.getAttribute('data-msg');
    await star.click();
    const pressed = await star.getAttribute('aria-pressed');
    if (pressed !== 'true') fail('aw-fav-browse', `aria-pressed="${pressed}" after click`);
    const favs = await json('/api/favorites?kind=message');
    if (!(favs.favorites || []).find(f => f.ref === String(msgId))) fail('aw-fav-browse', `message ${msgId} not in /api/favorites?kind=message`);
    await page.reload();
    await page.waitForLoadState('networkidle');
    await view('browse');
    await page.locator('#browse-list .fav-star').first().waitFor({ state: 'visible', timeout: 10000 });
    const starAfter = page.locator(`#browse-list [data-msg="${msgId}"] .fav-star`).first();
    const symAfter = await starAfter.textContent();
    if (symAfter.trim() !== '★') fail('aw-fav-browse', `after reload symbol="${symAfter.trim()}" want ★`);
    await page.locator('#browse-fav-only').click();
    await page.waitForLoadState('networkidle');
    await page.locator('#browse-list [data-msg]').first().waitFor({ state: 'visible', timeout: 10000 });
    const rows = await page.locator('#browse-list [data-msg]').count();
    const rowId = await page.locator('#browse-list [data-msg]').first().getAttribute('data-msg');
    if (rowId !== String(msgId)) fail('aw-fav-browse', `fav-only shows row ${rowId}, want ${msgId}`);
    if (rows !== 1) fail('aw-fav-browse', `fav-only shows ${rows} rows, want 1`);
    await page.locator('#browse-fav-only').click();
    await del(`/api/favorites/message/${msgId}`);
    pass('aw-fav-browse', `star on message #${msgId} → aria-pressed true, in API, ★ after reload, fav-only shows exactly 1 marked row`);
  }

  // ── (ax) Favorites in memory list ──────────────────────────────────────
  {
    await view('memory');
    await page.locator('#memory-list .fav-star').first().waitFor({ state: 'visible', timeout: 10000 });
    const star = page.locator('#memory-list .fav-star').first();
    const memCard = page.locator('#memory-list [data-mem-id]').first();
    const memId = await memCard.getAttribute('data-mem-id');
    await star.click();
    const pressed = await star.getAttribute('aria-pressed');
    if (pressed !== 'true') fail('ax-fav-memory', `aria-pressed="${pressed}" after click`);
    const favs = await json('/api/favorites?kind=memory');
    if (!(favs.favorites || []).find(f => f.ref === String(memId))) fail('ax-fav-memory', `memory ${memId} not in /api/favorites?kind=memory`);
    await page.reload();
    await page.waitForLoadState('networkidle');
    await view('memory');
    await page.locator('#memory-list .fav-star').first().waitFor({ state: 'visible', timeout: 10000 });
    const starAfter = page.locator(`#memory-list [data-mem-id="${memId}"] .fav-star`).first();
    const symAfter = await starAfter.textContent();
    if (symAfter.trim() !== '★') fail('ax-fav-memory', `after reload symbol="${symAfter.trim()}" want ★`);
    await page.locator('#memory-fav-only').click();
    await page.waitForLoadState('networkidle');
    await page.locator('#memory-list [data-mem-id]').first().waitFor({ state: 'visible', timeout: 10000 });
    const rows = await page.locator('#memory-list [data-mem-id]').count();
    const rowId = await page.locator('#memory-list [data-mem-id]').first().getAttribute('data-mem-id');
    if (rowId !== String(memId)) fail('ax-fav-memory', `fav-only shows card ${rowId}, want ${memId}`);
    if (rows !== 1) fail('ax-fav-memory', `fav-only shows ${rows} cards, want 1`);
    await page.locator('#memory-fav-only').click();
    await del(`/api/favorites/memory/${memId}`);
    pass('ax-fav-memory', `star on memory #${memId} → aria-pressed true, in API, ★ after reload, fav-only shows exactly 1 marked card`);
  }

  // ── (ay) Favorites in project list ─────────────────────────────────────
  {
    await view('projects');
    await page.locator('#projects-grid .fav-star').first().waitFor({ state: 'visible', timeout: 10000 });
    const star = page.locator('#projects-grid .fav-star').first();
    const projCard = page.locator('#projects-grid [data-project]').first();
    const projName = await projCard.getAttribute('data-project');
    await star.click();
    const pressed = await star.getAttribute('aria-pressed');
    if (pressed !== 'true') fail('ay-fav-projects', `aria-pressed="${pressed}" after click`);
    const favs = await json('/api/favorites?kind=project');
    if (!(favs.favorites || []).find(f => f.ref === projName)) fail('ay-fav-projects', `project "${projName}" not in /api/favorites?kind=project`);
    await page.reload();
    await page.waitForLoadState('networkidle');
    await view('projects');
    await page.locator('#projects-grid .fav-star').first().waitFor({ state: 'visible', timeout: 10000 });
    const starAfter = page.locator(`#projects-grid [data-project="${projName}"] .fav-star`).first();
    const symAfter = await starAfter.textContent();
    if (symAfter.trim() !== '★') fail('ay-fav-projects', `after reload symbol="${symAfter.trim()}" want ★`);
    await page.locator('#projects-fav-only').click();
    await page.waitForLoadState('networkidle');
    await page.locator('#projects-grid [data-project]').first().waitFor({ state: 'visible', timeout: 10000 });
    const rows = await page.locator('#projects-grid [data-project]').count();
    const rowName = await page.locator('#projects-grid [data-project]').first().getAttribute('data-project');
    if (rowName !== projName) fail('ay-fav-projects', `fav-only shows "${rowName}", want "${projName}"`);
    if (rows !== 1) fail('ay-fav-projects', `fav-only shows ${rows} cards, want 1`);
    await page.locator('#projects-fav-only').click();
    await del(`/api/favorites/project/${encodeURIComponent(projName)}`);
    pass('ay-fav-projects', `star on "${projName}" → aria-pressed true, in API, ★ after reload, fav-only shows exactly 1 marked card`);
  }

  // ── (az) Favorites in AI configuration (Imported) ──────────────────────
  {
    await view('assistants');
    await page.locator('#asst-src-imported').click();
    await page.waitForLoadState('networkidle');
    await page.locator('#asst-tools [data-tool]').first().click();
    await page.waitForLoadState('networkidle');
    await page.locator('#asst-list .fav-star').first().waitFor({ state: 'visible', timeout: 10000 });
    const star = page.locator('#asst-list .fav-star').first();
    const entry = page.locator('#asst-list [data-id]').first();
    const entryId = await entry.getAttribute('data-id');
    const starKind = await star.getAttribute('data-fav-kind');
    const starRef = await star.getAttribute('data-fav-ref');
    await star.click();
    const pressed = await star.getAttribute('aria-pressed');
    if (pressed !== 'true') fail('az-fav-assistants', `aria-pressed="${pressed}" after click`);
    const favs = await json('/api/favorites?kind=config');
    if (!(favs.favorites || []).find(f => f.ref === starRef)) fail('az-fav-assistants', `config ref "${starRef}" not in /api/favorites?kind=config`);
    await page.reload();
    await page.waitForLoadState('networkidle');
    await view('assistants');
    await page.locator('#asst-src-imported').click();
    await page.waitForLoadState('networkidle');
    await page.locator('#asst-tools [data-tool]').first().click();
    await page.waitForLoadState('networkidle');
    await page.locator('#asst-list .fav-star').first().waitFor({ state: 'visible', timeout: 10000 });
    const starAfter = page.locator(`#asst-list [data-fav-ref="${starRef}"]`).first();
    const symAfter = await starAfter.textContent();
    if (symAfter.trim() !== '★') fail('az-fav-assistants', `after reload symbol="${symAfter.trim()}" want ★`);
    await page.locator('#assistants-fav-only').click();
    await page.waitForLoadState('networkidle');
    await page.locator('#asst-list [data-id]').first().waitFor({ state: 'visible', timeout: 10000 });
    const rows = await page.locator('#asst-list [data-id]').count();
    if (rows !== 1) fail('az-fav-assistants', `fav-only shows ${rows} entries, want 1`);
    await page.locator('#assistants-fav-only').click();
    await del(`/api/favorites/config/${starRef}`);
    pass('az-fav-assistants', `star on config "${starRef}" → aria-pressed true, in API, ★ after reload, fav-only shows exactly 1 entry`);
  }

  // ── (ba) Favorites in search results ───────────────────────────────────
  {
    await view('browse');
    await page.locator('#browse-list .fav-star').first().waitFor({ state: 'visible', timeout: 10000 });
    const firstMsgId = await page.locator('#browse-list [data-msg]').first().getAttribute('data-msg');
    await page.locator('#browse-list .fav-star').first().click();
    await page.waitForTimeout(300);
    const favs = await json('/api/favorites?kind=message');
    const favIds = new Set((favs.favorites || []).map(f => String(f.ref)));
    if (!favIds.has(String(firstMsgId))) fail('ba-fav-search', `message ${firstMsgId} not in favorites after star click`);
    const msgData = await json(`/api/messages/${firstMsgId}`);
    const content = msgData.content || msgData.preview || '';
    const words = content.split(/\s+/).filter(w => w.length > 4);
    const searchWord = words[0] || 'the';
    await view('find');
    await page.locator('#find-q').fill(searchWord);
    await page.locator('#find-go').click();
    await page.locator('#find-results .row[data-id]').first().waitFor({ state: 'visible', timeout: 15000 });
    // The favorited message's hit must show ★
    const favHit = page.locator(`#find-results .fav-star[data-fav-ref="${firstMsgId}"]`);
    if (await favHit.count() === 0) fail('ba-fav-search', `no fav-star with data-fav-ref=${firstMsgId} in search hits`);
    const favPressed = await favHit.getAttribute('aria-pressed');
    if (favPressed !== 'true') fail('ba-fav-search', `favorited hit star aria-pressed="${favPressed}", want true`);
    // Toggle "Nur Favoriten" — only starred message hits remain
    await page.locator('#find-fav-only').click();
    await page.waitForTimeout(500);
    const hitRows = await page.locator('#find-results .row[data-id]').count();
    const hitStars = await page.locator('#find-results .fav-star[aria-pressed="true"]').count();
    if (hitRows === 0) fail('ba-fav-search', 'fav-only filter shows no hits');
    if (hitStars !== hitRows) fail('ba-fav-search', `fav-only shows ${hitRows} rows but ${hitStars} are starred`);
    await page.locator('#find-fav-only').click();
    await del(`/api/favorites/message/${firstMsgId}`);
    pass('ba-fav-search', `search "${searchWord}" shows ★ on favorite hit ref=${firstMsgId}, fav-only filter leaves only marked hits (${hitRows} rows, ${hitStars} starred)`);
  }

  // ── (bb) Favorites in detail views ─────────────────────────────────────
  {
    await view('history');
    await page.locator('#hist-tbody tr[data-id]').first().waitFor({ state: 'visible', timeout: 10000 });
    const convId = await page.locator('#hist-tbody tr[data-id]').first().getAttribute('data-id');
    await page.locator('#hist-tbody tr[data-id]').first().click();
    await page.locator('#detail-title').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');
    const detailStar = page.locator('#detail-fav-star .fav-star');
    await detailStar.waitFor({ state: 'attached', timeout: 10000 });
    const detailPressed = await detailStar.getAttribute('aria-pressed');
    const favsBefore = await json('/api/favorites?kind=conversation');
    const inApiBefore = (favsBefore.favorites || []).some(f => f.ref === String(convId));
    if ((detailPressed === 'true') !== inApiBefore) fail('bb-fav-detail', `detail star aria-pressed="${detailPressed}" but API has ${inApiBefore}`);
    await detailStar.click();
    const detailPressedAfter = await detailStar.getAttribute('aria-pressed');
    const favsAfter = await json('/api/favorites?kind=conversation');
    const inApiAfter = (favsAfter.favorites || []).some(f => f.ref === String(convId));
    if ((detailPressedAfter === 'true') !== inApiAfter) fail('bb-fav-detail', `after toggle aria-pressed="${detailPressedAfter}" but API has ${inApiAfter}`);
    const msgStar = page.locator('#detail-messages .fav-star').first();
    await msgStar.waitFor({ state: 'attached', timeout: 10000 });
    const msgPressed = await msgStar.getAttribute('aria-pressed');
    const msgId = await page.locator('#detail-messages [data-msg]').first().getAttribute('data-msg').catch(() => null)
      || await page.evaluate(() => document.querySelector('#detail-messages .fav-star')?.getAttribute('data-fav-ref'));
    const msgFavs = await json('/api/favorites?kind=message');
    const msgInApi = (msgFavs.favorites || []).some(f => f.ref === String(msgId));
    if ((msgPressed === 'true') !== msgInApi) fail('bb-fav-detail', `msg star aria-pressed="${msgPressed}" but API has ${msgInApi} for ref ${msgId}`);
    await del(`/api/favorites/conversation/${convId}`);
    if (msgInApi) await del(`/api/favorites/message/${msgId}`);
    pass('bb-fav-detail', `detail hero star matches API for conv #${convId} (toggle synced), message stars in detail match API (ref ${msgId})`);
  }

  // ── (bc) Favorites metric on home ──────────────────────────────────────
  {
    await post('/api/favorites', { kind: 'conversation', ref: String(1) });
    await post('/api/favorites', { kind: 'message', ref: String(1) });
    await view('home');
    await page.waitForLoadState('networkidle');
    const apiFavs = await json('/api/favorites');
    await page.waitForFunction(n => document.querySelector('#m-fav')?.textContent.trim() === String(n), apiFavs.count, { timeout: 10000 }).catch(() => {});
    const favCount = await page.locator('#m-fav').textContent();
    if (favCount.trim() !== String(apiFavs.count)) fail('bc-fav-home', `metric shows "${favCount.trim()}" but API count is ${apiFavs.count}`);
    await page.locator('#metric-favorites').click();
    await page.locator('#view-history').waitFor({ state: 'visible', timeout: 10000 });
    const favToggle = await page.locator('#history-fav-only').getAttribute('aria-pressed');
    if (favToggle !== 'true') fail('bc-fav-home', `after metric drill aria-pressed="${favToggle}", want true`);
    await page.reload();
    await page.waitForLoadState('networkidle');
    await page.evaluate(() => { localStorage.setItem('th-lang', 'de'); });
    await page.reload();
    await page.waitForLoadState('networkidle');
    await page.waitForFunction(() => (document.querySelector('#metric-favorites .metric-label')?.textContent || '').includes('Favoriten'), null, { timeout: 10000 }).catch(() => {});
    const deLabel = await page.locator('#metric-favorites .metric-label').textContent();
    if (!deLabel.includes('Favoriten')) fail('bc-fav-home', `DE label "${deLabel.trim()}" missing "Favoriten"`);
    await page.evaluate(() => { localStorage.setItem('th-lang', 'en'); });
    await del('/api/favorites/conversation/1');
    await del('/api/favorites/message/1');
    pass('bc-fav-home', `metric shows ${apiFavs.count} from API, drill opens history with fav-only active, DE label "${deLabel.trim()}"`);
  }

  // ── (bd) Session detail shows tool results under the Tool toggle ───────
  {
    await page.evaluate(() => { localStorage.setItem('th-lang', 'en'); });
    await page.goto(base + '/#session/18', { waitUntil: 'networkidle' });
    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('#view-detail').waitFor({ state: 'visible', timeout: 10000 });
    await page.locator('#detail-messages .msg').first().waitFor({ state: 'visible', timeout: 10000 });
    const conv = await json('/api/conversations/18');
    const shown = await page.locator('#detail-messages .msg').count();
    if (shown !== conv.messages.length) fail('bd-detail-tool-results', `${shown} of ${conv.messages.length} messages shown with all toggles on`);
    const toolBtn = page.locator('#view-detail .togglable[data-role="tool"]');
    const toolLabel = (await toolBtn.textContent()).trim();
    if (toolLabel !== 'Tool') fail('bd-detail-tool-results', `third toggle reads "${toolLabel}", want "Tool"`);
    const toolCards = page.locator('#detail-messages .msg-tool');
    if (await toolCards.count() < 1) fail('bd-detail-tool-results', 'no tool result card in session 18');
    const pill = (await toolCards.first().locator('.role-pill').textContent()).trim();
    if (pill !== 'Tool result') fail('bd-detail-tool-results', `tool result pill reads "${pill}"`);
    const startsClosed = await toolCards.first().evaluate((e) => !e.classList.contains('open'));
    if (!startsClosed) fail('bd-detail-tool-results', 'tool result does not start collapsed');
    await toolBtn.click();
    const hidden = await page.locator('#detail-messages .msg-tool').count();
    if (hidden !== 0) fail('bd-detail-tool-results', `${hidden} tool result(s) still shown with the Tool toggle off`);
    await toolBtn.click();
    pass('bd-detail-tool-results', `${shown}/${conv.messages.length} messages, toggle "Tool", pill "Tool result", starts collapsed, toggle hides it`);
  }

  // ── (be) Expand all opens every message and toggles back ───────────────
  {
    const btn = page.locator('#detail-expand');
    await btn.click();
    const total = await page.locator('#detail-messages .msg').count();
    const openAfter = await page.locator('#detail-messages .msg.open').count();
    const labelOpen = (await btn.textContent()).trim();
    if (openAfter !== total) fail('be-detail-expand-all', `${openAfter} of ${total} open after Expand all`);
    if (labelOpen !== 'Collapse all') fail('be-detail-expand-all', `button reads "${labelOpen}" after expanding`);
    await btn.click();
    const openBack = await page.locator('#detail-messages .msg.open').count();
    const labelBack = (await btn.textContent()).trim();
    if (openBack !== 0 || labelBack !== 'Expand all') fail('be-detail-expand-all', `after collapse ${openBack} open, label "${labelBack}"`);
    pass('be-detail-expand-all', `${total} open, label "Collapse all", then 0 open, label "Expand all"`);
  }

  // ── (bf) Opening a session sets #session/<id>, Back returns ────────────
  {
    await page.goto(base + '/#history', { waitUntil: 'networkidle' });
    await page.locator('#view-history').waitFor({ state: 'visible', timeout: 10000 });
    if ((await page.locator('#history-fav-only').getAttribute('aria-pressed')) === 'true') {
      await page.locator('#history-fav-only').click();
      await page.waitForLoadState('networkidle');
    }
    await page.locator('#hist-tbody tr[data-id]').first().waitFor({ state: 'visible', timeout: 10000 });
    const rowId = await page.locator('#hist-tbody tr[data-id]').first().getAttribute('data-id');
    await page.locator('#hist-tbody tr[data-id] td.td-date').first().click();
    await page.locator('#view-detail').waitFor({ state: 'visible', timeout: 10000 });
    const hash = await page.evaluate(() => location.hash);
    if (hash !== '#session/' + rowId) fail('bf-session-deeplink', `hash "${hash}", want "#session/${rowId}"`);
    await page.goBack();
    await page.locator('#view-history').waitFor({ state: 'visible', timeout: 10000 });
    pass('bf-session-deeplink', `row #${rowId} opens with ${hash}, browser Back returns to Sessions`);
  }
} finally {
  await browser.close();
}

// ─── Phase 2: empty throwaway DB (migrate only) ───────────────────────────
// Creates throughline_ux_empty in the demo postgres, migrates it, serves the
// same UI on 127.0.0.1:8797, and captures empty-state DOM/screenshots.
const EMPTY_DB = 'throughline_ux_empty';
const EMPTY_URL = process.env.THROUGHLINE_EMPTY_URL || 'http://127.0.0.1:8797';

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
