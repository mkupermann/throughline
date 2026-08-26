/** Shared furniture for the PM ("Project Management") surface.
 *
 * The PM pages are bilingual (Deutsch/English, see ./i18n.ts), so numbers,
 * dates and relative times are formatted in de-DE or en-US here to match the
 * current language — deliberately different from lib/format.ts, which is
 * fixed to en-US for the host app's English-only surfaces. The rule is the
 * same in both places: numbers follow the language of the words around
 * them; here that language can change at runtime.
 */

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ChevronRight, Languages, OctagonAlert, RefreshCw, X } from "lucide-react";

import { ApiError, pmApi, type PmAiCatalogTool, type PmProject, type PmTaskStatus } from "@/lib/api";
import { getLang, useLang, type Dict } from "./i18n";

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
        // The dashboard/cockpit refetch every 8-15s, so a budget crossing
        // into "serious"/"critical" while the page is open changed the
        // bar's colour and label with nothing announced to a screen-reader
        // user (UI audit full-app... PM audit L2). Budgets move slowly in
        // practice, so `polite` — never interrupting — is enough.
        aria-live="polite"
      >
        <div className="pm-budget-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="pm-budget-figures tabular">
        {fmtCompact(used)} / {fmtCompact(budget)} {t.common.tokens}{label ? ` · ${label}` : ""}
      </span>
    </div>
  );
}

// ── Inline confirm (no window.confirm, no modal) ─────────────────────────
// A two-step button: the first click "arms" it — the button's own content is
// replaced in place by a small confirm/cancel pair — and only the confirm
// click fires `onConfirm`. Arming auto-expires after a few seconds, and
// losing focus (blur on the whole group) disarms immediately, so an armed
// button never lingers as a trap for an unrelated later click.

const INLINE_CONFIRM_TIMEOUT_MS = 4000;

export function InlineConfirmButton({
  className,
  children,
  confirmLabel,
  cancelLabel,
  title,
  ariaLabel,
  disabled,
  pending,
  onConfirm,
}: {
  className?: string;
  children: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  title?: string;
  ariaLabel?: string;
  disabled?: boolean;
  pending?: boolean;
  onConfirm: () => void;
}) {
  const { t } = useLang();
  const [armed, setArmed] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(timerRef.current), []);

  function arm() {
    setArmed(true);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setArmed(false), INLINE_CONFIRM_TIMEOUT_MS);
  }
  function disarm() {
    clearTimeout(timerRef.current);
    setArmed(false);
  }

  if (armed) {
    return (
      <span
        className="pm-inline-confirm"
        // A blur that leaves the whole confirm/cancel pair (not just moves
        // from one of its buttons to the other) disarms — losing focus
        // entirely should not leave an armed "really delete?" button
        // sitting around for a later, unrelated click to land on.
        onBlur={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) disarm();
        }}
      >
        <button
          type="button"
          className={`${className ?? ""} pm-inline-confirm-yes`}
          onClick={() => {
            disarm();
            onConfirm();
          }}
          disabled={pending}
          autoFocus
        >
          {confirmLabel ?? t.common.confirmQuestion}
        </button>
        <button type="button" className="pm-inline-confirm-no" onClick={disarm}>
          {cancelLabel ?? t.common.cancel}
        </button>
      </span>
    );
  }

  return (
    <button
      type="button"
      className={className}
      onClick={arm}
      disabled={disabled}
      title={title}
      aria-label={ariaLabel}
    >
      {children}
    </button>
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

// ── Disclosure (a controlled <details>, with aria-expanded stated) ───────
// Native <details>/<summary> already carries an implicit disclosure
// semantic in every current browser/AT combination, but nothing on the page
// stated it explicitly, and the accessibility-tree snapshot the UI audit
// took showed these as bare `generic` nodes with no exposed expanded state
// — unlike the RoleRow/MemberRow/TeamRow "Edit" buttons a few lines away,
// which do expose `aria-expanded` (PM audit M4). This wrapper controls
// `open` itself so `aria-expanded` on the summary always matches it,
// closing the gap regardless of how a given AT surfaces <details> on its
// own.
export function Disclosure({
  summary,
  children,
  className,
  defaultOpen = false,
}: {
  summary: ReactNode;
  children: ReactNode;
  className?: string;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <details
      className={className}
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
    >
      <summary aria-expanded={open}>{summary}</summary>
      {children}
    </details>
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

/** Some skill descriptions are raw YAML block-scalar artifacts (literally
 *  `">-"` or `"|"`) rather than real prose — a source-data quality issue
 *  the UI has no control over, but it must not leak through as an
 *  accessible name or a visible description. Guards against that shape and
 *  treats it as "no description" instead (UI audit PM H1). */
const YAML_SCALAR_LEAK = /^[|>][-+0-9]{0,2}$/;

function cleanSkillDescription(desc: string | null | undefined): string | null {
  if (!desc) return null;
  const trimmed = desc.trim();
  if (!trimmed || YAML_SCALAR_LEAK.test(trimmed)) return null;
  return desc;
}

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
  const searchRef = useRef<HTMLInputElement>(null);

  // Auto-focus the search box the moment the picker has something to filter,
  // so the common path (type to narrow, then click) rarely needs to tab
  // through the up-to-40-row checkbox list below at all (UI audit PM H2).
  useEffect(() => {
    if (!isPending && !error) searchRef.current?.focus();
    // Only on the picker's own mount/data-ready transition, not on every
    // keystroke into the search box itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPending]);

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
        ref={searchRef}
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
            {matches.shown.map((s) => {
              // The description moves to a visually-hidden span referenced by
              // aria-describedby on the checkbox itself (not the wrapping
              // label) — a `title` attribute silently became the option's
              // accessible name for AT, so a malformed or huge description
              // drowned out or replaced the skill's own short name (UI audit
              // PM H1).
              const desc = cleanSkillDescription(s.description);
              const descId = desc ? `pm-skill-desc-${s.id}` : undefined;
              return (
                <li key={s.id}>
                  <label className="pm-skillpicker-option">
                    <input
                      type="checkbox"
                      checked={value.includes(s.id)}
                      onChange={() => toggle(s.id)}
                      aria-describedby={descId}
                    />
                    <span>{s.name}</span>
                    {desc && (
                      <span id={descId} className="sr-only">
                        {desc}
                      </span>
                    )}
                  </label>
                </li>
              );
            })}
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

// ── AI tool/model binding ────────────────────────────────────────────────
// GET /pm/ai-catalog resolves, at request time, what the launch pipeline
// actually understands right now (Ollama models it has pulled, ~/.vibe
// agent profiles, the static Claude Code entry) — replacing what used to be
// two free-text inputs where a typo only surfaced as a launch-time failure.

export function useAiCatalog() {
  return useQuery({
    queryKey: ["pm-ai-catalog"],
    queryFn: pmApi.aiCatalog,
    staleTime: 60_000,
  });
}

/** The three built-in tools' machine keys (`aider`/`claude`/`vibe`) are
 *  stable — see ai_catalog() in queries/pm.py — but their `label` and
 *  static model strings come straight from the server with no localization
 *  pass, so the EN locale still showed "Aider + Ollama (lokal)" verbatim
 *  (UI audit PM H3). The backend stays untouched (its label is a stable
 *  machine-adjacent string, not user data); the client maps the known keys
 *  through `t.aiPicker` and falls back to the server string for anything it
 *  doesn't recognise — a user-defined provider's `provider:<id>` label is
 *  the operator's own text and is shown as-is. */
function localizedToolLabel(tl: PmAiCatalogTool, t: Dict): string {
  if (tl.tool === "aider") return t.aiPicker.toolLabelAider;
  if (tl.tool === "claude") return t.aiPicker.toolLabelClaude;
  if (tl.tool === "vibe") return t.aiPicker.toolLabelVibe;
  return tl.label;
}

function localizedModelLabel(model: string, t: Dict): string {
  if (model === "claude -p (Standard)") return t.aiPicker.modelLabelClaudeDefault;
  return model;
}

/** Tool + model selects fed by the AI catalog. A stored value the catalog
 *  no longer lists (Ollama down, a profile deleted, ...) is kept as an
 *  extra "(saved: X)" option rather than silently dropped — editing must
 *  never destroy data the form didn't intend to change. */
export function AiBindingPicker({
  tool,
  model,
  onChange,
}: {
  tool: string | null;
  model: string | null;
  onChange: (next: { tool: string | null; model: string | null }) => void;
}) {
  const { t } = useLang();
  const { data, isPending, error, refetch } = useAiCatalog();
  const tools = data?.tools ?? [];
  const selectedTool: PmAiCatalogTool | undefined = tools.find((tl) => tl.tool === tool);
  const toolKnown = tool === null || tools.some((tl) => tl.tool === tool);

  const modelOptions = selectedTool ? [...selectedTool.models] : [];
  if (model !== null && !modelOptions.includes(model)) modelOptions.push(model);

  return (
    <>
      <label className="pm-field">
        <span className="pm-label">{t.forms.aiToolLabel}</span>
        <select
          className="pm-input"
          value={tool ?? ""}
          disabled={isPending}
          onChange={(e) => onChange({ tool: e.target.value || null, model: null })}
        >
          <option value="">{t.aiPicker.toolNone}</option>
          {tools.map((tl) => (
            <option key={tl.tool} value={tl.tool}>
              {localizedToolLabel(tl, t)}
            </option>
          ))}
          {!toolKnown && tool !== null && <option value={tool}>{t.aiPicker.storedOption(tool)}</option>}
        </select>
      </label>
      <label className="pm-field">
        <span className="pm-label">{t.forms.aiModelLabel}</span>
        <select
          className="pm-input"
          value={model ?? ""}
          disabled={isPending || tool === null}
          onChange={(e) => onChange({ tool, model: e.target.value || null })}
        >
          <option value="">{t.aiPicker.modelNone}</option>
          {modelOptions.map((m) => (
            <option key={m} value={m}>
              {m === model && !selectedTool?.models.includes(m)
                ? t.aiPicker.storedOption(m)
                : localizedModelLabel(m, t)}
            </option>
          ))}
        </select>
      </label>
      {error ? (
        <p className="pm-field-error pm-field-span" role="alert">
          {t.aiPicker.loadError}{" "}
          <button type="button" className="pm-linklike" onClick={() => refetch()}>
            {t.common.retry}
          </button>
        </p>
      ) : (
        selectedTool?.unavailable && (
          <p className="pm-field-hint pm-field-span">
            {selectedTool.tool === "aider" ? t.aiPicker.ollamaUnavailable : t.aiPicker.toolUnavailable(selectedTool.label)}
          </p>
        )
      )}
    </>
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
