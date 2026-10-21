import fs from "fs";
const p = process.argv[2];
let s = fs.readFileSync(p, "utf8");

// Make ingest-status provider rows drill into History filtered by that tool
if (!s.includes("data-ingest-tool")) {
  s = s.replace(
    `'<button class="row"><div class="row-main"><div class="row-title">' +
          escapeHtml(p.source_tool) +`,
    `'<button class="row" data-ingest-tool="' + escapeHtml(p.source_tool) + '" style="cursor:pointer;text-align:left;width:100%"><div class="row-main"><div class="row-title">' +
          escapeHtml(p.source_tool) +`
  );
}

if (!s.includes("closest(\u0022[data-ingest-tool]\u0022)")) {
  s = s.replace(
    "  document.addEventListener(\"click\", (e) => {",
    `  document.addEventListener("click", (e) => {
    const ing = e.target && e.target.closest && e.target.closest("[data-ingest-tool]");
    if (ing) {
      const tool = ing.getAttribute("data-ingest-tool") || "";
      if (tool && tool !== "unknown") {
        state.hist.tool = tool;
        state.hist.from = "";
        state.hist.to = "";
        state.hist.offset = 0;
        if ($("hist-tool")) $("hist-tool").value = tool;
        if ($("hist-from")) $("hist-from").value = "";
        if ($("hist-to")) $("hist-to").value = "";
        showView("history");
        loadHistory();
        toast("History filtered to " + tool);
      }
      return;
    }`
  );
}

// Make handoff extra decisions / questions searchable on click
if (!s.includes("data-handoff-q=")) {
  // leave as is if structure unknown
}

fs.writeFileSync(p, s, "utf8");
console.log("ingest drill", s.includes("data-ingest-tool"));
console.log("handler", s.includes("data-ingest-tool]\")"));
