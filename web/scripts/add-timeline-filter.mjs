import fs from "fs";
const path = process.argv[2];
let s = fs.readFileSync(path, "utf8");

// --- 1) Timeline toolbar: provider filter ---
if (!s.includes("timeline-tool")) {
  s = s.replace(
    `<div class="toolbar" style="margin:12px 0">
          <div class="view-toggle" role="group" aria-label="Timeline view mode">
            <button type="button" id="timeline-mode-chart" class="active">List</button>
            <button type="button" id="timeline-mode-cal">Calendar</button>
          </div>
          <div class="meta" id="timeline-cal-label"></div>
        </div>`,
    `<div class="toolbar" style="margin:12px 0">
          <div class="view-toggle" role="group" aria-label="Timeline view mode">
            <button type="button" id="timeline-mode-chart" class="active">List</button>
            <button type="button" id="timeline-mode-cal">Calendar</button>
          </div>
          <label class="field" style="min-width:220px">
            <span class="field-label">Provider</span>
            <select id="timeline-tool" aria-label="Filter timeline by provider">
              <option value="">All providers</option>
            </select>
          </label>
          <div class="meta" id="timeline-cal-label"></div>
        </div>`
  );
}

// --- 2) loadTimeline: filter by provider + clickable rows ---
const newLoad = `
  let timelineDays = [];
  let calMonth = new Date();

  function timelineToolFilter() {
    const el = $("timeline-tool");
    return el ? el.value : "";
  }

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
    let startDow = first.getDay() - 1;
    if (startDow < 0) startDow = 6;
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const tool = timelineToolFilter();
    const byDay = new Map();
    timelineDays
      .filter((d) => !tool || (d.source_tool || "unknown") === tool)
      .forEach((d) => byDay.set(d.day, (byDay.get(d.day) || 0) + Number(d.count || 0)));

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
          list.hidden = false;
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
      // populate provider filter once
      const sel = $("timeline-tool");
      if (sel && sel.options.length <= 1) {
        const tools = Array.from(new Set(days.map((d) => d.source_tool || "unknown"))).sort();
        tools.forEach((t) => {
          const opt = document.createElement("option");
          opt.value = t;
          opt.textContent = t;
          sel.appendChild(opt);
        });
      }
      const tool = timelineToolFilter();
      const byDay = new Map();
      days
        .filter((d) => !tool || (d.source_tool || "unknown") === tool)
        .forEach((d) => {
          const k = d.day;
          if (!byDay.has(k)) byDay.set(k, { day: k, total: 0, tools: [] });
          const rec = byDay.get(k);
          rec.total += Number(d.count || 0);
          rec.tools.push((d.source_tool || "unknown") + " \\u00d7 " + d.count);
        });
      const rows = Array.from(byDay.values()).sort((a, b) => (a.day < b.day ? 1 : -1)).slice(0, 60);
      el.innerHTML = rows.map((r) =>
        '<button type="button" class="row" data-day="' + escapeHtml(r.day) + '" style="cursor:pointer;text-align:left;width:100%">' +
        '<div class="row-id">' + escapeHtml(r.day) + "</div>" +
        '<div class="card-top" style="margin:0;gap:6px">' +
        r.tools.slice(0, 4).map((t) => '<span class="chip">' + escapeHtml(t) + "</span>").join("") +
        "</div>" +
        '<div class="row-main"><div class="row-title">' + r.total + " conversations</div></div>" +
        '<div class="row-meta">Open in History filter</div></button>'
      ).join("");
      // click a day row -> History filtered to that day
      el.querySelectorAll(".row[data-day]").forEach((row) => {
        row.addEventListener("click", () => {
          const day = row.getAttribute("data-day");
          showView("history");
          if ($("hist-from")) $("hist-from").value = day;
          if ($("hist-to")) $("hist-to").value = day;
          if ($("hist-tool") && tool) $("hist-tool").value = tool;
          if (typeof loadHistory === "function") loadHistory();
          toast("History filtered to " + day + (tool ? " · " + tool : ""));
        });
      });
      renderCalendar();
    } catch (e) {
      el.innerHTML = emptyBox("Could not load timeline", e.message);
    }
  }
`;

const start = s.indexOf("  let calMonth = new Date();");
const altStart = s.indexOf("  async function loadTimeline()");
const end = s.indexOf("  async function runAsk(q)");
if (end > 0) {
  const from = start > 0 ? start : altStart;
  if (from > 0 && end > from) {
    s = s.slice(0, from) + newLoad + "\n" + s.slice(end);
  }
}

// --- 3) Wire timeline-tool change + existing cal listeners ---
if (!s.includes("timeline-tool")) {
  // already added markup; add listener
}
if (!s.includes('$("timeline-tool")')) {
  s = s.replace(
    'if ($("timeline-mode-chart"))',
    'if ($("timeline-tool")) $("timeline-tool").addEventListener("change", () => { loadTimeline(); });\n  if ($("timeline-mode-chart"))'
  );
}

fs.writeFileSync(path, s, "utf8");
console.log("timeline-tool markup", s.includes("timeline-tool"));
console.log("filter in loadTimeline", s.includes("timelineToolFilter"));
console.log("clickable day rows", s.includes("Open in History filter"));
console.log("listener", s.includes('$("timeline-tool")'));
