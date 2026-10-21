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
  const body = await page.locator("body").innerText();
  const mimo = /mimo/i.test(body);
  log("branding neutral (no Mimo)", !mimo, mimo ? "found Mimo text" : "");

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
    await job.click();
    await page.waitForTimeout(name === "backup" ? 2500 : 800);
    const out = await page.locator("#operate-out").innerText().catch(() => "");
    log(`job ${name}`, out.trim().length > 0, out.slice(0, 100).replace(/\n/g, " "));
  }
  await page.screenshot({ path: `${outDir}/operate-audit.png` });

  // Picklists / selects
  const selects = await page.locator("select").all();
  log("select elements found", selects.length > 0, `count=${selects.length}`);
  for (const sel of selects) {
    const id = (await sel.getAttribute("id")) || "(no id)";
    const options = await sel.locator("option").allTextContents();
    if (options.length > 1) {
      await sel.selectOption({ index: 1 }).catch(() => {});
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
    const findOut = await page.locator("#find-out, #find-results, #view-find").first().innerText();
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
    const askOut = await page.locator("#ask-out").innerText().catch(() => "");
    log("ask", askOut.length > 0, askOut.slice(0, 80).replace(/\n/g, " "));
    await page.screenshot({ path: `${outDir}/ask.png` });
  }

  // Palette
  await page.keyboard.press("Control+k");
  await page.waitForTimeout(300);
  const palette = await page.locator(".palette, [role=dialog]").count();
  log("command palette opens", palette > 0, `dialogs=${palette}`);
  await page.keyboard.press("Escape");

  log("console errors", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
} catch (e) {
  log("e2e runner", false, String(e));
} finally {
  await page.screenshot({ path: `${outDir}/final.png` }).catch(() => {});
  await browser.close();
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
}
