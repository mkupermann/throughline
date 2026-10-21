import fs from "fs";
const p = "C:/Users/nicet/Documents/GitHub/mkupermann/throughline/rust/crates/throughline/web/index.html";
let s = fs.readFileSync(p, "utf8");
const boot = "loadHandoff();\n  loadIngestStatus();";
const listeners = `if ($("timeline-mode-chart")) $("timeline-mode-chart").addEventListener("click", () => setTimelineMode("chart"));
  if ($("timeline-mode-cal")) $("timeline-mode-cal").addEventListener("click", () => setTimelineMode("calendar"));
  if ($("cal-prev")) $("cal-prev").addEventListener("click", () => { calMonth.setMonth(calMonth.getMonth() - 1); renderCalendar(); });
  if ($("cal-next")) $("cal-next").addEventListener("click", () => { calMonth.setMonth(calMonth.getMonth() + 1); renderCalendar(); });
  if ($("cal-today")) $("cal-today").addEventListener("click", () => { calMonth = new Date(); renderCalendar(); });
  loadHandoff();
  loadIngestStatus();`;
if (s.includes(boot) && !s.includes('setTimelineMode("calendar")')) {
  s = s.split(boot).join(listeners);
  fs.writeFileSync(p, s, "utf8");
  console.log("wired listeners");
} else {
  console.log("already?", s.includes('setTimelineMode("calendar")'), "boot?", s.includes(boot));
}
