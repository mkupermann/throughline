#!/usr/bin/env node
/**
 * Capture the documentation screenshots.
 *
 * The previous set was taken by hand with Cmd+Shift+4, which is why it survived
 * two UI rewrites: nobody was going to redo eleven images by hand, so the docs
 * kept showing a Streamlit app that no longer existed. This script exists so
 * regenerating them costs one command.
 *
 * It never points at your real database. It expects a server already running
 * against the demo fixture — see docs/screenshots/README.md for the two
 * commands that start one — and it fails loudly rather than quietly
 * photographing whatever happens to be on the port.
 *
 *   node scripts/capture_screenshots.mjs [--url http://127.0.0.1:8791] [--out docs/screenshots]
 */
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const args = process.argv.slice(2);
const argOf = (flag, fallback) => {
  const i = args.indexOf(flag);
  return i === -1 ? fallback : args[i + 1];
};

const BASE = argOf("--url", "http://127.0.0.1:8791").replace(/\/$/, "");
const OUT = argOf("--out", "docs/screenshots");

// 1440x900 at 2x. The width is the narrowest desktop the layout is designed
// for, so every screenshot shows the layout under pressure rather than a
// generously spaced 2560px version nobody's laptop reproduces.
const VIEWPORT = { width: 1440, height: 900 };
const SCALE = 2;

/** Pages to capture. `prepare` runs after load, before the shutter. */
const SHOTS = [
  {
    name: "overview",
    path: "/",
    waitFor: "text=Needs attention",
    caption: "the worklist and the last seven days by project",
  },
  {
    name: "project",
    path: "/project/acme-web",
    waitFor: "text=acme-web",
    caption: "one project's sessions, sortable and searchable",
  },
  {
    name: "find",
    path: "/find?q=index",
    waitFor: "input",
    caption: "one query across conversations, messages, memory, skills and prompts at once",
  },
  {
    name: "ask",
    path: "/find?q=Why%20did%20we%20choose%20Iceberg%20over%20Delta%20Lake%3F&mode=ask",
    // The answer is generated live by whatever model the machine has, so this
    // one is slower than the rest and its wording will differ between runs.
    // That is the honest thing to photograph: a canned answer would be a
    // screenshot of a feature that does not exist.
    waitFor: "text=Cited records",
    settle: 45_000,
    caption: "a cited answer assembled from your own records",
  },
  {
    name: "timeline",
    path: "/timeline",
    waitFor: "text=Timeline",
    caption: "activity per day across every tool",
  },
  {
    name: "curate",
    path: "/curate",
    waitFor: "text=Curate",
    caption: "the queues that keep memory trustworthy",
  },
  {
    name: "operate",
    path: "/operate",
    waitFor: "text=Operate",
    caption: "pipeline state and the jobs that change it",
  },
  {
    name: "console",
    path: "/console",
    waitFor: "textarea, .cm-content",
    caption: "read-only SQL",
    prepare: async (page) => {
      const editor = page.locator("textarea, .cm-content").first();
      await editor.click();
      await editor.fill(
        "SELECT source_tool, count(*) AS sessions, sum(message_count) AS messages\n" +
          "FROM conversations\nGROUP BY source_tool\nORDER BY sessions DESC",
      );
      const run = page.getByRole("button", { name: /run/i }).first();
      if (await run.count()) {
        await run.click();
        await page.waitForTimeout(1200);
      }
    },
  },
];

const failures = [];

const run = async () => {
  await mkdir(OUT, { recursive: true });

  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: SCALE,
    colorScheme: "dark",
  });

  // Force the dark theme explicitly. `colorScheme` alone only covers the
  // "system" case, and a screenshot run must not depend on what the machine
  // taking it happens to prefer.
  await context.addInitScript(() => {
    try {
      localStorage.setItem("throughline-theme", "dark");
    } catch {}
  });

  const page = await context.newPage();
  page.on("pageerror", (e) => failures.push(`page error: ${e.message}`));

  for (const shot of SHOTS) {
    const url = `${BASE}${shot.path}`;
    process.stdout.write(`  ${shot.name.padEnd(10)} ${url}\n`);
    try {
      const res = await page.goto(url, { waitUntil: "networkidle", timeout: 30_000 });
      if (res && res.status() >= 400) throw new Error(`HTTP ${res.status()}`);
      if (shot.waitFor) {
        await page.locator(shot.waitFor).first().waitFor({ timeout: shot.settle ?? 15_000 });
      }
      if (shot.prepare) await shot.prepare(page);
      // Let charts finish their entrance animation; a screenshot caught
      // mid-transition shows half-drawn bars.
      await page.waitForTimeout(900);
      // Full page, not just the viewport: several of these surfaces are taller
      // than 900px, and a reader looking at a documentation screenshot wants
      // the whole page, not the fold. The viewport still governs the layout,
      // so the result is the narrow-desktop rendering, uncropped.
      await page.screenshot({
        path: path.join(OUT, `${shot.name}.png`),
        fullPage: shot.fullPage !== false,
      });
    } catch (err) {
      failures.push(`${shot.name}: ${err.message}`);
      // Capture it anyway — a wrong screenshot is diagnosable, a missing one
      // just leaves you guessing which selector moved.
      await page
        .screenshot({ path: path.join(OUT, `${shot.name}.FAILED.png`) })
        .catch(() => {});
    }
  }

  await browser.close();

  if (failures.length) {
    console.error("\nFailures:");
    for (const f of failures) console.error(`  - ${f}`);
    process.exit(1);
  }
  console.log(`\n${SHOTS.length} screenshots written to ${OUT}/`);
};

run().catch((e) => {
  console.error(e);
  process.exit(1);
});
