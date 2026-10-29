#!/usr/bin/env node
// Accessibility pass against the real Rust UI + fictional demo database.
// Fixture guard and served-UI check mirror capture-readme.mjs / e2e-ux.mjs.
// Nine views × three theme/lang combinations, axe-core impact counts,
// plus a keyboard focus-style walk on Home. Exit 0 only when critical=0
// and serious=0 everywhere and 15/15 focus styles are visible.
import { chromium } from 'playwright';
import { AxeBuilder } from '@axe-core/playwright';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import os from 'node:os';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const base = process.env.THROUGHLINE_DEMO_URL || 'http://127.0.0.1:8796';
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw Error('Loopback demo required');

const json = async route => {
  const r = await fetch(base + route);
  if (!r.ok) throw Error(`${route}: HTTP ${r.status}`);
  return r.json();
};

// ─── Fixture guard (mirror capture-readme.mjs) ────────────────────────────
const status = await json('/api/status');
const history = await json('/api/conversations?limit=200');
const rows = history.conversations;
if (status.conversations !== 18 || status.messages !== 72 || status.memory_chunks !== 12 ||
    !Array.isArray(rows) || rows.length !== 18 ||
    !rows.every(r => ['Atlas-demo','Harbor-demo','Orchard-demo'].includes(r.project_name) &&
      r.session_id?.startsWith('d0000000-0000-4000-8000-'))) {
  throw Error('Refusing a11y: expected complete throughline-readme-v1 fixture');
}
const source = await readFile(path.join(root, 'rust/crates/throughline/web/index.html'), 'utf8');
const served = await fetch(base + '/').then(r => r.text());
const normalize = text => text.replace(/\r\n/g, '\n');
if (normalize(source) !== normalize(served)) throw Error('Served UI differs from checkout; rebuild demo image');

const VIEWS = ['home', 'history', 'browse', 'memory', 'find', 'projects', 'timeline', 'ask', 'operate'];
const COMBOS = [
  { id: 'dark/en', theme: 'dark', lang: 'en', colorScheme: 'dark', locale: 'en-GB' },
  { id: 'light/en', theme: 'light', lang: 'en', colorScheme: 'light', locale: 'en-GB' },
  { id: 'dark/de', theme: 'dark', lang: 'de', colorScheme: 'dark', locale: 'de-DE' },
];
const IMPACTS = ['critical', 'serious', 'moderate', 'minor'];

const countByImpact = violations => {
  const counts = { critical: 0, serious: 0, moderate: 0, minor: 0 };
  for (const v of violations) {
    const impact = v.impact && counts[v.impact] != null ? v.impact : 'minor';
    counts[impact] += 1;
  }
  return counts;
};

const summarise = violations => violations.map(v => ({
  id: v.id,
  impact: v.impact,
  help: v.help,
  nodes: (v.nodes || []).slice(0, 5).map(n => n.target),
}));

const browser = await chromium.launch({ channel: process.env.THROUGHLINE_SCREENSHOT_BROWSER || undefined });
const report = {
  fixture: 'docs/demo/rust/seed.sql',
  checkedAt: new Date().toISOString(),
  uiSha256: createHash('sha256').update(normalize(source)).digest('hex'),
  corpus: status,
  combinations: [],
  keyboard: null,
  totals: { critical: 0, serious: 0, moderate: 0, minor: 0 },
};

try {
  for (const combo of COMBOS) {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      deviceScaleFactor: 1,
      reducedMotion: 'reduce',
      locale: combo.locale,
      timezoneId: 'UTC',
      colorScheme: combo.colorScheme,
    });
    await context.addInitScript(({ theme, lang }) => {
      try {
        localStorage.setItem('th-theme', theme);
        localStorage.setItem('th-lang', lang);
      } catch {}
    }, { theme: combo.theme, lang: combo.lang });
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    const comboResult = { id: combo.id, theme: combo.theme, lang: combo.lang, views: [] };

    await page.goto(base);
    await page.waitForLoadState('networkidle');
    await page.locator('#m-conv').filter({ hasText: '18' }).waitFor();

    for (const view of VIEWS) {
      await page.locator(`#nav [data-view="${view}"]`).click();
      await page.locator(`#view-${view}`).waitFor({ state: 'visible' });
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(250);
      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'])
        .analyze();
      const counts = countByImpact(results.violations);
      for (const k of IMPACTS) report.totals[k] += counts[k];
      comboResult.views.push({
        view,
        counts,
        violations: summarise(results.violations),
      });
      const line = `${combo.id} ${view}: critical=${counts.critical} serious=${counts.serious} moderate=${counts.moderate} minor=${counts.minor}`;
      console.log(line);
    }
    report.combinations.push(comboResult);
    await context.close();
  }

  // ─── Keyboard walk: first 15 focusable elements on Home ────────────────
  {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      deviceScaleFactor: 1,
      reducedMotion: 'reduce',
      locale: 'en-GB',
      timezoneId: 'UTC',
      colorScheme: 'dark',
    });
    await context.addInitScript(() => {
      try {
        localStorage.setItem('th-theme', 'dark');
        localStorage.setItem('th-lang', 'en');
      } catch {}
    });
    const page = await context.newPage();
    await page.goto(base);
    await page.waitForLoadState('networkidle');
    await page.locator('#nav [data-view="home"]').click();
    await page.locator('#view-home').waitFor({ state: 'visible' });
    await page.waitForLoadState('networkidle');

    // Start from the document body so Tab walks the real tab order.
    await page.evaluate(() => { document.activeElement?.blur?.(); document.body.focus?.(); });
    const focusables = [];
    for (let i = 0; i < 15; i++) {
      await page.keyboard.press('Tab');
      const info = await page.evaluate(() => {
        const el = document.activeElement;
        if (!el || el === document.body) return null;
        const cs = getComputedStyle(el);
        const outline = cs.outlineStyle === 'none' ? 'none' : `${cs.outlineStyle} ${cs.outlineWidth} ${cs.outlineColor}`;
        const boxShadow = cs.boxShadow;
        const hasOutline = cs.outlineStyle !== 'none' && cs.outlineWidth !== '0px' && cs.outlineWidth !== '0';
        const hasShadow = boxShadow && boxShadow !== 'none';
        return {
          index: 0,
          tag: el.tagName.toLowerCase(),
          id: el.id || null,
          text: (el.textContent || '').trim().slice(0, 40),
          outline,
          boxShadow,
          visibleFocus: Boolean(hasOutline || hasShadow),
        };
      });
      if (!info) break;
      info.index = i + 1;
      focusables.push(info);
    }
    const passed = focusables.filter(f => f.visibleFocus).length;
    report.keyboard = {
      target: 15,
      walked: focusables.length,
      withVisibleFocus: passed,
      items: focusables,
    };
    console.log(`keyboard: ${passed}/${focusables.length} focusable elements with visible focus style`);
    await context.close();
  }
} finally {
  await browser.close();
}

// ─── Report + exit ────────────────────────────────────────────────────────
const tmp = process.env.TEMP || process.env.TMP || os.tmpdir();
await mkdir(tmp, { recursive: true });
const reportPath = path.join(tmp, `a11y-rust-${Date.now()}.json`);
await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');

const { critical, serious, moderate, minor } = report.totals;
console.log('');
console.log('Totals by impact (all 27 checks):');
console.log(`  critical=${critical} serious=${serious} moderate=${moderate} minor=${minor}`);
for (const combo of report.combinations) {
  for (const v of combo.views) {
    const c = v.counts;
    if (c.critical || c.serious) {
      console.log(`BLOCKER ${combo.id} ${v.view}: critical=${c.critical} serious=${c.serious}`);
      for (const viol of v.violations) {
        if (viol.impact === 'critical' || viol.impact === 'serious') {
          console.log(`  [${viol.impact}] ${viol.id}: ${viol.help}`);
          for (const t of viol.nodes) console.log(`    ${JSON.stringify(t)}`);
        }
      }
    }
  }
}
if (moderate + minor > 0) {
  console.log('');
  console.log('moderate/minor findings (not gating):');
  const seen = new Set();
  for (const combo of report.combinations) {
    for (const v of combo.views) {
      for (const viol of v.violations) {
        if (viol.impact === 'moderate' || viol.impact === 'minor') {
          const key = `${combo.id}|${v.view}|${viol.id}`;
          if (seen.has(key)) continue;
          seen.add(key);
          console.log(`  [${viol.impact}] ${combo.id} ${v.view} ${viol.id}: ${viol.help}`);
        }
      }
    }
  }
}
const kb = report.keyboard;
console.log('');
console.log(`keyboard: ${kb.withVisibleFocus}/${kb.target} with visible focus style (${kb.walked} walked)`);
console.log(`report: ${reportPath}`);
console.log(`uiSha256: ${report.uiSha256}`);

const keyboardOk = kb.walked >= 15 && kb.withVisibleFocus >= 15;
if (critical > 0 || serious > 0) {
  console.error(`FAIL: ${critical} critical, ${serious} serious axe violations`);
  process.exit(1);
}
if (!keyboardOk) {
  console.error(`FAIL: keyboard focus styles ${kb.withVisibleFocus}/${kb.target}`);
  process.exit(1);
}
console.log('PASS: 0 critical, 0 serious; keyboard focus styles visible');
process.exit(0);
