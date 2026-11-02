/**
 * E2E for the Secrets view of the Rust GUI (M24b), against the fictional
 * README demo (docs/demo/rust/seed.sql): 4 secrets, 3 kinds, 3 projects.
 * Run: GUI_URL=http://127.0.0.1:8787 node web/scripts/e2e-secrets.mjs
 *
 * No secret value is written here. The check for clear values uses the
 * shapes of the kinds, not the values.
 */
import { chromium } from "playwright";

const BASE = process.env.GUI_URL || "http://127.0.0.1:8787";
const CLEAR = [/gh[p]_[A-Za-z0-9]{30,}/, /sk-proj-[A-Za-z0-9]{20,}/, /AKIA[0-9A-Z]{16}/];

const results = [];
function log(name, ok, detail = "") {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? " — " + detail : ""}`);
}
const clearIn = (text) => CLEAR.some((re) => re.test(text));

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.setDefaultTimeout(10000);
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

const rows = () => page.locator("#secrets-list .secret-row");
const ids = async () => rows().evaluateAll((els) => els.map((e) => Number(e.dataset.secret)));
async function settle() {
  await page.waitForFunction(() => !document.querySelector("#secrets-list .skeleton"));
  await page.waitForTimeout(150);
}

try {
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.evaluate(() => { try { localStorage.removeItem("th-sort-secrets"); localStorage.setItem("th-lang", "en"); } catch {} });
  await page.reload({ waitUntil: "networkidle" });

  // 1. The nav entry opens the view, and #secrets deep-links to it.
  const nav = page.locator('#nav button[data-view="secrets"]');
  log("s1 nav entry visible", await nav.isVisible());
  await nav.click();
  await settle();
  log("s1 view open with hash", (await page.locator("#view-secrets").isVisible()) && page.url().endsWith("#secrets"));

  // 2. All four, masked only.
  log("s2 four rows", (await rows().count()) === 4, `rows=${await rows().count()}`);
  const listText = await page.locator("#view-secrets").innerText();
  log("s2 no clear value on screen", !clearIn(listText));
  log("s2 every value masked", (await page.locator("#secrets-list .secret-value").allInnerTexts()).every((v) => v.includes("•••")));

  // 3. Default sort is new to old by first sighting, and old to new reverses it.
  const desc = await ids();
  await page.selectOption("#sec-sort", "asc");
  await settle();
  const asc = await ids();
  log("s3 sort new to old, then old to new", JSON.stringify(desc) === JSON.stringify([...asc].reverse()) && desc[0] !== asc[0], `desc=${desc} asc=${asc}`);
  await page.selectOption("#sec-sort", "desc");
  await settle();

  // 4. Filter by kind.
  await page.selectOption("#sec-kind", "github_token");
  await settle();
  const kinds = await page.locator("#secrets-list .secret-row .row-id .chip").allInnerTexts();
  log("s4 kind filter", kinds.length === 2 && kinds.every((k) => k === "github_token"), kinds.join(","));
  await page.selectOption("#sec-kind", "");
  await settle();

  // 5. Filter by project.
  await page.selectOption("#sec-project", "Atlas-demo");
  await settle();
  const subs = await page.locator("#secrets-list .secret-row .row-sub").allInnerTexts();
  log("s5 project filter", subs.length === 1 && subs[0].includes("Atlas-demo"), subs.join(" | "));
  await page.selectOption("#sec-project", "");
  await settle();

  // 6. Show reveals the value of that row only, Hide masks it again.
  const first = rows().first();
  const masked = await first.locator(".secret-value").innerText();
  await first.locator("button.secret-reveal").click();
  await page.waitForFunction(() => document.querySelector("#secrets-list .secret-row button.secret-reveal")?.getAttribute("aria-pressed") === "true");
  const shown = await first.locator(".secret-value").innerText();
  const others = await rows().evaluateAll((els) => els.slice(1).map((e) => e.querySelector(".secret-value").textContent));
  log("s6 show reveals the clear value", clearIn(shown) && shown !== masked && !shown.includes("•••"));
  log("s6 other rows stay masked", others.every((v) => v.includes("•••")));
  await first.locator("button.secret-reveal").click();
  log("s6 hide masks again", (await first.locator(".secret-value").innerText()) === masked);

  // 7. The session link opens the source session.
  await first.locator(".row-sub a").click();
  await page.waitForTimeout(600);
  log("s7 session link opens the session", (await page.locator("#view-detail").isVisible()) && /#session\/\d+$/.test(page.url()));

  // 8. German labels.
  await page.goto(BASE + "/#secrets", { waitUntil: "networkidle" });
  await page.locator("#lang-select").selectOption("de");
  await settle();
  const btn = await rows().first().locator("button.secret-reveal").innerText();
  const label = await page.locator('label:has(#sec-kind) .field-label').innerText();
  log("s8 German labels", btn === "Anzeigen" && label === "Art", `button=${btn} label=${label}`);
  await page.locator("#lang-select").selectOption("en");

  // 9. On a narrow screen Show stays reachable and the chip is not cut.
  await page.setViewportSize({ width: 600, height: 900 });
  await page.goto(BASE + "/#secrets", { waitUntil: "networkidle" });
  await settle();
  const showVisible = await rows().first().locator("button.secret-reveal").isVisible();
  const chipFits = await rows().first().locator(".row-id .chip").evaluate((c) => c.scrollWidth <= c.parentElement.clientWidth + 1);
  log("s9 narrow screen keeps Show and the chip", showVisible && chipFits, `show=${showVisible} chip=${chipFits}`);
  await page.setViewportSize({ width: 1280, height: 900 });

  // 10. No page errors.
  log("s10 no page errors", errors.length === 0, errors.slice(0, 3).join(" | "));
} catch (e) {
  log("e2e runner", false, String(e));
} finally {
  await browser.close();
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
}
