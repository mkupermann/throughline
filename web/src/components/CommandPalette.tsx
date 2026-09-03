import { Command } from "cmdk";
import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Download, Moon, Sun, Monitor, FileSearch, Play } from "lucide-react";

import { NAV, NAV_GROUPS } from "@/lib/nav";
import { useTheme } from "@/lib/theme";
import { carryProviders } from "@/lib/providerScope";
import { findApi, operateApi, type FindItem } from "@/lib/api";
import { routeFor } from "@/features/find/ResultList";
import { useToast } from "@/components/Toaster";

/**
 * What a jump-to row calls itself. A memory chunk carries no `title` — that
 * field is a conversation's own summary — so falling back to `kind #id`
 * (as a bare navigation link once did) named nothing a reader would
 * recognize. Category, then a snippet, then the id, mirrors the same
 * fallback ResultRow already uses for the full Find results list.
 */
function jumpHeading(item: FindItem): string {
  if (item.title) return item.title;
  if (item.kind === "memory" && item.category) return item.category;
  if (item.snippet) return item.snippet.slice(0, 80);
  return `${item.kind} #${item.id}`;
}

/**
 * Jobs worth reaching straight from the palette without a destination or a
 * selection first — the plan's "run any action" promise, scoped to the
 * subset that genuinely needs none. Curate's own actions (forget, raise
 * confidence, …) always apply to a chosen set of chunks, so they stay on the
 * Curate page; these do not.
 */
const QUICK_JOBS: { name: string; label: string; hint: string }[] = [
  { name: "ingest", label: "Run: Ingest sessions", hint: "Import new sessions from every configured tool" },
  { name: "extract", label: "Run: Extract memory", hint: "LLM extraction pass over conversations with no memory yet" },
  { name: "embed", label: "Run: Generate embeddings", hint: "Embed chunks that semantic search cannot currently reach" },
  { name: "reflect", label: "Run: Reflection", hint: "Deduplicate, find contradictions, mark stale memory" },
  { name: "doctor", label: "Run: Diagnostics", hint: "Check the install, database and extensions" },
];

/**
 * ⌘K palette. For a single-user, keyboard-driven tool this is the primary
 * navigation surface — the sidebar is the discoverable fallback, not the
 * fast path.
 */
export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const navigate = useNavigate();
  const [sp] = useSearchParams();
  const { setTheme, resolved } = useTheme();
  const toast = useToast();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // A short wait rather than one request per keystroke — the same trade
  // Find's own query box makes, just local to the palette.
  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedQuery(query.trim()), 200);
    return () => window.clearTimeout(t);
  }, [query]);

  const run = (fn: () => void) => {
    setOpen(false);
    fn();
  };

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (!next) {
      // Reopening with the last query's results already on screen would
      // show a jump target from a search the reader has since forgotten.
      setQuery("");
      setDebouncedQuery("");
    }
  };

  // Jump targets: conversations, memory, projects, skills, prompts — one
  // query across everything Find already covers, so the palette can jump
  // straight to a specific record and not just a surface. Held off until two
  // characters so it never fires on a single accidental keystroke.
  const { data: jumpResults } = useQuery({
    queryKey: ["palette", "jump", debouncedQuery],
    queryFn: () => {
      const p = new URLSearchParams();
      p.set("q", debouncedQuery);
      p.set("limit", "6");
      return findApi.search(p);
    },
    enabled: debouncedQuery.length >= 2,
    staleTime: 30_000,
  });

  // Job availability, so a palette action can say why it is stuck rather
  // than silently doing nothing — the same information JobCard already
  // shows on Operate, read from the same cache key.
  const { data: opStatus } = useQuery({
    queryKey: ["operate", "status"],
    queryFn: operateApi.status,
    staleTime: 30_000,
  });
  const jobByName = new Map((opStatus?.jobs ?? []).map((j) => [j.name, j]));

  const runJob = useMutation({
    mutationFn: (name: string) => operateApi.run(name),
    onSuccess: (res) => {
      const spec = QUICK_JOBS.find((j) => j.name === res.name);
      toast.push({ message: `Started: ${spec?.label.replace(/^Run: /, "") ?? res.name}` });
    },
    onError: (e) => toast.push({ message: (e as Error).message, tone: "error", duration: 8000 }),
  });

  return (
    <Command.Dialog
      open={open}
      onOpenChange={handleOpenChange}
      label="Command palette"
      contentClassName="palette"
      // Escape and outside-click both close: a modal must always have a way
      // out that does not require finding a button.
    >
      <Command.Input
        value={query}
        onValueChange={setQuery}
        placeholder="Jump to, or run a command…"
        className="palette-input"
      />
      <Command.List className="palette-list">
        <Command.Empty className="palette-empty">No matches.</Command.Empty>

        {NAV_GROUPS.map((group) => (
          <Command.Group key={group} heading={group} className="palette-group">
            {NAV.filter((item) => item.group === group).map((item) => (
              <Command.Item
                key={item.to}
                value={`${item.label} ${item.description ?? ""}`}
                onSelect={() => run(() => navigate(carryProviders(item.to, sp)))}
                className="palette-item"
              >
                <item.icon size={15} aria-hidden />
                <span>{item.label}</span>
                <span className="palette-hint">{item.description}</span>
                <kbd className="palette-kbd">g {item.chord}</kbd>
              </Command.Item>
            ))}
          </Command.Group>
        ))}

        {debouncedQuery.length >= 2 && jumpResults && jumpResults.items.length > 0 && (
          <Command.Group heading="Jump to" className="palette-group">
            {jumpResults.items.map((item) => (
              <Command.Item
                // The value must contain the literal query text, or cmdk's own
                // fuzzy filter — which knows nothing about why the server
                // returned this row — can drop a result the API already
                // matched.
                key={`${item.kind}-${item.id}`}
                value={`${query} ${item.title ?? ""} ${item.kind}`}
                onSelect={() => run(() => navigate(carryProviders(routeFor(item), sp)))}
                className="palette-item"
              >
                <FileSearch size={15} aria-hidden />
                <span className="palette-item-label">{jumpHeading(item)}</span>
                <span className="palette-hint">
                  {item.kind}
                  {item.project ? ` · ${item.project}` : ""}
                </span>
              </Command.Item>
            ))}
          </Command.Group>
        )}

        <Command.Group heading="Actions" className="palette-group">
          {QUICK_JOBS.map((spec) => {
            const job = jobByName.get(spec.name);
            const blocked = job?.running ? "Already running" : job?.unavailable ?? null;
            return (
              <Command.Item
                key={spec.name}
                value={`${spec.label} ${spec.hint}`}
                onSelect={() =>
                  run(() => {
                    if (blocked) {
                      toast.push({ message: blocked, tone: "error", duration: 6000 });
                      navigate(carryProviders("/operate", sp));
                      return;
                    }
                    runJob.mutate(spec.name);
                    navigate(carryProviders("/operate", sp));
                  })
                }
                className="palette-item"
              >
                <Play size={15} aria-hidden />
                <span>{spec.label}</span>
                <span className="palette-hint">{blocked ?? spec.hint}</span>
              </Command.Item>
            );
          })}
          {/* The export lives on Operate. Reaching it meant knowing that, and
              scrolling past fourteen job cards — so it is findable by name
              here, and by the words people actually use for it. */}
          <Command.Item
            value="Export as Markdown Obsidian vault markdown files backup"
            onSelect={() => run(() => navigate(carryProviders("/operate#export", sp)))}
            className="palette-item"
          >
            <Download size={15} aria-hidden />
            <span>Export as Markdown</span>
            <span className="palette-hint">One folder per project, for Obsidian or any editor</span>
          </Command.Item>
        </Command.Group>

        <Command.Group heading="Theme" className="palette-group">
          <Command.Item value="theme light" onSelect={() => run(() => setTheme("light"))} className="palette-item">
            <Sun size={15} aria-hidden />
            <span>Light</span>
            {resolved === "light" && <span className="palette-hint">current</span>}
          </Command.Item>
          <Command.Item value="theme dark" onSelect={() => run(() => setTheme("dark"))} className="palette-item">
            <Moon size={15} aria-hidden />
            <span>Dark</span>
            {resolved === "dark" && <span className="palette-hint">current</span>}
          </Command.Item>
          <Command.Item value="theme system" onSelect={() => run(() => setTheme("system"))} className="palette-item">
            <Monitor size={15} aria-hidden />
            <span>Match system</span>
          </Command.Item>
        </Command.Group>
      </Command.List>
    </Command.Dialog>
  );
}
