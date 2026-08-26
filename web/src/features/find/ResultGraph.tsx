import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationNodeDatum,
} from "d3-force";
import { Link, useNavigate } from "react-router-dom";
import { Network } from "lucide-react";

import { findApi, type FindItem } from "@/lib/api";

/**
 * Entity graph induced by the current result set.
 *
 * Never the whole graph — that is the point. The old Knowledge Graph page
 * rendered every entity and then offered filters to cut it down, which is
 * unreadable at any real size and answers a question nobody asked. Here the
 * nodes are the entities *these* results mention, and the edges are only
 * those between them.
 *
 * Rendered as SVG driven by d3-force rather than a graph library: the node
 * count is bounded by construction, so a full canvas engine would be ~400 kB
 * to draw at most a few dozen circles that must also honour the theme tokens.
 */
interface GraphNode extends SimulationNodeDatum {
  id: number;
  name: string;
  entity_type: string;
  mention_count: number;
  hits_in_results: number;
}

interface GraphEdge {
  source: number | GraphNode;
  target: number | GraphNode;
  relation_type: string;
}

const TYPE_VAR: Record<string, string> = {
  person: "var(--chart-2)",
  project: "var(--chart-5)",
  technology: "var(--chart-4)",
  decision: "var(--chart-3)",
  concept: "var(--chart-1)",
  organization: "var(--chart-6)",
};
const FALLBACK = "var(--chart-other)";

const W = 720;
const H = 420;

export function ResultGraph({ items }: { items: FindItem[] }) {
  // entity_mentions keys on the *source* record. Messages are attributed to
  // their conversation, which is how extraction records them.
  const sources = useMemo(() => {
    const seen = new Set<string>();
    const out: [string, number][] = [];
    for (const it of items) {
      const pair: [string, number] =
        it.kind === "message" && it.conversation_id
          ? ["conversation", it.conversation_id]
          : [it.kind, it.id];
      const key = `${pair[0]}:${pair[1]}`;
      if (!seen.has(key)) {
        seen.add(key);
        out.push(pair);
      }
    }
    return out.slice(0, 1000);
  }, [items]);

  const { data, isPending } = useQuery({
    queryKey: ["graph", sources],
    queryFn: () => findApi.graph(sources),
    enabled: sources.length > 0,
  });

  const [hover, setHover] = useState<GraphNode | null>(null);
  const navigate = useNavigate();

  /**
   * Layout is computed synchronously, once, rather than animated.
   *
   * d3-force normally drives its ticks from requestAnimationFrame, which the
   * browser suspends in a background tab — so a graph opened in a tab that
   * loses focus stays piled at the origin and never recovers. Ticking the
   * simulation manually removes that dependency entirely, makes the layout
   * deterministic for a given result set, and costs a few milliseconds at
   * this bounded node count instead of seconds of continuous main-thread
   * work for a picture that stops moving almost immediately.
   */
  const layout = useMemo(() => {
    if (!data?.nodes?.length) return { nodes: [] as GraphNode[], edges: [] as GraphEdge[] };

    const nodes: GraphNode[] = data.nodes.map((n) => ({ ...n }) as GraphNode);
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const edges: GraphEdge[] = (data.edges ?? [])
      .filter((e) => byId.has(e.from_entity) && byId.has(e.to_entity))
      .map((e) => ({
        source: byId.get(e.from_entity)!,
        target: byId.get(e.to_entity)!,
        relation_type: e.relation_type,
      }));

    const sim = forceSimulation<GraphNode>(nodes)
      .force("charge", forceManyBody().strength(-260))
      .force("center", forceCenter(W / 2, H / 2))
      .force("collide", forceCollide<GraphNode>().radius((d) => radius(d) + 8))
      .force(
        "link",
        forceLink<GraphNode, GraphEdge>(edges)
          .id((d) => d.id)
          .distance(100)
          .strength(0.35),
      )
      .stop();

    // Enough iterations for the layout to settle at this size.
    sim.tick(320);

    // Keep every node inside the viewBox — an unbounded simulation can push
    // an outlier past the edge where it is simply invisible.
    for (const n of nodes) {
      const r = radius(n) + 14;
      n.x = Math.max(r, Math.min(W - r, n.x ?? W / 2));
      n.y = Math.max(r, Math.min(H - r, n.y ?? H / 2));
    }
    return { nodes, edges };
  }, [data]);

  if (!sources.length) return null;

  if (isPending) return <div className="skeleton skeleton-row" style={{ height: H }} />;

  if (!data?.nodes.length) {
    return (
      <div className="empty-state">
        <Network size={22} aria-hidden />
        <h2>No entities in these results</h2>
        <p>
          The graph is built from entities the current results mention. Run the entity
          extractor from Operate, or widen the search.
        </p>
      </div>
    );
  }

  const { nodes, edges } = layout;

  return (
    <figure className="graph">
      <figcaption className="spark-caption">
        <span>
          Entities mentioned by these results — {nodes.length} node
          {nodes.length === 1 ? "" : "s"}, {edges.length} edge{edges.length === 1 ? "" : "s"}
        </span>
      </figcaption>
      <div className="graph-plot">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Entity graph: ${nodes.length} entities`}>
          <g>
            {edges.map((e, i) => {
              const s = e.source as GraphNode;
              const t = e.target as GraphNode;
              return (
                <line
                  key={i}
                  x1={s.x ?? 0}
                  y1={s.y ?? 0}
                  x2={t.x ?? 0}
                  y2={t.y ?? 0}
                  className="graph-edge"
                />
              );
            })}
          </g>
          <g>
            {nodes.map((n) => (
              <g
                key={n.id}
                transform={`translate(${n.x ?? 0},${n.y ?? 0})`}
                onMouseEnter={() => setHover(n)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(n)}
                onBlur={() => setHover(null)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    navigate(`/e/${n.id}`);
                  }
                }}
                // Node identity and the "Open" link previously lived only
                // behind onMouseEnter/onMouseLeave -- a keyboard or switch
                // user could open Graph mode and never learn what a single
                // node represented, or reach any of them (UI audit
                // full-app C2). tabIndex + role="button" make each node a
                // real stop in the tab order; Enter/Space navigate directly
                // rather than requiring a second click on the hover-only
                // "Open" link.
                tabIndex={0}
                role="button"
                aria-label={`${n.name}, ${n.entity_type}, ${n.hits_in_results} in results, ${n.mention_count} mentions total`}
                className="graph-node"
              >
                <circle
                  r={radius(n)}
                  fill={TYPE_VAR[n.entity_type] ?? FALLBACK}
                  className="graph-circle"
                />
                {/* Labels on the larger nodes only; labelling everything at
                    this density is unreadable and the rest have tooltips. */}
                {n.hits_in_results > 1 && (
                  <text y={radius(n) + 11} textAnchor="middle" className="graph-label">
                    {n.name.length > 18 ? `${n.name.slice(0, 17)}…` : n.name}
                  </text>
                )}
              </g>
            ))}
          </g>
        </svg>
        {hover && (
          <div className="graph-tip">
            <strong>{hover.name}</strong>
            <span>
              {hover.entity_type} · {hover.hits_in_results} in results · {hover.mention_count} total
            </span>
            <Link to={`/e/${hover.id}`}>Open</Link>
          </div>
        )}
      </div>
      <ul className="graph-legend">
        {[...new Set(nodes.map((n) => n.entity_type))].map((t) => (
          <li key={t}>
            <span className="graph-swatch" style={{ background: TYPE_VAR[t] ?? FALLBACK }} aria-hidden />
            {t}
          </li>
        ))}
      </ul>
    </figure>
  );
}

function radius(n: GraphNode): number {
  return 5 + Math.min(12, Math.sqrt(n.hits_in_results) * 3);
}
