import fs from "fs";
const p = process.argv[2];
let s = fs.readFileSync(p, "utf8");

// Replace the whole loadMemory map block cleanly
const start = s.indexOf("  async function loadMemory() {");
const end = s.indexOf("  (function wireMemory()");
if (start < 0 || end < start) {
  console.log("loadMemory block not found");
  process.exit(1);
}

const block = `  async function loadMemory() {
    const el = $("memory-list");
    if (!el) return;
    el.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
    const p = new URLSearchParams();
    p.set("limit", String(state.memory.limit));
    p.set("offset", String(state.memory.offset));
    if (state.memory.q) p.set("q", state.memory.q);
    if (state.memory.category) p.set("category", state.memory.category);
    try {
      const data = await fetchJson("/api/memory?" + p.toString());
      state.memory.total = data.total || 0;
      const items = data.chunks || [];
      el.innerHTML = items.map((c) => {
        const cat = escapeHtml(c.category || "insight");
        const proj = c.project_name ? '<span class="chip">' + escapeHtml(c.project_name) + "</span>" : "";
        const date = escapeHtml(fmtDate(c.created_at));
        const preview = escapeHtml((c.content || "").slice(0, 180));
        const full = escapeHtml(c.content || "");
        return (
          '<button type="button" class="card mem-card" data-mem-id="' + c.id + '" aria-expanded="false">' +
          '<div class="card-top" style="margin:0 0 8px">' +
          '<span class="chip">' + cat + "</span>" + proj +
          '<span class="chip">#' + c.id + "</span>" +
          '<span class="chip">recorded ' + date + "</span>" +
          "</div>" +
          '<p class="mem-preview" style="-webkit-line-clamp:4;margin:0">' + preview + "</p>" +
          '<p class="mem-full" hidden style="margin:8px 0 0;white-space:pre-wrap">' + full + "</p>" +
          '<div class="card-meta"><span>Click to expand full memory</span></div></button>'
        );
      }).join("") || emptyBox("No memory yet", "Ingest agent memory to pull durable notes.");
      el.querySelectorAll(".mem-card").forEach((btn) => {
        btn.addEventListener("click", () => {
          const open = btn.getAttribute("aria-expanded") === "true";
          btn.setAttribute("aria-expanded", open ? "false" : "true");
          const prev = btn.querySelector(".mem-preview");
          const full = btn.querySelector(".mem-full");
          if (prev) prev.hidden = !open;
          if (full) full.hidden = open;
          const meta = btn.querySelector(".card-meta span");
          if (meta) meta.textContent = open ? "Click to expand full memory" : "Click to collapse";
        });
      });
      const from = data.total ? data.offset + 1 : 0;
      const to = Math.min(data.offset + items.length, data.total);
      $("memory-count").textContent =
        from.toLocaleString() + "–" + to.toLocaleString() + " of " + data.total.toLocaleString();
      const page = Math.floor(data.offset / state.memory.limit) + 1;
      const pages = Math.max(1, Math.ceil(data.total / state.memory.limit));
      $("memory-page-info").textContent = "Page " + page + " / " + pages;
      $("memory-prev").disabled = data.offset <= 0;
      $("memory-next").disabled = data.offset + state.memory.limit >= data.total;
    } catch (e) {
      el.innerHTML = emptyBox("Could not load memory", e.message);
    }
  }

`;

s = s.slice(0, start) + block + s.slice(end);
fs.writeFileSync(p, s, "utf8");
console.log("loadMemory rewritten", s.includes("mem-card"));
