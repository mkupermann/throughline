import fs from "fs";
const p = process.argv[2];
let s = fs.readFileSync(p, "utf8");

// --- metrics become buttons ---
const oldMetrics = `<div class="metrics" id="metrics">
          <div class="metric"><div class="metric-label">Conversations</div><div class="metric-value" id="m-conv">—</div><div class="metric-sub">ingested sessions</div></div>
          <div class="metric"><div class="metric-label">Messages</div><div class="metric-value" id="m-msg">—</div><div class="metric-sub">across all tools</div></div>
          <div class="metric"><div class="metric-label">Memory</div><div class="metric-value" id="m-chunk">—</div><div class="metric-sub">extracted chunks</div></div>
          <div class="metric"><div class="metric-label">Vectors</div><div class="metric-value" id="m-emb">—</div><div class="metric-sub">semantic index</div></div>
          <div class="metric"><div class="metric-label">Projects</div><div class="metric-value" id="m-proj">—</div><div class="metric-sub">workspaces seen</div></div>
        </div>`;
const newMetrics = `<div class="metrics" id="metrics">
          <button type="button" class="metric metric-click" data-drill="history" aria-label="Open conversation history">
            <div class="metric-label">Conversations</div><div class="metric-value" id="m-conv">—</div><div class="metric-sub">ingested sessions</div>
          </button>
          <button type="button" class="metric metric-click" data-drill="history" aria-label="Open full corpus history">
            <div class="metric-label">Messages</div><div class="metric-value" id="m-msg">—</div><div class="metric-sub">across all tools</div>
          </button>
          <button type="button" class="metric metric-click" data-drill="memory" aria-label="Open agent memory">
            <div class="metric-label">Memory</div><div class="metric-value" id="m-chunk">—</div><div class="metric-sub">extracted chunks</div>
          </button>
          <button type="button" class="metric metric-click" data-drill="find" aria-label="Open semantic find">
            <div class="metric-label">Vectors</div><div class="metric-value" id="m-emb">—</div><div class="metric-sub">semantic index</div>
          </button>
          <button type="button" class="metric metric-click" data-drill="projects" aria-label="Open projects">
            <div class="metric-label">Projects</div><div class="metric-value" id="m-proj">—</div><div class="metric-sub">workspaces seen</div>
          </button>
        </div>`;
if (s.includes(oldMetrics) && !s.includes("metric-click")) {
  s = s.split(oldMetrics).join(newMetrics);
}

// --- chart bars clickable (in loadActivity) ---
const oldBars = `$("activity-chart").innerHTML = series.map((s) =>
        '<div class="chart-bar" title="' + s.day + " · " + s.count +
        ' conversations" style="height:' + Math.max(3, Math.round((s.count / max) * 100)) + '%"></div>'
      ).join("");
      $("timeline-chart").innerHTML = $("activity-chart").innerHTML;`;
const newBars = `const barHtml = series.map((s) =>
        '<button type="button" class="chart-bar chart-bar-click" data-day="' + s.day +
        '" title="' + s.day + " · " + s.count + ' conversations" aria-label="' + s.day + ": " + s.count +
        ' conversations" style="height:' + Math.max(3, Math.round((s.count / max) * 100)) + '%"></button>'
      ).join("");
      $("activity-chart").innerHTML = barHtml;
      $("timeline-chart").innerHTML = barHtml;
      const wireBars = (root) => {
        root.querySelectorAll(".chart-bar[data-day]").forEach((bar) => {
          bar.addEventListener("click", () => {
            const day = bar.getAttribute("data-day");
            const tool = ($("timeline-tool") && $("timeline-tool").value) || "";
            state.hist.from = day;
            state.hist.to = day;
            state.hist.tool = tool;
            state.hist.offset = 0;
            if ($("hist-from")) $("hist-from").value = day;
            if ($("hist-to")) $("hist-to").value = day;
            if ($("hist-tool")) $("hist-tool").value = tool;
            showView("history");
            loadHistory();
            toast("History filtered to " + day + (tool ? " · " + tool : ""));
          });
        });
      };
      wireBars($("activity-chart"));
      wireBars($("timeline-chart"));`;
if (s.includes(oldBars) && !s.includes("chart-bar-click")) {
  s = s.split(oldBars).join(newBars);
}

// --- CSS for clickable metric + chart bars ---
if (!s.includes(".metric-click")) {
  s = s.replace(
    "/* Timeline calendar */",
    `/* Clickable metrics / chart bars */
.metric-click {
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: inherit;
  transition: border-color 0.15s ease, transform 0.12s ease;
}
.metric-click:hover { border-color: var(--accent, #22c55e); transform: translateY(-1px); }
.metric-click:focus-visible { outline: 2px solid var(--accent, #22c55e); outline-offset: 2px; }
.chart-bar-click {
  cursor: pointer;
  border: none;
  padding: 0;
  width: 100%;
  min-height: 3px;
  border-radius: 3px 3px 0 0;
}
.chart-bar-click:hover { filter: brightness(1.25); }
.chart-bar-click:focus-visible { outline: 2px solid var(--accent, #22c55e); outline-offset: 1px; }

/* Timeline calendar */`
  );
}

// --- wire metric tiles after loadStatus definition ---
if (!s.includes('data-drill="history"') || !s.includes("metric-click")) {
  // already handled if markup replaced
}
if (!s.includes(".metric-click").toString().length) {
  /* noop */
}
if (!s.includes("querySelectorAll(\u0022.metric-click\u0022)")) {
  s = s.replace(
    "  async function loadActivity() {",
    `  document.addEventListener("click", (e) => {
    const t = e.target && e.target.closest && e.target.closest("[data-drill]");
    if (!t) return;
    const drill = t.getAttribute("data-drill");
    if (drill === "history") {
      state.hist.from = ""; state.hist.to = ""; state.hist.tool = ""; state.hist.offset = 0;
      if ($("hist-from")) $("hist-from").value = "";
      if ($("hist-to")) $("hist-to").value = "";
      if ($("hist-tool")) $("hist-tool").value = "";
      showView("history");
      loadHistory();
    } else if (drill === "memory") {
      showView("memory");
      if (typeof loadMemory === "function") loadMemory();
    } else if (drill === "find") {
      showView("find");
    } else if (drill === "projects") {
      showView("projects");
      if (typeof loadProjects === "function") loadProjects();
    }
  });

  async function loadActivity() {`
  );
}

fs.writeFileSync(p, s, "utf8");
console.log("metric buttons", s.includes("metric-click"));
console.log("chart bars", s.includes("chart-bar-click"));
console.log("drill handler", s.includes("data-drill"));
