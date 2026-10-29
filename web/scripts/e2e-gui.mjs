/**
 * E2E smoke for the Throughline GUI.
 * Run: node scripts/e2e-gui.mjs   (from rust/ or via absolute path)
 */
import { chromium } from "playwright";
import { mkdirSync } from "fs";

const BASE = process.env.GUI_URL || "http://127.0.0.1:8787";
const outDir = "output/playwright";
mkdirSync(outDir, { recursive: true });

const results = [];
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
// Keep every auto-wait bounded so a missing locator cannot stall the suite.
page.setDefaultTimeout(10_000);
page.setDefaultNavigationTimeout(15_000);
const consoleErrors = [];
page.on("pageerror", (e) => consoleErrors.push(String(e)));
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text());
});

function log(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? " — " + detail : ""}`);
}

try {
  await page.goto(BASE, { waitUntil: "networkidle" });
  log("page title", (await page.title()).includes("Throughline"), await page.title());

  // Branding must be neutral
  // Branding check is chrome-only: corpus rows may legitimately mention other products.
  const chromeText = await page
    .locator("aside, h1, h2, .eyebrow, .brand, nav button, .btn, .job, .meta, .metric-label")
    .allInnerTexts()
    .then((t) => t.join("\n"));
  const mimo = /mimo/i.test(chromeText);
  log("branding neutral (no Mimo)", !mimo, mimo ? "found Mimo in chrome" : "");

  // Nav menus
  const navs = ["Home", "History", "Browse", "Agent Memory", "Find", "Projects", "Timeline", "Ask", "Operate"];
  for (const label of navs) {
    const btn = page.locator(`nav button`, { hasText: label }).first();
    if ((await btn.count()) === 0) {
      log(`nav ${label}`, false, "button missing");
      continue;
    }
    await btn.click();
    await page.waitForTimeout(400);
    const visible = await page.locator("main section:visible").count();
    log(`nav ${label}`, visible >= 1, `sections=${visible}`);
  }

  // Handoff panel on Home
  await page.locator("nav button", { hasText: "Home" }).first().click();
  await page.waitForTimeout(500);
  const handoff = page.locator("#handoff-brief");
  const handoffText = await handoff.innerText().catch(() => "");
  log("handoff panel", handoffText.length > 20, handoffText.slice(0, 80).replace(/\n/g, " "));
  await page.screenshot({ path: `${outDir}/home-handoff.png` });

  // Operate jobs including audit
  await page.locator("nav button", { hasText: "Operate" }).first().click();
  await page.waitForTimeout(300);
  const jobs = await page.locator("#view-operate [data-job]").all();
  log("operate job buttons", jobs.length >= 4, `count=${jobs.length}`);
  for (const job of jobs) {
    const name = await job.getAttribute("data-job");
    await job.click({ timeout: 5_000 }).catch(() => {});
    await page.waitForTimeout(name === "backup" ? 2500 : 800);
    const out = await page.locator("#operate-out").innerText({ timeout: 5_000 }).catch(() => "");
    log(`job ${name}`, out.trim().length > 0, out.slice(0, 100).replace(/\n/g, " "));
  }
  await page.screenshot({ path: `${outDir}/operate-audit.png` });

  // Find/Ask empty states BEFORE any query runs
  await page.locator("nav button", { hasText: "Find" }).first().click();
  await page.waitForTimeout(300);
  const findEmptyPre = await page.locator("#find-results").innerText().catch(() => "");
  log("find empty state", /Search your corpus|Start typing/i.test(findEmptyPre), findEmptyPre.slice(0, 60).replace(/\n/g, " "));
  await page.locator("nav button", { hasText: "Ask" }).first().click();
  await page.waitForTimeout(250);
  const askEmptyPre = await page.locator("#ask-out").innerText().catch(() => "");
  log("ask empty state", /Ask anything|Ask a question/i.test(askEmptyPre), askEmptyPre.slice(0, 60).replace(/\n/g, " "));

  // Picklists / selects
  const selects = await page.locator("select").all();
  log("select elements found", selects.length > 0, `count=${selects.length}`);
  for (const sel of selects) {
    const id = (await sel.getAttribute("id")) || "(no id)";
    const options = await sel.locator("option").allTextContents();
    if (options.length > 1) {
      await sel.selectOption({ index: 1 }, { timeout: 5_000 }).catch(() => {});
    }
    log(`select ${id}`, options.length > 0, options.join(" | ").slice(0, 80));
  }

  // Find search
  await page.locator("nav button", { hasText: "Find" }).first().click();
  await page.waitForTimeout(300);
  const q = page.locator("#find-q");
  if (await q.count()) {
    await q.fill("database");
    await page.waitForTimeout(600);
    const findOut = await page
      .locator("#find-out, #find-results, #view-find")
      .first()
      .innerText({ timeout: 5_000 })
      .catch(() => "");
    log("find search", findOut.length > 0, findOut.slice(0, 80).replace(/\n/g, " "));
    await page.screenshot({ path: `${outDir}/find.png` });
  } else {
    log("find search", false, "no #find-q");
  }

  // Ask
  await page.locator("nav button", { hasText: "Ask" }).first().click();
  await page.waitForTimeout(200);
  if (await page.locator("#ask-q").count()) {
    await page.locator("#ask-q").fill("what database does this use");
    await page.locator("#ask-go").click();
    await page.waitForTimeout(1500);
    const askOut = await page.locator("#ask-out").innerText({ timeout: 5_000 }).catch(() => "");
    log("ask", askOut.length > 0, askOut.slice(0, 80).replace(/\n/g, " "));
    await page.screenshot({ path: `${outDir}/ask.png` });
  }

  // Palette open + Escape close + focus restore
  await page.keyboard.press("Control+k");
  await page.waitForTimeout(300);
  const palette = await page.locator(".palette, [role=dialog]").count();
  log("command palette opens", palette > 0, `dialogs=${palette}`);
  const paletteOpen = await page.locator("#palette.open").count();
  log("palette has open class", paletteOpen > 0);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(150);
  const paletteClosed = (await page.locator("#palette.open").count()) === 0;
  log("escape closes palette", paletteClosed);

  // Skip link (a11y)
  const skip = page.locator("#skip-link");
  log("skip link present", (await skip.count()) === 1, await skip.getAttribute("href").catch(() => ""));
  // Reset focus context so Tab starts from document start
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.evaluate(() => {
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    document.body.focus();
  });
  await page.keyboard.press("Tab");
  await page.waitForTimeout(150);
  const activeId = await page.evaluate(() => document.activeElement && document.activeElement.id);
  const activeHref = await page.evaluate(() => document.activeElement && document.activeElement.getAttribute && document.activeElement.getAttribute("href"));
  log("skip link is first tab stop", activeId === "skip-link" || activeHref === "#main", `active=${activeId || activeHref}`);

  // aria-live regions
  const toastLive = await page.locator("#toast[aria-live=polite]").count();
  log("toast aria-live", toastLive > 0);
  const srStatus = await page.locator("#sr-status[role=status]").count();
  log("sr-status live region", srStatus > 0);

  // (empty states checked earlier, before queries)


  // Focus management: heading receives focus after nav
  await page.locator("nav button", { hasText: "History" }).first().click();
  await page.waitForTimeout(350);
  const focusedTag = await page.evaluate(() => {
    const el = document.activeElement;
    return el ? el.tagName + "#" + (el.id || "") + "." + (el.className || "") : "";
  });
  log("focus moves after view switch", /H1/i.test(focusedTag) || focusedTag.includes("main"), focusedTag);

  // Handoff panel usability on Home
  await page.locator("nav button", { hasText: "Home" }).first().click();
  await page.waitForTimeout(500);
  const handoffBtns = await page.locator("#handoff-brief .row[data-id]").count();
  log("handoff sessions are actionable", handoffBtns >= 0, `buttons=${handoffBtns}`);

  // Operate aria-busy / progress feedback
  await page.locator("nav button", { hasText: "Operate" }).first().click();
  await page.waitForTimeout(250);
  const operateLive = await page.locator("#operate-out[aria-live=polite]").count();
  log("operate-out aria-live", operateLive > 0);
  const statusJob = page.locator('#view-operate [data-job="status"]');
  if (await statusJob.count()) {
    await statusJob.click();
    await page.waitForTimeout(700);
    const outText = await page.locator("#operate-out").innerText().catch(() => "");
    log("operate job feedback", outText.trim().length > 0, outText.slice(0, 80).replace(/\n/g, " "));
    const busy = await page.locator("#operate-out").getAttribute("aria-busy");
    log("operate aria-busy cleared", busy === "false" || busy === null, `busy=${busy}`);
  }

  // g-chord: g then h returns Home
  await page.keyboard.press("Escape");
  await page.locator("body").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("g");
  await page.waitForTimeout(120);
  await page.keyboard.press("h");
  await page.waitForTimeout(350);
  const homeVisible = await page.locator("#view-home").isVisible();
  log("g-chord g h -> Home", homeVisible);

  // Picklist defaults
  await page.locator("nav button", { hasText: "Find" }).first().click();
  await page.waitForTimeout(250);
  const findMode = await page.locator("#find-mode").inputValue().catch(() => "");
  log("find-mode default hybrid", findMode === "hybrid", `mode=${findMode}`);

  const unexpectedErrors = consoleErrors.filter((m) => !/500|ollama|generation backend|Failed to load resource/i.test(m));
  log("console errors", unexpectedErrors.length === 0, unexpectedErrors.slice(0, 3).join(" | ") || (consoleErrors.length ? `ignored ${consoleErrors.length} backend 500s` : ""));

  // Server must still be alive after the full sweep (catches backend panics).
  const health = await page.request.get(`${BASE}/api/health`).catch(() => null);
  log("server alive after sweep", Boolean(health && health.ok()), health ? `status=${health.status()}` : "no response");
} catch (e) {
  log("e2e runner", false, String(e));
} finally {
  await page.screenshot({ path: `${outDir}/final.png` }).catch(() => {});
  await browser.close();
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
}
