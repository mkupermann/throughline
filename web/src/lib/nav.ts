import {
  Compass,
  MessagesSquare,
  Search,
  ClipboardCheck,
  Cog,
  Terminal,
  CalendarRange,
  Users,
  type LucideIcon,
} from "lucide-react";

import { features } from "@/lib/features";

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
  { to: "/", label: "Projects", icon: Compass, group: "Work", chord: "o", description: "Resume your projects" },
  { to: "/conversations", label: "Conversations", icon: MessagesSquare, group: "Work", chord: "v", description: "Open conversations by project" },
  { to: "/find", label: "Find", icon: Search, group: "Work", chord: "f", description: "Search everything" },
  { to: "/timeline", label: "Timeline", icon: CalendarRange, group: "Work", chord: "t", description: "Activity over time" },
  { to: "/curate", label: "Review", icon: ClipboardCheck, group: "Trust", chord: "c", description: "Keep memory trustworthy" },
  { to: "/settings/ai", label: "AI settings", icon: Cog, group: "System", chord: "a", description: "Models and services by purpose" },
  { to: "/operate", label: "Operate", icon: Cog, group: "System", chord: "p", description: "Pipeline and jobs" },
  { to: "/console", label: "Console", icon: Terminal, group: "System", chord: "s", description: "SQL console" },
  { to: "/pm", label: "AI team operations", icon: Users, group: "System", chord: "m", description: "Virtual team ops" },
];

export const NAV_GROUPS = ["Work", "Trust", "System"] as const;

export type NavGroup = (typeof NAV_GROUPS)[number];

/** Nav items the running server actually serves. PM is opt-in behind
 *  THROUGHLINE_ENABLE_PM, so a fenced-off build must not advertise it.
 *  Keyed on the route rather than the group: AI team operations sits in
 *  System alongside surfaces that are always present. */
export function visibleNav(): NavItem[] {
  return features().pm ? NAV : NAV.filter((item) => !item.to.startsWith("/pm"));
}

/** Groups with at least one visible item — an empty group heading is noise. */
export function visibleNavGroups(): NavGroup[] {
  const items = visibleNav();
  return NAV_GROUPS.filter((group) => items.some((item) => item.group === group));
}
