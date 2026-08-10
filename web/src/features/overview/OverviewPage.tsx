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

const SEVERITY_ICON = {
  critical: OctagonAlert,
  warning: AlertTriangle,
  info: Info,
} as const;

/** Severity is announced in text as well as colour — never colour alone. */
const SEVERITY_LABEL = {
  critical: "Critical",
  warning: "Warning",
  info: "For information",
} as const;

function AttentionRow({ item }: { item: AttentionItem }) {
  const Icon = SEVERITY_ICON[item.severity];
  return (
    <li className={`attn attn-${item.severity}`}>
      <span className="attn-icon" aria-hidden>
        <Icon size={16} />
      </span>
      <div className="attn-body">
        <div className="attn-head">
          <span className="attn-title">{item.title}</span>
          <span className="sr-only">{SEVERITY_LABEL[item.severity]}.</span>
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
            <p className="page-subtitle">{data.headline.label}</p>
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

      {/* The headline is a number, not a chart — one figure needs no plot. */}
      <div className="headline">
        <div className="headline-value tabular">{formatCompact(data.headline.value)}</div>
        <div className="headline-sub">{data.headline.sublabel}</div>
      </div>

      <VerdictBanner data={data} />

      {data.attention.length > 0 && (
        <section aria-labelledby="attn-h">
          <h2 id="attn-h" className="section-label">
            Needs attention
          </h2>
          <ul className="attn-list">
            {data.attention.map((item) => (
              <AttentionRow key={item.id} item={item} />
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="activity-h" className="stack-top">
        <h2 id="activity-h" className="section-label">
          Activity
        </h2>
        <Sparkline data={data.activity} days={30} label="Conversations, last 30 days" />
      </section>

      <section aria-labelledby="totals-h" className="stack-top">
        <h2 id="totals-h" className="section-label">
          Inventory
        </h2>
        <dl className="totals">
          {Object.entries(data.totals).map(([k, v]) => (
            <div key={k} className="total">
              <dt>{k}</dt>
              <dd className="tabular">{formatCount(v)}</dd>
            </div>
          ))}
        </dl>
      </section>
    </>
  );
}
