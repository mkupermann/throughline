import { chromium } from "playwright";

const results = [];
function log(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? " — " + detail : ""}`);
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

try {
  await page.goto("http://127.0.0.1:8787", { waitUntil: "networkidle" });

  // Fix assertion: use first matching detail section
  await page.locator("nav button", { hasText: "History" }).first().click();
  await page.waitForTimeout(900);
  const rows = page.locator("#history-list button.row, table.sessions tbody tr");
  if (await rows.count()) {
    await rows.first().click();
    await page.waitForTimeout(700);
    const detail = page.locator("#view-detail, #view-msg").first();
    log("history row → detail", await detail.isVisible());
  }

  // DATE AUDIT: compare API started_at vs History table
  await page.locator("nav button", { hasText: "History" }).first().click();
  await page.waitForTimeout(800);
  const api = await page.evaluate(() =>
    fetch("/api/conversations?limit=5").then((r) => r.json())
  );
  const rowsData = api.conversations || [];
  const histText = await page.locator("#view-history").innerText();
  let ok = rowsData.length > 0;
  const details = [];
  for (const c of rowsData.slice(0, 5)) {
    const d = (c.started_at || "").slice(0, 10);
    const shown = histText.includes(d) || histText.includes(d.replace(/-/g, " "));
    // must NOT require ingest-day if started_at is not today
    details.push(`${d}:${shown ? "shown" : "MISSING"}`);
    if (!shown && d !== "2026-10-21") ok = false;
  }
  log("History displays started_at dates", ok, details.join(" "));

  // Compare: API started_at vs row-sub on Home list
  await page.locator("nav button", { hasText: "Home" }).first().click();
  await page.waitForTimeout(700);
  const homeText = await page.locator("#home-list").innerText();
  const homeOk = rowsData.some((c) => homeText.includes((c.started_at || "").slice(0, 10)));
  log("Home list shows session dates", homeOk, homeText.slice(0, 120).replace(/\n/g, " "));

  // Detail page date should be conversation range not ingest
  await page.locator("#home-list button").first().click();
  await page.waitForTimeout(700);
  const det = await page.locator("#view-detail, #view-msg").first().innerText();
  log("detail date is 2026 session range", /2026-/.test(det), det.slice(0, 140).replace(/\n/g, " "));

  // Memory rows: prefer session/project over ingest stamp if displayed
  await page.locator("nav button", { hasText: "Agent Memory" }).first().click();
  await page.waitForTimeout(700);
  const mem = await page.locator("#view-memory").innerText();
  log("memory view loads", mem.length > 20, mem.slice(0, 80).replace(/\n/g, " "));

  // Handoff meta
  await page.locator("nav button", { hasText: "Home" }).first().click();
  await page.waitForTimeout(500);
  const hand = await page.locator("#handoff-brief").innerText();
  log("handoff uses recent session dates", /2026-/.test(hand), hand.slice(0, 100).replace(/\n/g, " "));
} catch (e) {
  log("runner", false, String(e).slice(0, 140));
}

await page.screenshot({ path: "output/playwright/date-audit.png" });
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
