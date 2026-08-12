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
/**
 * Surfaces the scope does not reach.
 *
 * Console runs raw SQL and ignores it (spec §4.2). Overview was added after
 * measuring it: `/api/overview` never reads the `provider` parameter, so the
 * chips returned byte-identical totals filtered and unfiltered. The control
 * looked live — chip highlights, URL changes — and moved nothing on the page,
 * which is worse than having no control at all, because the reader concludes
 * the numbers are scoped when they are not.
 *
 * Scoping Overview would also be answering the wrong question: its items
 * (pending extraction, unapplied migrations, a broken pgvector) are properties
 * of the whole store, not of one assistant. A per-tool worklist is not a
 * smaller version of this page — it is a different page.
 */
const HIDDEN_ON = ["/console"];
const HIDDEN_EXACT = ["/"];

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
  // Exact, not prefix: "/" is the prefix of every route.
  if (HIDDEN_EXACT.includes(pathname)) return null;

  const active = new Set(readProviders(sp));
  const all = data?.providers ?? [];

  // Only tools that have something here, or that have something waiting to be
  // imported. Six of the nine registered adapters read "0" on a typical
  // machine — nobody runs all nine — and they occupied the top strip of every
  // page with a filter guaranteed to return nothing. A chip whose only
  // possible outcome is an empty result is not a control.
  //
  // `pending > 0` keeps a tool visible before its first ingest: that is
  // precisely when the user needs to see it, and the chip's dot is how they
  // find out there is anything to import.
  //
  // A provider that is currently part of the scope always stays, whatever its
  // count — removing the chip a filter is switched on by would strand the user
  // with a filter they can see the effect of but not the control for.
  const providers = all.filter(
    (p) => p.ingested > 0 || p.pending > 0 || active.has(p.name) || p.name === UNATTRIBUTED,
  );
  const hidden = all.length - providers.length;

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
      {/* Says the omission out loud. Silently dropping the empty tools would
          make "Throughline supports nine assistants" and a bar showing three
          contradict each other with no explanation on screen. */}
      {hidden > 0 && (
        <span
          className="provider-bar-note"
          title="Adapters registered but holding nothing on this machine — throughline ingest --list-sources"
        >
          +{hidden} with no data here
        </span>
      )}
    </div>
  );
}
