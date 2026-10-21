import fs from "fs";
const s = fs.readFileSync(
  "C:/Users/nicet/Documents/GitHub/mkupermann/throughline/rust/crates/throughline/web/index.html",
  "utf8"
);
console.log("chart-panel", s.includes('id="timeline-chart-panel"'));
console.log("cal-panel", s.includes('id="timeline-calendar-panel"'));
console.log("setTimelineMode", (s.match(/function setTimelineMode/g) || []).length);
console.log("loadTimeline", (s.match(/async function loadTimeline/g) || []).length);
console.log("listener wired", s.includes('setTimelineMode("calendar")'));
const i = s.indexOf("function setTimelineMode");
console.log("snippet:", s.slice(i, i + 600).replace(/\s+/g, " ").slice(0, 450));
const j = s.indexOf("loadHandoff();");
console.log("boot around loadHandoff:", s.slice(Math.max(0, j - 200), j + 80).replace(/\s+/g, " ").slice(0, 280));
