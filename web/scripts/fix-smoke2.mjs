import fs from "fs";
const p = "scripts/smoke-compose-ui.mjs";
let s = fs.readFileSync(p, "utf8");
s = s.replace(
  `const input = page.locator("#find-q, input[type=search]").first();
  await input.fill("database");`,
  `await page.waitForSelector("#find-q", { timeout: 10000 });
  await page.locator("#find-q").fill("database");`
);
fs.writeFileSync(p, s, "utf8");
console.log("ok");
