import { chromium } from "playwright";

const results = [];
function log(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? " — " + detail : ""}`);
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("pageerror", (e) => log("pageerror", false, String(e).slice(0, 80)));

async function home() {
  await page.locator("nav button", { hasText: "Home" }).first().click();
  await page.waitForTimeout(400);
}

try {
  await page.goto("http://127.0.0.1:8787", { waitUntil: "networkidle" });

  // 1) Metric tiles
  const drills = ["history", "history", "memory", "find", "projects"];
  const labels = ["conv", "msg", "memory", "vectors", "projects"];
  for (let i = 0; i < drills.length; i++) {
    await home();
    await page.waitForTimeout(300);
    const tile = page.locator("[data-drill]").nth(i);
    await tile.click();
    await page.waitForTimeout(500);
    const view = drills[i];
    const visible = await page.locator(`#view-${view === "history" ? "history" : view}`).isVisible();
    log(`metric ${labels[i]} → ${view}`, visible);
  }

  // 2) Chart bar → History with date
  await home();
  await page.waitForTimeout(500);
  const bar = page.locator("#activity-chart .chart-bar-click[data-day]").nth(20);
  const day = await bar.getAttribute("data-day");
  await bar.click();
  await page.waitForTimeout(600);
  const from = await page.locator("#hist-from").inputValue();
  log("chart bar → History date", from === day, `bar=${day} from=${from}`);

  // 3) Timeline day row → History
  await page.locator("nav button", { hasText: "Timeline" }).first().click();
  await page.waitForTimeout(700);
  const trow = page.locator("#timeline-list .row[data-day]").first();
  const tday = await trow.getAttribute("data-day");
  await trow.click();
  await page.waitForTimeout(600);
  const from2 = await page.locator("#hist-from").inputValue();
  log("timeline row → History date", from2 === tday, `row=${tday} from=${from2}`);

  // 4) Tool legend chip → History with tool
  await home();
  await page.waitForTimeout(500);
  const chip = page.locator("#tool-legend [data-tool]").first();
  const tool = await chip.getAttribute("data-tool");
  await chip.click();
  await page.waitForTimeout(600);
  const toolSel = await page.locator("#hist-tool").inputValue();
  log("tool chip → History tool", toolSel === tool, `chip=${tool} sel=${toolSel}`);

  // 5) Ingest status row → History tool
  await page.locator("nav button", { hasText: "Operate" }).first().click();
  await page.waitForTimeout(800);
  const ing = page.locator("#ingest-status-body [data-ingest-tool]").first();
  if (await ing.count()) {
    const itool = await ing.getAttribute("data-ingest-tool");
    await ing.click();
    await page.waitForTimeout(600);
    const histTool = await page.locator("#hist-tool").inputValue();
    log("ingest row → History tool", histTool === itool, `ing=${itool} sel=${histTool}`);
  } else {
    log("ingest row → History tool", false, "no data-ingest-tool rows");
  }

  // 6) Handoff session button → detail
  await home();
  await page.waitForTimeout(700);
  const hand = page.locator("#handoff-brief button[data-id], #handoff-brief button.row").first();
  if (await hand.count()) {
    await hand.click();
    await page.waitForTimeout(700);
    const detail = await page.locator("#view-detail").isVisible();
    log("handoff session → detail", detail);
  } else {
    log("handoff session → detail", false, "no handoff buttons");
  }

  // 7) History row → detail
  await page.locator("nav button", { hasText: "History" }).first().click();
  await page.waitForTimeout(800);
  const hrow = page.locator("#history-list button.row, table.sessions tbody tr, #history-list .row").first();
  if (await hrow.count()) {
    await hrow.click();
    await page.waitForTimeout(700);
    log("history row → detail", await page.locator("#view-detail, #view-msg").isVisible());
  } else {
    log("history row → detail", false, "no history rows");
  }

  // 8) DATE CHECK — conversation dates not ingest dates
  await page.locator("nav button", { hasText: "History" }).first().click();
  await page.waitForTimeout(900);
  const body = await page.locator("#view-history").innerText();
  // Corpus sessions are from 2026-01 .. 2026-10; ingest stamps are "today" 2026-10-21
  const todayish = (body.match(/2026-10-21/g) || []).length;
  const older = (body.match(/2026-0[1-9]-|2026-10-1[0-9]/g) || []).length;
  log("history shows session dates not ingest date", older > 0, `older=${older} ingest-day=${todayish}`);

  // Sample a detail page date
  await page.locator("nav button", { hasText: "Home" }).first().click();
  await page.waitForTimeout(500);
  await page.locator("#home-list button").first().click();
  await page.waitForTimeout(700);
  const detailText = await page.locator("#view-detail, #view-msg").first().innerText();
  const has2026 = /2026-/.test(detailText);
  log("detail shows 2026 conversation dates", has2026, detailText.slice(0, 100).replace(/\n/g, " "));

  // 9) Calendar day click
  await page.locator("nav button", { hasText: "Timeline" }).first().click();
  await page.waitForTimeout(500);
  await page.locator("#timeline-mode-cal").click();
  await page.waitForTimeout(400);
  await page.locator(".cal-day[data-day]").nth(18).click();
  await page.waitForTimeout(400);
  log("calendar day clickable", true);
} catch (e) {
  log("runner", false, String(e).slice(0, 120));
}

await page.screenshot({ path: "output/playwright/click-audit.png" });
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) console.log("FAILURES:", failed.map((f) => f.name + ": " + f.detail).join(" | "));
process.exit(failed.length ? 1 : 0);
