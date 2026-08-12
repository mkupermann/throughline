import { Command } from "cmdk";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Moon, Sun, Monitor } from "lucide-react";

import { NAV } from "@/lib/nav";
import { useTheme } from "@/lib/theme";
import { carryProviders } from "@/lib/providerScope";

/**
 * ⌘K palette. For a single-user, keyboard-driven tool this is the primary
 * navigation surface — the sidebar is the discoverable fallback, not the
 * fast path.
 */
export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const [sp] = useSearchParams();
  const { setTheme, resolved } = useTheme();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const run = (fn: () => void) => {
    setOpen(false);
    fn();
  };

  return (
    <Command.Dialog
      open={open}
      onOpenChange={setOpen}
      label="Command palette"
      className="palette"
      // Escape and outside-click both close: a modal must always have a way
      // out that does not require finding a button.
    >
      <Command.Input placeholder="Jump to, or run a command…" className="palette-input" />
      <Command.List className="palette-list">
        <Command.Empty className="palette-empty">No matches.</Command.Empty>

        <Command.Group heading="Go to" className="palette-group">
          {NAV.map((item) => (
            <Command.Item
              key={item.to}
              value={`${item.label} ${item.hint}`}
              onSelect={() => run(() => navigate(carryProviders(item.to, sp)))}
              className="palette-item"
            >
              <item.icon size={15} aria-hidden />
              <span>{item.label}</span>
              <span className="palette-hint">{item.hint}</span>
              <kbd className="palette-kbd">g {item.chord}</kbd>
            </Command.Item>
          ))}
        </Command.Group>

        <Command.Group heading="Theme" className="palette-group">
          <Command.Item value="theme light" onSelect={() => run(() => setTheme("light"))} className="palette-item">
            <Sun size={15} aria-hidden />
            <span>Light</span>
            {resolved === "light" && <span className="palette-hint">current</span>}
          </Command.Item>
          <Command.Item value="theme dark" onSelect={() => run(() => setTheme("dark"))} className="palette-item">
            <Moon size={15} aria-hidden />
            <span>Dark</span>
            {resolved === "dark" && <span className="palette-hint">current</span>}
          </Command.Item>
          <Command.Item value="theme system" onSelect={() => run(() => setTheme("system"))} className="palette-item">
            <Monitor size={15} aria-hidden />
            <span>Match system</span>
          </Command.Item>
        </Command.Group>
      </Command.List>
    </Command.Dialog>
  );
}
