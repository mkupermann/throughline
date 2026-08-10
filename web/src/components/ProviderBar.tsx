import { useQuery } from "@tanstack/react-query";
import { useLocation, useSearchParams } from "react-router-dom";

import { providersApi } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { readProviders, withProviders } from "@/lib/providerScope";

/**
 * The provider scope, permanently visible.
 *
 * Throughline exists to unify memory across nine AI CLIs, and the previous
 * interface exposed the originating tool in exactly one place — the
 * conversation detail record. This bar is the fix: the scope is always on
 * screen, so it can never silently filter something the user has forgotten
 * about.
 *
 * Hidden on Console, where raw SQL ignores it (spec §4.2) — a scope control
 * that does not affect what you are seeing is worse than none.
 */
const HIDDEN_ON = ["/console"];

// The pseudo-provider name coverage() emits for source_tool IS NULL rows
// (throughline/providers.py's UNATTRIBUTED_LABEL). Not a real provider —
// see the branch below.
const UNATTRIBUTED = "(unattributed)";

export function ProviderBar() {
  const { pathname } = useLocation();
  const [sp, setSp] = useSearchParams();
  const { data } = useQuery({
    queryKey: ["providers"],
    queryFn: () => providersApi.list(),
    staleTime: 60_000,
  });

  if (HIDDEN_ON.some((p) => pathname.startsWith(p))) return null;

  const active = new Set(readProviders(sp));
  const providers = data?.providers ?? [];

  function toggle(name: string) {
    const next = new Set(active);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setSp(withProviders(sp, [...next]), { replace: false });
  }

  return (
    <div className="provider-bar" data-testid="provider-bar" role="group" aria-label="Provider scope">
      {providers.map((p) => {
        // coverage() appends this pseudo-row for conversations with
        // source_tool IS NULL (see throughline/queries/providers.py). It is
        // not a real provider name and the query layer only ever matches
        // `source_tool = ANY(...)` — NULL never satisfies that, in Find or
        // in Timeline. Making this chip a scope toggle would set
        // `?provider=(unattributed)`, which then silently filters every
        // result out with no explanation (and rides along on every nav via
        // carryProviders). OperatePage's coverage table already withholds
        // the Ingest action for the same row rather than offering a control
        // guaranteed to fail; this is the same call for the same reason —
        // show the count, don't offer a scope nothing can honor.
        if (p.name === UNATTRIBUTED) {
          return (
            <span
              key={p.name}
              className="provider-chip provider-chip-static"
              data-status={p.status}
              title={`${formatCount(p.ingested)} conversation(s) with no recorded tool — not filterable`}
            >
              <span className="provider-chip-label">{p.label}</span>
              <span className="provider-chip-count tabular">{formatCount(p.ingested)}</span>
            </span>
          );
        }
        const on = active.has(p.name);
        return (
          <button
            key={p.name}
            type="button"
            className={`provider-chip${on ? " is-active" : ""}`}
            data-status={p.status}
            data-slot={p.chart_slot}
            aria-pressed={on}
            onClick={() => toggle(p.name)}
            title={
              p.pending > 0
                ? `${p.pending} file(s) on disk not imported`
                : `${formatCount(p.ingested)} conversation(s)`
            }
          >
            <span className="provider-chip-label">{p.label}</span>
            <span className="provider-chip-count tabular">{formatCount(p.ingested)}</span>
            {p.pending > 0 && (
              // The dot alone is colour, so it carries its own accessible name — it is
              // never the only differentiator (data-status and the title do the rest).
              <span className="provider-chip-dot" aria-label="not fully imported" />
            )}
          </button>
        );
      })}
      {active.size > 0 && (
        <button
          type="button"
          className="provider-chip provider-chip-clear"
          onClick={() => setSp(withProviders(sp, []))}
        >
          Clear scope
        </button>
      )}
    </div>
  );
}
