import fs from "fs";

const path = process.argv[2] || "rust/crates/throughline/web/index.html";
let s = fs.readFileSync(path, "utf8");
const before = s;

// UTF-8 sequences that were decoded as Latin-1/CP1252 and re-saved
const map = {
  "â€”": "—",
  "â€“": "–",
  "â€™": "’",
  "â€˜": "‘",
  "â€œ": "“",
  "â€\u009d": "”",
  "â€\u009c": "“",
  "â€\u0099": "’",
  "â€\u0098": "‘",
  "â€\u009a": "‚",
  "â€\u009b": "‛",
  "â€\u009e": "„",
  "â€\u00a0": " ",
  "â€\u00a6": "…",
  "â€\u00b0": "°",
  "â†’": "→",
  "â‡’": "⇒",
  "â†©": "↵",
  "â†‘": "↑",
  "â†“": "↓",
  "Â·": "·",
  "Ã¤": "ä",
  "Ã¶": "ö",
  "Ã¼": "ü",
  "ÃŸ": "ß",
  "Ã©": "é",
  "Ã¨": "è",
  "Ã¥": "å",
  "â„¢": "™",
  "â‚¬": "€",
  "â—Ž": "◎",
  "â–¤": "▤",
  "â˜°": "☰",
  "âœ¦": "✦",
  "âŒ•": "⌕",
  "â–£": "▣",
  "âŒ": "⌁",
  "âš™": "⚙",
  "âŒ˜": "⌘",
  "âœ“": "✓",
  "âœ—": "✗",
  "âœ”": "✔",
  "â–¶": "▶",
  "â—†": "◆",
  "â—‡": "◇",
  "â—‰": "‰",
  "â™¥": "♥",
  "â˜…": "★",
  "â˜†": "☆",
  "â–²": "▲",
  "â–¼": "▼",
  "â—€": "◀",
  "â–·": "▸",
  "â–ª": "▪",
  "â€¢": "•",
  "â—¦": "◦",
  "â˜°": "☰",
};

let n = 0;
for (const [bad, good] of Object.entries(map)) {
  const parts = s.split(bad);
  if (parts.length > 1) {
    n += parts.length - 1;
    s = parts.join(good);
  }
}

// Catch-all: classic UTF8-as-Latin1 for U+20xx / U+00C2+ continuation
// e.g. remaining "â€X" where X is 0x80-0x9F
s = s.replace(/â€[\u0080-\u009f]/g, (m) => {
  const code = m.charCodeAt(2);
  const mapWin = {
    0x80: "€", 0x81: "‚", 0x82: "„", 0x83: "…", 0x84: "†",
    0x85: "‡", 0x86: "ˆ", 0x87: "‰", 0x88: "Š", 0x89: "‹",
    0x8a: "Œ", 0x8b: "Ž", 0x8c: "‘", 0x8d: "’", 0x8e: "“",
    0x8f: "”", 0x90: "•", 0x91: "–", 0x92: "—", 0x93: "˜",
    0x94: "™", 0x95: "š", 0x96: "›", 0x97: "œ", 0x98: "ž",
    0x99: "Ÿ", 0x9a: "¡", 0x9b: "¢", 0x9c: "£", 0x9d: "¤",
    0x9e: "¥", 0x9f: "¦",
  };
  n++;
  return mapWin[code] || "";
});

// Remaining lone "Â" before a non-breaking space or middle dot
s = s.replace(/Â([\u00a0\u00b7\u00a9\u00ae])/g, (_, c) => {
  n++;
  return c;
});

fs.writeFileSync(path, s, "utf8");
const remaining = (s.match(/â€|Ã¤|Â·|â†’|ÃŸ/g) || []).length;
console.log("file", path);
console.log("replacements", n);
console.log("changed", before !== s);
console.log("remaining_mojibake", remaining);

// Sample user-facing lines
for (const line of s.split("\n")) {
  if (/searchable, filterable|Every message|Browse the store|Local memory store/.test(line)) {
    console.log("SAMPLE:", line.trim().slice(0, 140));
  }
}
