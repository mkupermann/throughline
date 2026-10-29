import { chromium } from "playwright";

const BASE = "http://127.0.0.1:8788";
const results = [];
const log = (n, ok, d = "") => {
  results.push({ n, ok, d });
  console.log(`${ok ? "PASS" : "FAIL"}  ${n}${d ? " — " + d : ""}`);
};

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errs = [];
page.on("pageerror", (e) => errs.push("page: " + String(e).slice(0, 100)));
page.on("console", (m) => {
  if (m.type() === "error" && !/Failed to load resource|500|ollama|generation backend/i.test(m.text())) {
    errs.push("console: " + m.text().slice(0, 100));
  }
});

try {
  // --- Overview loads ---
  await page.goto(BASE, { waitUntil: "networkidle", timeout: 30000 });
  const title = await page.title();
  log("page loads", /Throughline/i.test(title), title);

  // SPA shell
  const hasNav = (await page.locator("nav").count()) > 0;
  log("shell/nav renders", hasNav);

  // Overview content
  await page.locator("nav button", { hasText: "Home" }).first().click().catch(() => {});
  await page.waitForTimeout(800);
  const home = await page.locator("#main").innerText();
  log("Overview metrics", /Conversations|conversations/i.test(home) && /Messages|messages/i.test(home), home.slice(0, 80).replace(/\n/g, " "));
  log("handoff or recent", /Continue where|Recent|handoff/i.test(home));

  // API-backed number present
  const hasNumbers = /\d[\d.,]*\d|\d/.test(home);
  log("live data on Overview", hasNumbers);

  // --- Find ---
  await page.locator("nav button", { hasText: "Find" }).first().click();
  await page.waitForTimeout(600);
  const findVisible = await page.locator("#view-find").isVisible();
  log("Find view opens", findVisible);

  await page.waitForSelector("#find-q", { timeout: 10000 });
  await page.locator("#find-q").fill("database");
  await page.waitForTimeout(900);
  // try submit
  const go = page.locator("#find-go, button").filter({ hasText: /search|find|go/i }).first();
  if (await go.count()) await go.click().catch(() => {});
  await page.waitForTimeout(1000);

  const findText = await page.locator("#view-find").innerText();
  log("Find runs search", findText.length > 30, findText.slice(0, 100).replace(/\n/g, " "));
  const hit =
    /database|conversation|message|match|result|session|no match|excerpt/i.test(findText);
  log("Find shows results or empty state", hit);

  // screenshot
  await page.screenshot({ path: "output/playwright/smoke-compose-ui.png" });

  log("no unexpected js errors", errs.length === 0, errs.slice(0, 2).join(" | "));
} catch (e) {
  log("runner", false, String(e).slice(0, 150));
}

await browser.close();
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
