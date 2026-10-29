#!/usr/bin/env node
/** Real-server regression checks. Requires the isolated seed_demo_data.py corpus. */
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';
import AxeBuilder from '@axe-core/playwright';

const base = process.env.THROUGHLINE_DEMO_URL || 'http://127.0.0.1:8795';
const output = process.env.THROUGHLINE_BROWSER_OUTPUT || '/tmp/throughline-browser-results';
const response = await fetch(`${base}/api/overview`);
assert.equal(response.status, 200);
const overview = await response.json();
assert.equal(overview.totals.conversations, 44, 'Use an isolated fictional demo database');
assert.equal(overview.totals.messages, 249);
await mkdir(output, { recursive: true });
const browser = await chromium.launch();
const results = [];
const errors = [];
try {
  for (const theme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, reducedMotion: 'reduce' });
    await context.addInitScript(theme => {
      localStorage.setItem('throughline-theme', theme);
      localStorage.setItem('pm-lang', 'en');
    }, theme);
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    for (const route of ['/project/Atlas%20(demo)', '/pm', '/pm/templates', '/operate']) {
      await page.goto(base + route);
      await page.waitForLoadState('networkidle');
      if (route.includes('/project/')) {
        await page.getByRole('heading', { name: 'Where we stand' }).waitFor();
        await page.getByRole('button', { name: 'Continue this project', exact: true }).click();
        const brief = page.getByRole('textbox', { name: 'Continuation brief' });
        await brief.waitFor();
        assert.ok((await brief.inputValue()).includes('# Continue: Atlas (demo)'));
        const downloading = page.waitForEvent('download');
        await page.getByRole('button', { name: 'Download continuation brief' }).click();
        assert.equal((await downloading).suggestedFilename(), 'throughline-continuation.md');
      }
      if (route === '/operate') {
        await page.getByText('Where is my data?', {exact:true}).click();
        await page.getByText('Actual model routes and measured speed', {exact:true}).click();
        await page.getByRole('combobox', {name:'Project',exact:true}).selectOption('Atlas (demo)');
        assert.ok(await page.getByText('Stored conversations', {exact:true}).isVisible());
      }
      if (route === '/pm/templates') {
        await page.getByRole('combobox', { name: 'Category', exact: true }).selectOption('finance');
        await page.waitForFunction(() => document.querySelectorAll('.template-card').length === 3);
        await page.locator('.template-card').first().click();
        await page.getByRole('button', { name: 'Close preview', exact: true }).waitFor();
      }
      const audit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
      results.push({ theme, route, violations: audit.violations });
      await page.screenshot({ path: `${output}/${theme}-${results.length}.png` });
      assert.deepEqual(audit.violations.map(v => ({ id: v.id, targets: v.nodes.map(n => n.target) })), [], `${theme} ${route}`);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${base}/project/Atlas%20(demo)`);
    await page.getByRole('heading', { name: 'Where we stand' }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'No horizontal overflow');
    const menu = page.getByRole('button', { name: 'Menu', exact: true });
    await menu.click();
    await page.getByRole('dialog', { name: 'Navigation' }).waitFor();
    assert.equal(await page.locator('#main').evaluate(el => el.inert), true);
    await page.keyboard.press('Escape');
    assert.equal(await menu.evaluate(el => el === document.activeElement), true, 'Menu restores keyboard focus');
    await page.screenshot({ path: `${output}/${theme}-mobile.png` });
    await context.close();
  }
  assert.deepEqual(errors, [], 'No uncaught browser errors');
} finally {
  await writeFile(`${output}/results.json`, JSON.stringify({ results, errors }, null, 2));
  await browser.close();
}
console.log(`Passed ${results.length} real-page accessibility checks and both mobile keyboard flows.`);
