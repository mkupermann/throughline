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
  group: "Work" | "Trust" | "System" | "Project Management";
  /** Single-key chord after `g`, e.g. `g f` jumps to Find. */
  chord: string;
  description?: string;
}

export const NAV: NavItem[] = [
  { to: "/", label: "Overview", icon: Compass, group: "Work", chord: "o", description: "What needs attention" },
  { to: "/find", label: "Find", icon: Search, group: "Work", chord: "f", description: "Search everything" },
  { to: "/timeline", label: "Timeline", icon: CalendarRange, group: "Work", chord: "t", description: "Activity over time" },
  { to: "/curate", label: "Review", icon: ClipboardCheck, group: "Trust", chord: "c", description: "Keep memory trustworthy" },
  { to: "/operate", label: "Operate", icon: Cog, group: "System", chord: "p", description: "Pipeline and jobs" },
  { to: "/console", label: "Console", icon: Terminal, group: "System", chord: "s", description: "SQL console" },
  { to: "/pm", label: "Project Management", icon: Users, group: "Project Management", chord: "m", description: "Virtual team ops" },
];

export const NAV_GROUPS = ["Work", "Trust", "System", "Project Management"] as const;
