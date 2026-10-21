import fs from "fs";
const p = process.argv[2];
let s = fs.readFileSync(p, "utf8");
const needle = 'const sel = $("timeline-tool");';
const insert =
  'const sel = $("timeline-tool");\n      if (sel && !sel.dataset.bound) { sel.dataset.bound = "1"; sel.addEventListener("change", () => loadTimeline()); }';
if (s.includes("sel.dataset.bound")) {
  console.log("already bound");
} else if (s.includes(needle)) {
  s = s.split(needle).join(insert);
  fs.writeFileSync(p, s, "utf8");
  console.log("bound change handler");
} else {
  console.log("needle missing");
}
console.log("has bound flag", s.includes("sel.dataset.bound"));
