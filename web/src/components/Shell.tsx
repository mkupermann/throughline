import { NavLink, Outlet, ScrollRestoration, useNavigate, useSearchParams } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import { Moon, Sun, Monitor, Command as CommandIcon, Keyboard } from "lucide-react";

import { Logo } from "@/components/Logo";
import { NAV, NAV_GROUPS } from "@/lib/nav";
import { useTheme } from "@/lib/theme";
import { carryProviders } from "@/lib/providerScope";
import { readDensity, saveDensity, type Density } from "@/lib/density";
import { CommandPalette } from "./CommandPalette";
import { ProviderBar } from "./ProviderBar";

/** `g` followed by a nav chord jumps between surfaces, carrying provider scope. */
function useGoChords(sp: URLSearchParams, enabled: boolean) {
  const navigate = useNavigate();
  const armed = useRef(false);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    const disarm = () => {
      armed.current = false;
      if (timer.current) window.clearTimeout(timer.current);
    };

    const onKey = (e: KeyboardEvent) => {
      // Never steal keys while the user is typing.
      const el = e.target as HTMLElement | null;
      if (
        el &&
        (el.tagName === "INPUT" ||
          el.tagName === "TEXTAREA" ||
          el.tagName === "SELECT" ||
          el.isContentEditable)
      ) {
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      if (armed.current) {
        const item = NAV.find((n) => n.chord === e.key.toLowerCase());
        if (item) {
          e.preventDefault();
          navigate(carryProviders(item.to, sp));
        }
        disarm();
        return;
      }
      if (e.key.toLowerCase() === "g") {
        armed.current = true;
        // A chord that never times out silently swallows the next keystroke.
        timer.current = window.setTimeout(disarm, 1200);
      }
    };

    if (!enabled) return disarm;
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      disarm();
    };
  }, [enabled, navigate, sp]);
}

function ThemeToggle() {
  const { theme, cycleTheme } = useTheme();
  const Icon = theme === "light" ? Sun : theme === "dark" ? Moon : Monitor;
  const label =
    theme === "light" ? "Light theme" : theme === "dark" ? "Dark theme" : "Matching system theme";
  return (
    <button
      type="button"
      onClick={cycleTheme}
      className="icon-button"
      // Icon-only control: the accessible name comes from aria-label, and
      // the tooltip repeats it for sighted users.
      aria-label={`${label}. Click to change.`}
      title={label}
    >
      <Icon size={16} aria-hidden />
    </button>
  );
}

function KeyboardHelp({ onClose }: { onClose: () => void }) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;

    const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable || focusable.length === 0) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if ((event.shiftKey && document.activeElement === first) || (!event.shiftKey && document.activeElement === last)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    }
  };

  return (
    <div className="keyboard-help-backdrop" data-testid="keyboard-help-backdrop" onMouseDown={onClose}>
      <div
        ref={dialogRef}
        className="keyboard-help"
        role="dialog"
        aria-modal="true"
        aria-label="Keyboard shortcuts"
        onKeyDown={onKeyDown}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="keyboard-help-header">
          <h2>Keyboard shortcuts</h2>
          <button ref={closeRef} type="button" className="icon-button" aria-label="Close keyboard shortcuts" onClick={onClose}>
            ×
          </button>
        </div>
        <dl>
          <div><dt><kbd>Cmd/Ctrl+K</kbd></dt><dd>Open the command palette</dd></div>
          {NAV.map((item) => (
            <div key={item.to}><dt><kbd>g {item.chord}</kbd></dt><dd>{item.label}</dd></div>
          ))}
        </dl>
      </div>
    </div>
  );
}

export function Shell() {
  const [sp] = useSearchParams();
  const [paletteHintSeen, setPaletteHintSeen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [density, setDensity] = useState<Density>(readDensity);
  const helpTriggerRef = useRef<HTMLButtonElement>(null);

  useGoChords(sp, !helpOpen);

  const closeHelp = () => {
    setHelpOpen(false);
    // The trigger never unmounts, so focus can return synchronously. Waiting
    // for an animation frame left keyboard users briefly at the document body
    // and made the result depend on frame timing under load.
    helpTriggerRef.current?.focus();
  };

  useEffect(() => {
    const t = window.setTimeout(() => setPaletteHintSeen(true), 6000);
    return () => window.clearTimeout(t);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.density = density;
    saveDensity(density);
  }, [density]);

  return (
    <div className="shell">
      <a href="#main" className="sr-only">
        Skip to main content
      </a>

      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Logo size={28} title={null} />
          </div>
          <div className="brand-text">
            <div className="brand-title">Throughline</div>
            <div className="brand-sub">Memory</div>
          </div>
        </div>

        <div className="nav-groups">
          {NAV_GROUPS.map((group) => (
            <nav key={group} aria-label={group}>
              <h2 className="nav-group-title">{group}</h2>
              <ul className="nav-list">
                {NAV.filter((item) => item.group === group).map((item) => (
                  <li key={item.to}>
                    <NavLink
                      to={carryProviders(item.to, sp)}
                      end={item.to === "/"}
                      className={({ isActive }) => `nav-link${isActive ? " is-active" : ""}`}
                    >
                      <item.icon size={16} aria-hidden />
                      <span>{item.label}</span>
                      <kbd className="nav-kbd">g {item.chord}</kbd>
                    </NavLink>
                  </li>
                ))}
              </ul>
            </nav>
          ))}
        </div>

        <div className="sidebar-foot">
          {!paletteHintSeen && (
            <div className="palette-nudge">
              <CommandIcon size={13} aria-hidden />
              <span>
                Press <kbd>⌘K</kbd>
              </span>
            </div>
          )}
          <ThemeToggle />
          <button ref={helpTriggerRef} type="button" className="icon-button" aria-label="Keyboard shortcuts" onClick={() => setHelpOpen(true)}>
            <Keyboard size={16} aria-hidden />
          </button>
          <div className="density-toggle" role="group" aria-label="Display density">
            {(["comfortable", "compact"] as const).map((option) => (
              <button
                key={option}
                type="button"
                aria-label={`${option[0].toUpperCase()}${option.slice(1)} density`}
                aria-pressed={density === option}
                onClick={() => setDensity(option)}
              >
                {option === "comfortable" ? "Comfortable" : "Compact"}
              </button>
            ))}
          </div>
        </div>
      </aside>

      <main id="main" className="main" tabIndex={-1}>
        <ProviderBar />
        <Outlet />
      </main>

      {/* Back must land where you left, not at the top. This works only
          because React Query serves the previous result set from cache, so
          the page has its full height on the first paint after navigating
          back — restoring scroll against a still-loading page would put you
          at 0. */}
      <ScrollRestoration />
      <CommandPalette />
      {helpOpen && <KeyboardHelp onClose={closeHelp} />}
    </div>
  );
}
