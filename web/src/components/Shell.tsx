import { NavLink, Outlet, ScrollRestoration, useNavigate, useSearchParams } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import { Moon, Sun, Monitor, Command as CommandIcon } from "lucide-react";

import { NAV } from "@/lib/nav";
import { useTheme } from "@/lib/theme";
import { carryProviders } from "@/lib/providerScope";
import { CommandPalette } from "./CommandPalette";
import { ProviderBar } from "./ProviderBar";

/** `g` followed by a nav chord jumps between surfaces, carrying provider scope. */
function useGoChords(sp: URLSearchParams) {
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

    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      disarm();
    };
  }, [navigate, sp]);
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

export function Shell() {
  const [sp] = useSearchParams();
  useGoChords(sp);
  const [paletteHintSeen, setPaletteHintSeen] = useState(false);

  useEffect(() => {
    const t = window.setTimeout(() => setPaletteHintSeen(true), 6000);
    return () => window.clearTimeout(t);
  }, []);

  return (
    <div className="shell">
      <a href="#main" className="sr-only">
        Skip to main content
      </a>

      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark" aria-hidden>
            T
          </div>
          <div className="brand-text">
            <div className="brand-title">Throughline</div>
            <div className="brand-sub">Memory</div>
          </div>
        </div>

        <nav aria-label="Main">
          <ul className="nav-list">
            {NAV.map((item) => (
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
    </div>
  );
}
