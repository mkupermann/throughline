import fs from "fs";
const p = "scripts/smoke-compose-ui.mjs";
let s = fs.readFileSync(p, "utf8");
s = s.replace('locator("#main, body").innerText()', 'locator("#main").innerText()');
fs.writeFileSync(p, s, "utf8");
console.log("patched");
