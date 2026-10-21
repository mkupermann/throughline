import fs from "fs";
const p = process.argv[2];
let s = fs.readFileSync(p, "utf8");
const old = 'const reload = () => { state.hist.offset = 0; if (typeof loadHistory === "function") loadHistory(); };';
const neu = `const reload = () => {
      if ($("hist-q")) state.hist.q = $("hist-q").value.trim();
      if ($("hist-tool")) state.hist.tool = $("hist-tool").value;
      if ($("hist-project")) state.hist.project = $("hist-project").value;
      if ($("hist-from")) state.hist.from = $("hist-from").value;
      if ($("hist-to")) state.hist.to = $("hist-to").value;
      if ($("hist-sort")) {
        const v = $("hist-sort").value;
        if (v === "recent") { state.hist.sortField = "started_at"; state.hist.sortDir = "desc"; }
        else if (v === "old") { state.hist.sortField = "started_at"; state.hist.sortDir = "asc"; }
        else if (v === "messages") { state.hist.sortField = "message_count"; state.hist.sortDir = "desc"; }
      }
      state.hist.offset = 0;
      if (typeof loadHistory === "function") loadHistory();
    };`;
if (s.includes(old)) {
  s = s.split(old).join(neu);
  fs.writeFileSync(p, s, "utf8");
  console.log("reload fixed");
} else {
  console.log("not found");
}
