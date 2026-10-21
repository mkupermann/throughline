import fs from "fs";
const path = process.argv[2];
let s = fs.readFileSync(path, "utf8");

if (!s.includes('data-job="ingest"')) {
  s = s.replace(
    '<button class="job" data-job="status"><strong>Status</strong><span>Corpus counts</span></button>',
    '<button class="job" data-job="ingest"><strong>Ingest all providers</strong><span>Import sessions from every adapter</span></button>\n          <button class="job" data-job="status"><strong>Status</strong><span>Corpus counts</span></button>'
  );
}

if (!s.includes("ingest-status")) {
  s = s.replace(
    '<div id="operate-out"',
    '<div class="panel" id="ingest-status" style="margin-top:16px">\n          <div class="panel-title"><h2>Ingestion status</h2><div class="meta">per provider</div></div>\n          <div id="ingest-status-body" class="history list-anim">Loading…</div>\n        </div>\n        <div id="operate-out"'
  );
}

if (!s.includes("async function loadIngestStatus")) {
  const loader = `async function loadIngestStatus() {
    const el = document.getElementById("ingest-status-body");
    if (!el) return;
    try {
      const r = await fetchJson("/api/ingest-status");
      let html = "";
      (r.providers || []).forEach((p) => {
        const badge = p.adapter_present ? "on disk" : "no source dir";
        html +=
          '<button class="row"><div class="row-main"><div class="row-title">' +
          escapeHtml(p.source_tool) +
          ' <span class="chip">' + badge + '</span></div><div class="row-meta">' +
          p.conversations + " conversations · " + p.messages + " messages" +
          (p.last_session ? " · last " + escapeHtml(String(p.last_session).slice(0, 10)) : "") +
          '</div></div></button>';
      });
      html += '<div class="row-meta" style="margin-top:8px">files logged: ' + (r.total_files_logged || 0) +
        ' · totals: ' + (r.total_conversations || 0) + ' sessions / ' + (r.total_messages || 0) + ' messages</div>';
      el.innerHTML = html || '<div class="empty">No providers yet</div>';
    } catch (e) {
      el.innerHTML = emptyBox("Could not load ingest status", e.message);
    }
  }

  async function loadHandoff()`;
  s = s.replace("async function loadHandoff()", loader);
  s = s.replace("loadHandoff();", "loadHandoff();\n  loadIngestStatus();");
  s = s.replace(
    'toast(job + " finished");',
    'toast(job + " finished");\n      if (job === "ingest") loadIngestStatus();'
  );
}

fs.writeFileSync(path, s, "utf8");
console.log("ingest button", s.includes('data-job="ingest"'));
console.log("status panel", s.includes("ingest-status"));
console.log("loader", s.includes("loadIngestStatus"));
