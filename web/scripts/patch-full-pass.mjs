import fs from "fs";
const p = "scripts/full-pass.mjs";
let s = fs.readFileSync(p, "utf8");
s = s.replace(
  `log("home session ? detail", await page.locator("#view-detail, #view-msg").first().isVisible());`,
  `log("home session ? detail", await page.locator("#view-detail").isVisible());`
);
s = s.replace(
  `// skip link
  await page.keyboard.press("Tab");`,
  `// skip link — measure on a fresh load
  await page.goto("http://127.0.0.1:8787", { waitUntil: "networkidle" });
  await page.keyboard.press("Tab");`
);
fs.writeFileSync(p, s, "utf8");
console.log("patched");
