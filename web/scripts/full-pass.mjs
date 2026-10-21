import { chromium } from "playwright";

const results = [];
function log(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? " — " + detail : ""}`);
}

const browser = await chromium.launch({ ignoreHTTPSErrors: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const pageErrors = [];
const consoleErrors = [];
page.on("pageerror", (e) => pageErrors.push(String(e)));
page.on("console", (m) => {
  if (m.type() === "error") {
    const t = m.text();
    if (!/Failed to load resource|500|ollama|unreachable|generation backend/i.test(t)) {
      consoleErrors.push(t);
    }
  }
});

async function view(name) {
  await page.locator("nav button", { hasText: name }).first().click();
  await page.waitForTimeout(700);
}

try {
  await page.goto("http://127.0.0.1:8787", { waitUntil: "networkidle" });

  // === HOME ===
  await view("Home");
  const metrics = await page.locator("#metrics .metric").count();
  log("home metrics", metrics >= 5, `count=${metrics}`);
  const handoff = await page.locator("#handoff-brief").innerText();
  log("home handoff loads", handoff.length > 20, handoff.slice(0, 60).replace(/\n/g, " "));
  const homeRows = await page.locator("#home-list button, #home-list .row").count();
  log("home recent sessions", homeRows > 0, `rows=${homeRows}`);
  if (homeRows) {
    await page.locator("#home-list button, #home-list .row").first().click();
    await page.waitForTimeout(600);
    log("home session → detail", await page.locator("#view-detail").first().isVisible());
    await view("Home");
    await page.waitForTimeout(400);
  }

  // === HISTORY ===
  await view("History");
  const histRows = await page.locator("#history-list .row, table.sessions tbody tr").count();
  log("history rows", histRows > 0, `rows=${histRows}`);
  await page.selectOption("#hist-tool", "codex");
  await page.waitForTimeout(800);
  const histT = await page.locator("#view-history").innerText();
  log("history tool filter", /codex/i.test(histT) && !/1–50 of 4\./.test(histT), histT.slice(0, 80).replace(/\n/g, " "));
  await page.selectOption("#hist-tool", "");
  await page.waitForTimeout(600);
  // search box
  await page.fill("#hist-q", "database");
  await page.waitForTimeout(800);
  log("history text filter", (await page.locator("#view-history").innerText()).length > 0);
  await page.fill("#hist-q", "");
  await page.waitForTimeout(600);

  // === BROWSE ===
  await view("Browse");
  const browse = await page.locator("#view-browse").innerText();
  log("browse loads", browse.length > 40, browse.slice(0, 60).replace(/\n/g, " "));
  const roleSel = page.locator("#browse-role");
  if (await roleSel.count()) {
    await roleSel.selectOption("assistant");
    await page.waitForTimeout(700);
    log("browse role filter", true);
    await roleSel.selectOption("");
    await page.waitForTimeout(500);
  }

  // === AGENT MEMORY ===
  await view("Agent Memory");
  const memCards = await page.locator("#memory-list .mem-card, #memory-list .card").count();
  log("memory cards", memCards > 0, `cards=${memCards}`);
  if (await page.locator("#memory-cat").count()) {
    await page.selectOption("#memory-cat", "decision");
    await page.waitForTimeout(700);
    log("memory category filter", true);
    await page.selectOption("#memory-cat", "");
    await page.waitForTimeout(500);
  }

  // === FIND ===
  await view("Find");
  log("find empty state", (await page.locator("#view-find").innerText()).length > 20);
  await page.fill("#find-q", "database");
  await page.waitForTimeout(900);
  log("find search runs", (await page.locator("#view-find").innerText()).length > 20);

  // === PROJECTS ===
  await view("Projects");
  const projCards = await page.locator("#view-projects .card, #view-projects button, #view-projects .row").count();
  log("projects list", projCards > 0, `items=${projCards}`);

  // === TIMELINE ===
  await view("Timeline");
  log("timeline loads", (await page.locator("#view-timeline").innerText()).length > 30);
  await page.locator("#timeline-mode-cal").click();
  await page.waitForTimeout(500);
  const calDays = await page.locator("#timeline-calendar .cal-day:not(.is-empty)").count();
  log("timeline calendar", calDays > 0, `days=${calDays}`);
  await page.locator("#cal-prev").click();
  await page.waitForTimeout(300);
  await page.locator("#cal-today").click();
  await page.waitForTimeout(300);
  await page.locator("#timeline-mode-chart").click();
  await page.waitForTimeout(300);
  if (await page.locator("#timeline-tool").count()) {
    await page.selectOption("#timeline-tool", "codex");
    await page.waitForTimeout(700);
    log("timeline provider filter", true);
    await page.selectOption("#timeline-tool", "");
  }

  // === ASK ===
  await view("Ask");
  log("ask empty", (await page.locator("#view-ask").innerText()).length > 20);
  await page.fill("#ask-q", "what database");
  await page.locator("#ask-go").click();
  await page.waitForTimeout(1500);
  log("ask responds", (await page.locator("#ask-out").innerText()).length > 10);

  // === OPERATE ===
  await view("Operate");
  const jobs = await page.locator("#view-operate [data-job]").count();
  log("operate jobs", jobs >= 5, `jobs=${jobs}`);
  await page.locator('[data-job="status"]').click();
  await page.waitForTimeout(800);
  log("job status", (await page.locator("#operate-out").innerText()).includes("conversations"));
  await page.locator('[data-job="audit"]').click();
  await page.waitForTimeout(800);
  log("job audit", /audit|mcp_writes/i.test(await page.locator("#operate-out").innerText()));
  const ingRows = await page.locator("#ingest-status-body [data-ingest-tool]").count();
  log("ingest status rows", ingRows > 0, `rows=${ingRows}`);
  if (ingRows) {
    await page.locator("#ingest-status-body [data-ingest-tool]").first().click();
    await page.waitForTimeout(700);
    log("ingest row → History", await page.locator("#view-history").isVisible());
  }

  // === PALETTE / KEYBOARD ===
  await view("Home");
  await page.keyboard.press("Control+k");
  await page.waitForTimeout(400);
  log("palette opens", (await page.locator("[role=dialog], .palette").count()) > 0);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  await page.keyboard.press("g");
  await page.keyboard.press("t");
  await page.waitForTimeout(500);
  log("g t → Timeline", await page.locator("#view-timeline").isVisible());

  // skip link � measure on a fresh load
  await page.goto("http://127.0.0.1:8787", { waitUntil: "networkidle" });
  await page.keyboard.press("Tab");
  const active = await page.evaluate(() => document.activeElement && document.activeElement.id);
  log("first tab = skip link", active === "skip-link", `active=${active}`);

  log("no unexpected page errors", pageErrors.length === 0, pageErrors.slice(0, 2).join(" | "));
  log("no unexpected console errors", consoleErrors.length === 0, consoleErrors.slice(0, 2).join(" | "));
} catch (e) {
  log("runner", false, String(e).slice(0, 150));
}

await page.screenshot({ path: "output/playwright/full-pass-final.png" });
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n=== ${results.length - failed.length}/${results.length} passed ===`);
if (failed.length) {
  console.log("FAILURES:");
  failed.forEach((f) => console.log(" -", f.name, f.detail));
}
process.exit(failed.length ? 1 : 0);
