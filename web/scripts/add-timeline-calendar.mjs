import fs from "fs";
const path = process.argv[2];
let s = fs.readFileSync(path, "utf8");

// 1) Timeline section: add view toggle + calendar panel
if (!s.includes("timeline-calendar")) {
  s = s.replace(
    `<div class="panel">
          <div class="chart" id="timeline-chart"></div>
        </div>
        <div class="history list-anim" id="timeline-list"></div>`,
    `<div class="toolbar" style="margin:12px 0">
          <div class="view-toggle" role="group" aria-label="Timeline view mode">
            <button type="button" id="timeline-mode-chart" class="active">List</button>
            <button type="button" id="timeline-mode-cal">Calendar</button>
          </div>
          <div class="meta" id="timeline-cal-label"></div>
        </div>
        <div class="panel" id="timeline-chart-panel">
          <div class="chart" id="timeline-chart"></div>
        </div>
        <div class="panel" id="timeline-calendar-panel" hidden>
          <div class="panel-title">
            <h2>Activity calendar</h2>
            <div style="display:flex;gap:8px;align-items:center">
              <button class="btn btn-ghost" type="button" id="cal-prev" aria-label="Previous month">&#8249;</button>
              <div class="meta" id="cal-month-label"></div>
              <button class="btn btn-ghost" type="button" id="cal-next" aria-label="Next month">&#8250;</button>
              <button class="btn btn-ghost" type="button" id="cal-today">Today</button>
            </div>
          </div>
          <div class="cal-weekdays" aria-hidden="true">
            <span>Mon</span><span>Tue</span><span>Wed</span><span>Thu</span><span>Fri</span><span>Sat</span><span>Sun</span>
          </div>
          <div class="cal-grid" id="timeline-calendar" role="grid" aria-label="Activity by day"></div>
          <div class="meta" id="cal-legend" style="margin-top:10px">
            <span class="chip">low</span>
            <span class="cal-swatch l1"></span>
            <span class="cal-swatch l2"></span>
            <span class="cal-swatch l3"></span>
            <span class="cal-swatch l4"></span>
            <span class="chip">high</span>
          </div>
        </div>
        <div class="history list-anim" id="timeline-list"></div>`
  );
}

// 2) CSS
if (!s.includes(".cal-grid")) {
  s = s.replace(
    "/* Toast */",
    `/* Timeline calendar */
.cal-weekdays {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 6px;
  margin-bottom: 6px;
  color: var(--text-3);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.cal-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 6px;
}
.cal-day {
  min-height: 72px;
  border-radius: 10px;
  border: 1px solid var(--border, #212b3c);
  background: var(--bg-inset, #0e131c);
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  cursor: pointer;
  transition: border-color 0.15s ease;
}
.cal-day:hover { border-color: var(--accent, #22c55e); }
.cal-day:focus-visible { outline: 2px solid var(--accent, #22c55e); outline-offset: 2px; }
.cal-day.is-today { border-color: var(--accent, #22c55e); }
.cal-day.is-empty {
  opacity: 0.35;
  cursor: default;
  background: transparent;
}
.cal-day-num {
  font-family: ui-monospace, monospace;
  font-size: 12px;
  color: var(--text-3);
}
.cal-day-count {
  font-family: ui-monospace, monospace;
  font-size: 16px;
  font-weight: 600;
}
.cal-day.l1 { background: rgba(34, 197, 94, 0.12); }
.cal-day.l2 { background: rgba(34, 197, 94, 0.22); }
.cal-day.l3 { background: rgba(34, 197, 94, 0.35); }
.cal-day.l4 { background: rgba(34, 197, 94, 0.5); }
.cal-swatch {
  display: inline-block;
  width: 14px;
  height: 14px;
  border-radius: 4px;
  vertical-align: middle;
}
.cal-swatch.l1 { background: rgba(34, 197, 94, 0.12); }
.cal-swatch.l2 { background: rgba(34, 197, 94, 0.22); }
.cal-swatch.l3 { background: rgba(34, 197, 94, 0.35); }
.cal-swatch.l4 { background: rgba(34, 197, 94, 0.5); }

/* Toast */`
  );
}

// 3) JS: calendar rendering + toggle
if (!s.includes("function renderCalendar")) {
  const js = `
  let calMonth = new Date();
  let timelineDays = [];

  function renderCalendar() {
    const grid = document.getElementById("timeline-calendar");
    const label = document.getElementById("cal-month-label");
    if (!grid) return;
    const year = calMonth.getFullYear();
    const month = calMonth.getMonth();
    const monthName = calMonth.toLocaleString(undefined, { month: "long", year: "numeric" });
    if (label) label.textContent = monthName;
    const tlLabel = document.getElementById("timeline-cal-label");
    if (tlLabel) tlLabel.textContent = monthName;

    const first = new Date(year, month, 1);
    // Monday-first offset
    let startDow = first.getDay() - 1;
    if (startDow < 0) startDow = 6;
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const byDay = new Map();
    timelineDays.forEach((d) => byDay.set(d.day, (byDay.get(d.day) || 0) + Number(d.count || 0)));

    const today = new Date();
    const todayStr =
      today.getFullYear() +
      "-" +
      String(today.getMonth() + 1).padStart(2, "0") +
      "-" +
      String(today.getDate()).padStart(2, "0");

    let html = "";
    for (let i = 0; i < startDow; i++) html += '<div class="cal-day is-empty" aria-hidden="true"></div>';
    for (let day = 1; day <= daysInMonth; day++) {
      const ds = year + "-" + String(month + 1).padStart(2, "0") + "-" + String(day).padStart(2, "0");
      const count = byDay.get(ds) || 0;
      let level = "";
      if (count >= 20) level = "l4";
      else if (count >= 10) level = "l3";
      else if (count >= 5) level = "l2";
      else if (count >= 1) level = "l1";
      const isToday = ds === todayStr ? " is-today" : "";
      html +=
        '<button type="button" class="cal-day ' + level + isToday + '" data-day="' + ds +
        '" aria-label="' + ds + ": " + count + ' conversations">' +
        '<span class="cal-day-num">' + day + "</span>" +
        '<span class="cal-day-count">' + (count || "") + "</span></button>";
    }
    grid.innerHTML = html;
    grid.querySelectorAll(".cal-day[data-day]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const day = btn.getAttribute("data-day");
        const list = $("timeline-list");
        if (list) {
          list.scrollIntoView({ behavior: "smooth", block: "nearest" });
          const row = list.querySelector('[data-day="' + day + '"]');
          if (row) {
            row.style.outline = "2px solid var(--accent)";
            setTimeout(() => (row.style.outline = ""), 1200);
          }
        }
      });
    });
  }

  function setTimelineMode(mode) {
    const chartPanel = $("timeline-chart-panel");
    const calPanel = $("timeline-calendar-panel");
    const list = $("timeline-list");
    const bChart = $("timeline-mode-chart");
    const bCal = $("timeline-mode-cal");
    const cal = mode === "calendar";
    if (chartPanel) chartPanel.hidden = cal;
    if (calPanel) calPanel.hidden = !cal;
    if (bChart) bChart.classList.toggle("active", !cal);
    if (bCal) bCal.classList.toggle("active", cal);
    if (cal) renderCalendar();
    if (list) list.hidden = cal;
  }

  async function loadTimeline() {
    await loadActivity();
    const el = $("timeline-list");
    el.innerHTML = '<div class="skeleton"></div>';
    try {
      const data = await fetchJson("/api/timeline");
      const days = data.days || [];
      timelineDays = days;
      const byDay = new Map();
      days.forEach((d) => {
        const k = d.day;
        if (!byDay.has(k)) byDay.set(k, { day: k, total: 0, tools: [] });
        const rec = byDay.get(k);
        rec.total += Number(d.count || 0);
        rec.tools.push((d.source_tool || "unknown") + " \\u00d7 " + d.count);
      });
      const rows = Array.from(byDay.values()).sort((a, b) => (a.day < b.day ? 1 : -1)).slice(0, 60);
      el.innerHTML = rows.map((r) =>
        '<div class="row" style="cursor:default" data-day="' + escapeHtml(r.day) + '">' +
        '<div class="row-id">' + escapeHtml(r.day) + "</div>" +
        '<div class="card-top" style="margin:0;gap:6px">' +
        r.tools.slice(0, 4).map((t) => '<span class="chip">' + escapeHtml(t) + "</span>").join("") +
        "</div>" +
        '<div class="row-main"><div class="row-title">' + r.total + " conversations</div></div>" +
        '<div class="row-meta"></div></div>'
      ).join("");
      renderCalendar();
    } catch (e) {
      el.innerHTML = emptyBox("Could not load timeline", e.message);
    }
  }
`;
  // Replace existing loadTimeline block
  const start = s.indexOf("  async function loadTimeline() {");
  const end = s.indexOf("  async function runAsk(q)", start);
  if (start > 0 && end > start) {
    s = s.slice(0, start) + js + "\n" + s.slice(end);
  }
}

// 4) Wire buttons after palette section or at boot
if (!s.includes("timeline-mode-cal")) {
  s = s.replace(
    "loadHandoff();",
    `if ($("timeline-mode-chart")) $("timeline-mode-chart").addEventListener("click", () => setTimelineMode("chart"));
  if ($("timeline-mode-cal")) $("timeline-mode-cal").addEventListener("click", () => setTimelineMode("calendar"));
  if ($("cal-prev")) $("cal-prev").addEventListener("click", () => { calMonth.setMonth(calMonth.getMonth() - 1); renderCalendar(); });
  if ($("cal-next")) $("cal-next").addEventListener("click", () => { calMonth.setMonth(calMonth.getMonth() + 1); renderCalendar(); });
  if ($("cal-today")) $("cal-today").addEventListener("click", () => { calMonth = new Date(); renderCalendar(); });
  loadHandoff();`
  );
}

fs.writeFileSync(path, s, "utf8");
console.log("calendar css", s.includes(".cal-grid"));
console.log("calendar panel", s.includes("timeline-calendar"));
console.log("renderCalendar", s.includes("function renderCalendar"));
console.log("mode toggle", s.includes("timeline-mode-cal"));
