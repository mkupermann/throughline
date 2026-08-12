import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";

import { projectsApi } from "@/lib/api";
import { formatCount } from "@/lib/format";

/**
 * What the last week was spent on, by project.
 *
 * The page's own question is "what needs doing, and what is in here", and this
 * is the second half of it in the form people actually think in: not 330
 * conversations, but four projects with names. Each row opens that project's
 * full history.
 *
 * A project is the working directory a session ran in — the only grouping the
 * data already carries, and the one a person recognises. Counts are of human
 * sessions; the tool's own calls are excluded here as everywhere, which is the
 * difference between "36 sessions" and a number in the thousands.
 */

const DAYS = 7;

function lastSeen(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const days = Math.floor((Date.now() - d.getTime()) / 86_400_000);
  // Relative for the recent past, which is the whole window: "3 days ago"
  // places a project against today faster than a date does.
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  return `${days} days ago`;
}

export function RecentProjects() {
  const { data, isPending } = useQuery({
    queryKey: ["projects-recent", DAYS],
    queryFn: () => projectsApi.recent(DAYS),
  });

  if (isPending) return null;

  const projects = data?.projects ?? [];
  if (projects.length === 0) {
    return (
      <section aria-labelledby="proj-h" className="stack-top">
        <h2 id="proj-h" className="section-label">
          Last {DAYS} days
        </h2>
        <p className="empty-hint">No sessions in the last {DAYS} days.</p>
      </section>
    );
  }

  return (
    <section aria-labelledby="proj-h" className="stack-top">
      <h2 id="proj-h" className="section-label">
        Last {DAYS} days
      </h2>
      <ul className="proj-list">
        {projects.map((p) => (
          <li key={p.project}>
            <Link to={`/project/${encodeURIComponent(p.project)}`} className="proj-row">
              <span className="proj-name">{p.project}</span>
              <span className="proj-stats">
                <span className="tabular">{formatCount(p.sessions)} sessions</span>
                <span className="tabular">{formatCount(p.messages)} messages</span>
                {/* Which assistants were used. A project spanning two tools is
                    the thing this product exists to make visible, and it is
                    invisible in every per-tool interface. */}
                {p.tool_names.length > 1 && (
                  <span className="proj-tools">{p.tool_names.join(" · ")}</span>
                )}
                <span className="proj-when">{lastSeen(p.last_active)}</span>
              </span>
              <ArrowRight size={14} aria-hidden className="proj-go" />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
