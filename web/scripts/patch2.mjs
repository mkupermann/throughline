import fs from "fs";
const p = "scripts/full-pass.mjs";
let s = fs.readFileSync(p, "utf8");
s = s.replace("#view-detail, #view-msg", "#view-detail");
fs.writeFileSync(p, s, "utf8");
console.log("now", s.includes('home session'), s.match(/home session.*/)[0]);
