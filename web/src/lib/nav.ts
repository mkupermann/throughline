import {
  Compass,
  Search,
  ClipboardCheck,
  Cog,
  Terminal,
  CalendarRange,
  Users,
  type LucideIcon,
} from "lucide-react";

/**
 * Six surfaces, replacing the fourteen flat nav entries of the Streamlit
 * app. Nine of those fourteen were variations on "find something", which is
 * why /find carries view modes instead of the sidebar carrying nine links.
 * Timeline is its own surface rather than a Find view mode (spec §5.4) — it
 * covers a date range, not a page of search results.
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
  { to: "/timeline", label: "Timeline", icon: CalendarRange, chord: "t", hint: "Activity over time" },
  { to: "/curate", label: "Curate", icon: ClipboardCheck, chord: "c", hint: "Keep memory trustworthy" },
  { to: "/operate", label: "Operate", icon: Cog, chord: "p", hint: "Pipeline and jobs" },
  { to: "/console", label: "Console", icon: Terminal, chord: "s", hint: "SQL console" },
  { to: "/pm", label: "Team", icon: Users, chord: "m", hint: "Virtual team ops" },
];
