import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Info,
  OctagonAlert,
  RefreshCw,
} from "lucide-react";

import { api, ApiError, type AttentionItem, type Overview } from "@/lib/api";
import { formatCompact, formatCount } from "@/lib/format";
import { Sparkline } from "@/components/Sparkline";
import { RecentProjects } from "./RecentProjects";

const SEVERITY_ICON = {
  critical: OctagonAlert,
  warning: AlertTriangle,
  info: Info,
} as const;

/** Severity is announced in text as well as colour — never colour alone. */
const SEVERITY_LABEL = {
  critical: "Critical",
  warning: "Warning",
  info: "FYI",
} as const;

//: Reading order for the worklist. Lower sorts first.
const SEVERITY_RANK = { critical: 0, warning: 1, info: 2 } as const;

function AttentionRow({ item }: { item: AttentionItem }) {
  const Icon = SEVERITY_ICON[item.severity];
  return (
    <li className={`attn attn-${item.severity}`}>
      <span className="attn-icon" aria-hidden>
        <Icon size={16} />
      </span>
      <div className="attn-body">
        <div className="attn-head">
          {/* Urgency in words, not only in a hue and an icon shape. This label
            * was `sr-only`: a screen-reader user was told an item was critical
            * while a sighted reader had to infer it from a border colour — and
            * with several items listed, from nothing at all if they could not
            * separate the hues. */}
          <span className={`attn-sev attn-sev-${item.severity}`}>
            {SEVERITY_LABEL[item.severity]}
          </span>
          <span className="attn-title">{item.title}</span>
          {item.count !== null && <span className="attn-count tabular">{formatCount(item.count)}</span>}
        </div>
        <p className="attn-detail">{item.detail}</p>
      </div>
      {item.action && (
        <Link to={item.action} className="attn-action">
          {item.action_label ?? "Open"}
          <ArrowRight size={14} aria-hidden />
        </Link>
      )}
    </li>
  );
}

function VerdictBanner({ data }: { data: Overview }) {
  if (data.verdict === "ok") {
    return (
      <div className="verdict verdict-ok">
        <CheckCircle2 size={18} aria-hidden />
        <span>Nothing needs your attention.</span>
      </div>
    );
  }
  const Icon = data.verdict === "broken" ? OctagonAlert : AlertTriangle;
  return (
    <div className={`verdict verdict-${data.verdict}`}>
      <Icon size={18} aria-hidden />
      <span>{data.verdict_reason}</span>
    </div>
  );
}

export function OverviewPage() {
  const { data, isPending, error, refetch, isFetching } = useQuery({
    queryKey: ["overview"],
    queryFn: api.overview,
  });

  if (isPending) {
    // Skeleton, not a spinner — the layout is known, so reserve its space
    // and avoid a shift when the data lands.
    return (
      <>
        <header className="page-header">
          <h1 className="page-title">Overview</h1>
        </header>
        <div className="skeleton skeleton-headline" />
        <div className="skeleton skeleton-row" />
        <div className="skeleton skeleton-row" />
      </>
    );
  }

  if (error) {
    const e = error as ApiError;
    return (
      <>
        <header className="page-header">
          <h1 className="page-title">Overview</h1>
        </header>
        <div className="empty-state">
          <OctagonAlert size={22} aria-hidden />
          <h2>Cannot load the overview</h2>
          <p>{e.message}</p>
          {e.hint && <p className="empty-hint">{e.hint}</p>}
          <button type="button" className="button" onClick={() => refetch()}>
            <RefreshCw size={14} aria-hidden />
            Try again
          </button>
        </div>
      </>
    );
  }

  return (
    <>
      <header className="page-header">
        <div className="page-header-row">
          <div>
            <h1 className="page-title">Overview</h1>
            {/* States what the page is for. It used to echo the headline's
              * label — "Memory chunks under management" — which describes a
              * database table rather than telling the reader why they are
              * here. */}
            <p className="page-subtitle">What needs doing, and what is in here.</p>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={() => refetch()}
            aria-label="Refresh"
            title="Refresh"
            disabled={isFetching}
          >
            <RefreshCw size={16} aria-hidden className={isFetching ? "spin" : undefined} />
          </button>
        </div>
      </header>

      {/* The work comes first. This page exists to answer "what should I do
        * next", and it used to open with an inventory figure, then a banner
        * saying "1 item needs attention.", then — immediately below — that same
        * item again under the heading "Needs attention". The banner restated
        * the count of a list the reader could already see and count.
        *
        * So: the list itself when there is work, and the banner only when there
        * is none — an empty page cannot say "nothing is wrong", and that is
        * exactly the case where the reader needs telling. */}
      {data.attention.length > 0 ? (
        <section aria-labelledby="attn-h" className="worklist">
          <h2 id="attn-h" className="worklist-title">
            Needs attention
          </h2>
          <ul className="attn-list">
            {/* Most urgent first. The server appends items in the order its
              * checks happen to run, which is an implementation detail, not a
              * priority — a critical item could land below an informational
              * one and the reader would work top-down through the wrong thing
              * first. Ties keep the server's order, which is deliberate within
              * a severity. */}
            {[...data.attention]
              .sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity])
              .map((item) => (
                <AttentionRow key={item.id} item={item} />
              ))}
          </ul>
        </section>
      ) : (
        <VerdictBanner data={data} />
      )}

      {/* ── What is in here ──────────────────────────────────────────────
        * One band, not four stacked sections each announced by its own
        * identical small-caps label. Four labels shouting at the same volume
        * is the same as none: the reader gets no order to read in. The
        * headline figure and the counts that qualify it now sit on one
        * baseline, because they are one statement — the number used to float
        * between the worklist and a row of cards, related to neither.
        *
        * No card borders. Five bordered boxes for five integers is chrome
        * competing with its own contents; space groups them just as well and
        * leaves the page quiet enough for the worklist above to lead. */}
      <section aria-labelledby="stock-h" className="stock">
        <h2 id="stock-h" className="sr-only">
          What is stored
        </h2>
        <div className="stock-lead">
          <div className="stock-value tabular">{formatCompact(data.headline.value)}</div>
          <div className="stock-label">
            <span className="stock-label-name">{data.headline.label}</span>
            <span className="stock-label-sub">{data.headline.sublabel}</span>
          </div>
        </div>
        <dl className="stock-figures">
          {Object.entries(data.totals).map(([k, v]) => (
            <div key={k} className="stock-figure">
              <dt>{k}</dt>
              <dd className="tabular">{formatCount(v)}</dd>
            </div>
          ))}
        </dl>
      </section>

      {/* ── Where the week went ──────────────────────────────────────────
        * Above the charts, because it is the answer to "what have I been
        * doing" and the charts are only its shape. Each row opens that
        * project's full history. */}
      <RecentProjects />

      {/* ── The two charts, side by side ─────────────────────────────────
        * Stacked full-width, these ran a flat line and eight bars across
        * 1100px and pushed the page past 1500px of scroll for two facts. Side
        * by side they fit one screen and each gets a measure it can be read
        * at. Falls back to one column below 1100px. */}
      <div className="panels">
        <section aria-labelledby="activity-h" className="panel">
          <h2 id="activity-h" className="panel-title">
            Activity
          </h2>
          <Sparkline data={data.activity} days={30} label="Conversations, last 30 days" />
        </section>

        {data.categories.length > 0 && (
          <section aria-labelledby="cats-h" className="panel">
            <h2 id="cats-h" className="panel-title">
              Memory by category
            </h2>
            <CategoryBars categories={data.categories} />
          </section>
        )}
      </div>
    </>
  );
}

/** Horizontal bars, longest first, scaled against the largest category.
 *
 * Intensity is carried by bar length alone — every bar uses one hue from the
 * validated sequential ramp. The category name is written beside each bar, so
 * assigning eight different colours (as the Streamlit chart did) would encode
 * nothing that the label does not already say, while risking pairs that a
 * colour-blind reader cannot separate.
 */
function CategoryBars({ categories }: { categories: { category: string; n: number }[] }) {
  const max = Math.max(1, ...categories.map((c) => c.n));
  return (
    <dl className="catbars">
      {categories.map((c) => (
        <div className="catbar" key={c.category}>
          <dt className="catbar-name" title={c.category}>
            {c.category.replace(/_/g, " ")}
          </dt>
          <div className="catbar-track">
            <div
              className="catbar-fill"
              style={{ width: `${(c.n / max) * 100}%` }}
              role="img"
              aria-label={`${c.category.replace(/_/g, " ")}: ${c.n} chunks`}
            />
          </div>
          <dd className="catbar-value tabular">{formatCount(c.n)}</dd>
        </div>
      ))}
    </dl>
  );
}
