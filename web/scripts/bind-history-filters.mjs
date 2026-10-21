import fs from "fs";
const p = process.argv[2];
let s = fs.readFileSync(p, "utf8");

if (!s.includes("hist-tool-change-bound")) {
  const wire = `
  // History filter controls must reload the list (every picklist/input drills through)
  (function bindHistoryFilters() {
    const reload = () => { state.hist.offset = 0; if (typeof loadHistory === "function") loadHistory(); };
    const ids = ["hist-tool", "hist-project", "hist-sort", "hist-from", "hist-to"];
    ids.forEach((id) => {
      const el = $(id);
      if (!el || el.dataset.bound) return;
      el.dataset.bound = "1";
      el.addEventListener("change", reload);
    });
    const q = $("hist-q");
    if (q && !q.dataset.bound) {
      q.dataset.bound = "1";
      let t = null;
      q.addEventListener("input", () => {
        clearTimeout(t);
        t = setTimeout(reload, 300);
      });
    }
  })();
`;
  s = s.replace("  async function loadHistory()", wire + "\n  async function loadHistory()");
}

// Mark for future scans
if (!s.includes("hist-tool-change-bound")) {
  s = s.replace("hist-tool-change-bound", "hist-tool-change-bound");
}

fs.writeFileSync(p, s, "utf8");
console.log("bound history filters", s.includes("bindHistoryFilters"));
