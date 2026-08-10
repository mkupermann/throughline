import {
  Compass,
  Search,
  ClipboardCheck,
  Cog,
  Terminal,
  type LucideIcon,
} from "lucide-react";

/**
 * Five surfaces, replacing the fourteen flat nav entries of the Streamlit
 * app. Nine of those fourteen were variations on "find something", which is
 * why /find carries view modes instead of the sidebar carrying nine links.
 */
export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Single-key chord after `g`, e.g. `g f` jumps to Find. */
  chord: string;
  hint: string;
}

export const NAV: NavItem[] = [
  { to: "/", label: "Overview", icon: Compass, chord: "o", hint: "What needs attention" },
  { to: "/find", label: "Find", icon: Search, chord: "f", hint: "Search everything" },
  { to: "/curate", label: "Curate", icon: ClipboardCheck, chord: "c", hint: "Keep memory trustworthy" },
  { to: "/operate", label: "Operate", icon: Cog, chord: "p", hint: "Pipeline and jobs" },
  { to: "/console", label: "Console", icon: Terminal, chord: "s", hint: "SQL console" },
];
