#!/usr/bin/env bash
#
# Installiert Claude Code's SessionStart-Hook für context_preload.py.
# Merged mit bestehenden Hooks in ~/.claude/settings.json.
#
# Usage:
#   bash install_hooks.sh         # installiert (merge)
#   bash install_hooks.sh --show  # zeigt aktuellen Hook-Status
#   bash install_hooks.sh --uninstall
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRELOAD="$SCRIPT_DIR/context_preload.py"
SETTINGS="$HOME/.claude/settings.json"
MODE="${1:-install}"

if [[ ! -f "$PRELOAD" ]]; then
  echo "FEHLER: context_preload.py nicht gefunden unter $PRELOAD" >&2
  exit 1
fi

mkdir -p "$(dirname "$SETTINGS")"
if [[ ! -f "$SETTINGS" ]]; then
  echo "{}" > "$SETTINGS"
fi

PY="$(command -v python3 || true)"
if [[ -z "$PY" ]]; then
  echo "FEHLER: python3 nicht gefunden" >&2
  exit 1
fi

HOOK_CMD="$PY $PRELOAD"

case "$MODE" in
  --show|show)
    echo "Settings: $SETTINGS"
    python3 - "$SETTINGS" <<'PY'
import json, sys
p = sys.argv[1]
try:
    s = json.load(open(p))
except Exception as e:
    print("(konnte settings.json nicht lesen:", e, ")"); sys.exit(0)
hooks = s.get("hooks", {})
print("hooks:")
print(json.dumps(hooks, indent=2, ensure_ascii=False))
PY
    exit 0
    ;;
  --uninstall|uninstall)
    python3 - "$SETTINGS" "$HOOK_CMD" <<'PY'
import json, sys
p, cmd = sys.argv[1], sys.argv[2]
s = json.load(open(p))
hooks = s.get("hooks", {})
ss = hooks.get("SessionStart", [])
new = []
removed = 0
for group in ss:
    # group format per Claude Code: {"matcher": "...", "hooks": [{"type":"command","command":"..."}]}
    inner = group.get("hooks", []) if isinstance(group, dict) else []
    inner_filtered = [h for h in inner if not (isinstance(h, dict) and h.get("command") == cmd)]
    if len(inner_filtered) != len(inner):
        removed += len(inner) - len(inner_filtered)
    if inner_filtered:
        new_group = {**group, "hooks": inner_filtered}
        new.append(new_group)
if removed == 0:
    print("Kein passender Hook gefunden, nichts entfernt.")
else:
    if new:
        hooks["SessionStart"] = new
    else:
        hooks.pop("SessionStart", None)
    s["hooks"] = hooks
    json.dump(s, open(p, "w"), indent=2, ensure_ascii=False)
    print(f"OK — {removed} Hook-Einträge entfernt.")
PY
    exit 0
    ;;
  install|"")
    ;;
  *)
    echo "Unbekannter Modus: $MODE"
    echo "Usage: $0 [install|--show|--uninstall]"
    exit 1
    ;;
esac

# Install (merge)
python3 - "$SETTINGS" "$HOOK_CMD" <<'PY'
import json, sys, shutil, os, time
p, cmd = sys.argv[1], sys.argv[2]
try:
    s = json.load(open(p))
except Exception:
    s = {}

hooks = s.setdefault("hooks", {})
ss = hooks.setdefault("SessionStart", [])

# Matcher "startup" ist Standard für SessionStart-Hooks
target_matcher = "startup"

# Gibt es schon eine Matcher-Gruppe?
group = None
for g in ss:
    if isinstance(g, dict) and g.get("matcher") == target_matcher:
        group = g
        break
if group is None:
    group = {"matcher": target_matcher, "hooks": []}
    ss.append(group)

inner = group.setdefault("hooks", [])
exists = any(isinstance(h, dict) and h.get("command") == cmd for h in inner)
if exists:
    print("Hook bereits installiert — nichts zu tun.")
else:
    inner.append({
        "type": "command",
        "command": cmd,
        "timeout": 10,
    })
    # Backup
    bak = p + f".bak.{int(time.time())}"
    try:
        shutil.copy2(p, bak)
    except Exception:
        pass
    json.dump(s, open(p, "w"), indent=2, ensure_ascii=False)
    print(f"OK — SessionStart-Hook installiert (Backup: {bak}).")

print("\nNeuer SessionStart-Block:")
print(json.dumps(hooks.get("SessionStart"), indent=2, ensure_ascii=False))
PY

cat <<EOF

Installation abgeschlossen.

Was passiert jetzt beim nächsten Claude Code Start?
  1. Hook ruft: $HOOK_CMD
  2. Script liest \$CLAUDE_PROJECT_DIR (oder pwd), holt Top-20 Memory-Chunks aus
     PostgreSQL 'claude_memory' DB und schreibt sie nach
     <project>/.claude/MEMORY_CONTEXT.md
  3. Claude Code liest diese Datei automatisch als Zusatz-Context.

Prüfen:
  bash $0 --show
  # oder: jq '.hooks.SessionStart' $SETTINGS

Deinstallieren:
  bash $0 --uninstall
EOF
