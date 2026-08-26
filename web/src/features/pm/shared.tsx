/** Shared furniture for the PM ("Project Management") surface.
 *
 * The PM pages are bilingual (Deutsch/English, see ./i18n.ts), so numbers,
 * dates and relative times are formatted in de-DE or en-US here to match the
 * current language — deliberately different from lib/format.ts, which is
 * fixed to en-US for the host app's English-only surfaces. The rule is the
 * same in both places: numbers follow the language of the words around
 * them; here that language can change at runtime.
 */

import { useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ChevronRight, Languages, OctagonAlert, RefreshCw, X } from "lucide-react";

import { ApiError, pmApi, type PmProject, type PmTaskStatus } from "@/lib/api";
import { getLang, useLang } from "./i18n";

// ── Formatting (locale follows the current language) ────────────────────

const nfDe = new Intl.NumberFormat("de-DE");
const nfEn = new Intl.NumberFormat("en-US");
const nfCompactDe = new Intl.NumberFormat("de-DE", { notation: "compact", maximumFractionDigits: 1 });
const nfCompactEn = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const rtfDe = new Intl.RelativeTimeFormat("de-DE", { numeric: "auto" });
const rtfEn = new Intl.RelativeTimeFormat("en-US", { numeric: "auto" });
const dtfDe = new Intl.DateTimeFormat("de-DE", { day: "numeric", month: "short", year: "numeric" });
const dtfEn = new Intl.DateTimeFormat("en-US", { day: "numeric", month: "short", year: "numeric" });

export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return (getLang() === "de" ? nfDe : nfEn).format(n);
}

/** Compact token figures for gauges and chips; full precision stays in rows. */
export function fmtCompact(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  if (Math.abs(n) < 10_000) return (getLang() === "de" ? nfDe : nfEn).format(n);
  return (getLang() === "de" ? nfCompactDe : nfCompactEn).format(n);
}

/** "1 Skill" / "3 Skills" — a count with a unit that agrees with it. */
export function plural(n: number, one: string, many: string): string {
  return `${fmtInt(n)} ${n === 1 ? one : many}`;
}

export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const rtf = getLang() === "de" ? rtfDe : rtfEn;
  const s = Math.round((then - Date.now()) / 1000);
  const abs = Math.abs(s);
  if (abs < 60) return rtf.format(Math.trunc(s / 1), "second");
  if (abs < 3600) return rtf.format(Math.trunc(s / 60), "minute");
  if (abs < 86_400) return rtf.format(Math.trunc(s / 3600), "hour");
  if (abs < 30 * 86_400) return rtf.format(Math.trunc(s / 86_400), "day");
  return (getLang() === "de" ? dtfDe : dtfEn).format(new Date(iso));
}

// ── Status vocabulary ────────────────────────────────────────────────────
// One consistent word per state and language, everywhere. PASS/FAIL
// verdicts are rendered as BESTANDEN/ABGELEHNT (PASSED/REJECTED) — see
// TaskPage — never mixed with these.

export const TASK_STATUSES: PmTaskStatus[] = [
  "pending", "running", "pass", "fail", "budget_exceeded", "crashed", "stopped",
];

export const TERMINAL_STATUSES: PmTaskStatus[] = [
  "pass", "fail", "budget_exceeded", "crashed", "stopped",
];

export function TaskStatusChip({ status }: { status: PmTaskStatus }) {
  const { t } = useLang();
  return (
    <span className={`pm-status pm-status-${status}`}>{t.status.task[status]}</span>
  );
}

export function ProjectStatusChip({ status }: { status: PmProject["status"] }) {
  const { t } = useLang();
  return (
    <span className={`pm-status pm-status-${status}`}>{t.status.project[status]}</span>
  );
}

// ── Language toggle ──────────────────────────────────────────────────────

export function LangToggle() {
  const { lang, toggle } = useLang();
  return (
    <button
      type="button"
      className="pm-lang-toggle"
      onClick={toggle}
      aria-label={lang === "de" ? "Switch to English" : "Auf Deutsch umschalten"}
      title={lang === "de" ? "Switch to English" : "Auf Deutsch umschalten"}
    >
      <Languages size={13} aria-hidden />
      <span className={lang === "de" ? "is-active" : undefined}>DE</span>
      <span aria-hidden className="pm-lang-toggle-sep">|</span>
      <span className={lang === "en" ? "is-active" : undefined}>EN</span>
    </button>
  );
}

// ── Breadcrumbs ──────────────────────────────────────────────────────────

export interface Crumb {
  label: string;
  to?: string;
}

export function Breadcrumbs({ items }: { items: Crumb[] }) {
  const { t } = useLang();
  return (
    <nav aria-label={t.common.path} className="pm-crumbs">
      {items.map((c, i) => (
        <span key={`${c.label}-${i}`} className="pm-crumb">
          {i > 0 && <ChevronRight size={13} aria-hidden className="pm-crumb-sep" />}
          {c.to ? (
            <Link to={c.to}>{c.label}</Link>
          ) : (
            <span aria-current="page">{c.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}

/** Breadcrumbs plus the language toggle, in the placement every PM page
 *  shares: a row at the top of the header, breadcrumb trail on the left and
 *  DE|EN on the right. */
export function PmHeaderBar({ items }: { items: Crumb[] }) {
  return (
    <div className="pm-headerbar">
      <Breadcrumbs items={items} />
      <LangToggle />
    </div>
  );
}

// ── Budget gauge ─────────────────────────────────────────────────────────

/** Spend vs budget. Without a budget there is no bar — a gauge against
 *  nothing would be decoration — just the spend figure. */
export function BudgetBar({
  used,
  budget,
  label,
}: {
  used: number;
  budget: number | null;
  label?: string;
}) {
  const { t } = useLang();
  if (budget === null || budget <= 0) {
    return (
      <div className="pm-budget pm-budget-unbounded">
        <span className="pm-budget-figures tabular">
          {fmtInt(used)} {t.common.tokens}{label ? ` · ${label}` : ""} · {t.budget.noBudgetSet}
        </span>
      </div>
    );
  }
  const ratio = used / budget;
  const pct = Math.min(100, ratio * 100);
  const tone = ratio >= 1 ? "critical" : ratio >= 0.8 ? "serious" : "ok";
  return (
    <div className="pm-budget" data-tone={tone}>
      <div
        className="pm-budget-track"
        role="img"
        aria-label={t.budget.usedOfLabel(fmtInt(used), fmtInt(budget))}
      >
        <div className="pm-budget-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="pm-budget-figures tabular">
        {fmtCompact(used)} / {fmtCompact(budget)} {t.common.tokens}{label ? ` · ${label}` : ""}
      </span>
    </div>
  );
}

// ── Loading / error / empty ──────────────────────────────────────────────

export function SkeletonRows({ n = 3, header = false }: { n?: number; header?: boolean }) {
  return (
    <div aria-hidden>
      {header && <div className="skeleton skeleton-headline" />}
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="skeleton skeleton-row" />
      ))}
    </div>
  );
}

export function ErrorState({
  title,
  error,
  onRetry,
}: {
  title: string;
  error: unknown;
  onRetry: () => void;
}) {
  const { t } = useLang();
  const e = error instanceof ApiError ? error : null;
  return (
    <div className="empty-state" role="alert">
      <OctagonAlert size={22} aria-hidden />
      <h2>{title}</h2>
      <p>{e?.message ?? String(error)}</p>
      {e?.hint && <p className="empty-hint">{e.hint}</p>}
      <button type="button" className="button" onClick={onRetry}>
        <RefreshCw size={14} aria-hidden />
        {t.common.retry}
      </button>
    </div>
  );
}

export function EmptyState({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <h2>{title}</h2>
      {children}
    </div>
  );
}

// ── Minimal markdown rendering ───────────────────────────────────────────
// Headings, lists, fenced code, bold and inline code — enough to make a
// SPEC.md readable as typography instead of a raw blob. Deliberately not an
// external library: the input is our own pipeline's SPEC files.

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  // Split on **bold** and `code` spans, keeping the delimiters' content.
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<strong key={`${keyBase}-b${i}`}>{tok.slice(2, -2)}</strong>);
    else out.push(<code key={`${keyBase}-c${i}`}>{tok.slice(1, -1)}</code>);
    last = m.index + tok.length;
    i += 1;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim() === "") {
      i += 1;
      continue;
    }

    if (line.startsWith("```")) {
      const code: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith("```")) {
        code.push(lines[i]);
        i += 1;
      }
      i += 1; // closing fence
      blocks.push(
        <pre key={key++} className="pm-md-code">
          <code>{code.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      const Tag = (`h${Math.min(level + 2, 6)}`) as "h3" | "h4" | "h5" | "h6";
      blocks.push(<Tag key={key++} className={`pm-md-h pm-md-h${level}`}>{inline(heading[2], `h${key}`)}</Tag>);
      i += 1;
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i += 1;
      }
      blocks.push(
        <ul key={key++} className="pm-md-list">
          {items.map((it, j) => (
            <li key={j}>{inline(it, `u${key}-${j}`)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i += 1;
      }
      blocks.push(
        <ol key={key++} className="pm-md-list">
          {items.map((it, j) => (
            <li key={j}>{inline(it, `o${key}-${j}`)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    // Paragraph: consecutive plain lines joined with spaces.
    const para: string[] = [line];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^(#{1,4})\s|^```|^\s*[-*]\s+|^\s*\d+\.\s+/.test(lines[i])
    ) {
      para.push(lines[i]);
      i += 1;
    }
    blocks.push(<p key={key++}>{inline(para.join(" "), `p${key}`)}</p>);
  }

  return <div className="pm-md">{blocks}</div>;
}

// ── Skills multi-select ──────────────────────────────────────────────────
// The skills table holds thousands of rows, so the picker is a filter input
// over a capped result list, with the current selection always visible as
// removable chips above it.

export function useSkills() {
  return useQuery({
    queryKey: ["pm-skills"],
    queryFn: pmApi.listSkills,
    staleTime: 5 * 60_000,
  });
}

const SKILL_RESULT_CAP = 40;

export function SkillPicker({
  value,
  onChange,
}: {
  value: number[];
  onChange: (ids: number[]) => void;
}) {
  const { t } = useLang();
  const { data, isPending, error, refetch } = useSkills();
  const [q, setQ] = useState("");

  const skills = data?.skills ?? [];
  const byId = useMemo(() => new Map(skills.map((s) => [s.id, s])), [skills]);

  const matches = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const pool = needle
      ? skills.filter((s) => s.name.toLowerCase().includes(needle))
      : skills;
    return { shown: pool.slice(0, SKILL_RESULT_CAP), total: pool.length };
  }, [skills, q]);

  function toggle(id: number) {
    onChange(value.includes(id) ? value.filter((v) => v !== id) : [...value, id]);
  }

  if (error) {
    return (
      <div className="pm-skillpicker">
        <p className="pm-field-error">
          {t.skillPicker.loadError}{" "}
          <button type="button" className="pm-linklike" onClick={() => refetch()}>
            {t.common.retry}
          </button>
        </p>
      </div>
    );
  }

  return (
    <div className="pm-skillpicker">
      {value.length > 0 && (
        <div className="pm-skillpicker-chips">
          {value.map((id) => (
            <button
              key={id}
              type="button"
              className="pm-chip"
              onClick={() => toggle(id)}
              title={t.skillPicker.removeTitle}
            >
              {byId.get(id)?.name ?? `Skill ${id}`}
              <X size={12} aria-hidden />
            </button>
          ))}
        </div>
      )}
      <input
        type="search"
        className="pm-input"
        placeholder={t.skillPicker.searchPlaceholder}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        aria-label={t.skillPicker.searchLabel}
      />
      {isPending ? (
        <div className="skeleton pm-skillpicker-skeleton" />
      ) : (
        <>
          <ul className="pm-skillpicker-list">
            {matches.shown.map((s) => (
              <li key={s.id}>
                <label className="pm-skillpicker-option" title={s.description ?? undefined}>
                  <input
                    type="checkbox"
                    checked={value.includes(s.id)}
                    onChange={() => toggle(s.id)}
                  />
                  <span>{s.name}</span>
                </label>
              </li>
            ))}
            {matches.shown.length === 0 && (
              <li className="pm-skillpicker-none">{t.skillPicker.none}</li>
            )}
          </ul>
          {matches.total > matches.shown.length && (
            <p className="pm-skillpicker-more">
              {t.skillPicker.more(fmtInt(matches.shown.length), fmtInt(matches.total))}
            </p>
          )}
        </>
      )}
    </div>
  );
}

// ── Document path list editor ────────────────────────────────────────────

export function DocListEditor({
  value,
  onChange,
}: {
  value: string[];
  onChange: (docs: string[]) => void;
}) {
  const { t } = useLang();
  const [draft, setDraft] = useState("");

  function add() {
    const p = draft.trim();
    if (!p || value.includes(p)) return;
    onChange([...value, p]);
    setDraft("");
  }

  return (
    <div className="pm-doclist">
      {value.length > 0 && (
        <ul className="pm-doclist-items">
          {value.map((d) => (
            <li key={d}>
              <code>{d}</code>
              <button
                type="button"
                className="pm-linklike"
                onClick={() => onChange(value.filter((v) => v !== d))}
                aria-label={t.docList.removeAria(d)}
              >
                {t.docList.remove}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="pm-doclist-add">
        <input
          className="pm-input"
          placeholder={t.docList.placeholder}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <button type="button" className="button pm-button-flush" onClick={add} disabled={!draft.trim()}>
          {t.docList.add}
        </button>
      </div>
    </div>
  );
}
