import fs from "fs";
const p = process.argv[2];
let s = fs.readFileSync(p, "utf8");

// Operate job buttons
if (!s.includes('data-job="stale"')) {
  s = s.replace(
    '<button class="job" data-job="audit"><strong>Audit</strong><span>Agent accountability</span></button>',
    '<button class="job" data-job="audit"><strong>Audit</strong><span>Agent accountability</span></button>\n          <button class="job" data-job="stale"><strong>Stale digest</strong><span>What went stale since last session</span></button>\n          <button class="job" data-job="writeback"><strong>Write to AGENTS.md</strong><span>Distilled decisions into the project file</span></button>\n          <button class="job" data-job="answer-eval"><strong>Answer eval</strong><span>Citations and hallucination check</span></button>'
  );
}

// runJob: handle new jobs via API
if (!s.includes('job === "stale"')) {
  s = s.replace(
    'if (job === "audit") {',
    `if (job === "stale") {
        const d = await fetchJson("/api/stale-digest");
        let html = '<div class="prose"><strong>' + escapeHtml(d.banner || "") + "</strong><ul>";
        (d.items || []).slice(0, 15).forEach((it) => {
          html += "<li>[" + escapeHtml(it.reason) + "] " + escapeHtml((it.content || "").slice(0, 90)) + "</li>";
        });
        html += "</ul></div>";
        el.innerHTML = html;
        toast("stale digest ready");
        return;
      }
      if (job === "writeback") {
        const w = await fetchJson("/api/writeback");
        el.innerHTML = '<div class="prose">Wrote ' + w.items_written + " items to " + escapeHtml(w.file) + "</div>";
        toast("writeback done");
        return;
      }
      if (job === "answer-eval") {
        const r = await fetchJson("/api/answer-eval");
        el.innerHTML = '<div class="prose"><strong>answer-eval</strong> passed=' + r.passed +
          "/" + (r.passed + r.failed) +
          " hallucination_rate=" + Math.round((r.hallucination_rate || 0) * 100) + "%</div>";
        toast("answer eval done");
        return;
      }
      if (job === "audit") {`
  );
}

// Health recovery on Overview
if (!s.includes("health-recovery")) {
  s = s.replace(
    '<div class="panel">\n          <div class="panel-title">\n            <h2>Continue where you left off</h2>',
    '<div class="panel" id="health-recovery" hidden>\n          <div class="panel-title"><h2>Needs attention</h2><div class="meta">one-click recovery</div></div>\n          <div id="health-recovery-body"></div>\n        </div>\n\n        <div class="panel">\n          <div class="panel-title">\n            <h2>Continue where you left off</h2>'
  );
  s = s.replace(
    "loadHandoff();",
    `loadHandoff();
  fetchJson("/api/health-recovery").then((h) => {
    const box = $("health-recovery");
    const body = $("health-recovery-body");
    if (!box || !body) return;
    if (h.healthy && (!h.actions || !h.actions.length)) { box.hidden = true; return; }
    box.hidden = false;
    body.innerHTML = (h.actions || []).map((a) =>
      '<div class="row" style="cursor:default"><div class="row-main"><div class="row-title">' +
      escapeHtml(a.label) + '</div><div class="row-meta">' + escapeHtml(a.hint || "") +
      "</div></div></div>"
    ).join("") || '<div class="empty">All systems ok</div>';
  }).catch(() => {});`
  );
}

fs.writeFileSync(p, s, "utf8");
console.log("stale job", s.includes('data-job="stale"'));
console.log("health panel", s.includes("health-recovery"));
console.log("writeback job", s.includes('data-job="writeback"'));
