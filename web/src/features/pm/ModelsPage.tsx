/** /pm/models — AI-Modelle: Cline/Cursor-style provider & model management.
 *  Users add STANDARD providers (name, type, optional base URL, API key) —
 *  models are then fetched live from the provider — plus CUSTOM model ids
 *  of their own. Every enabled provider shows up as an extra entry in
 *  GET /pm/ai-catalog, so it becomes selectable in the Role editor
 *  (CatalogForms.tsx's AiBindingPicker, shared.tsx) without any change
 *  there — it already iterates whatever the catalog returns. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";

import { pmApi, type PmAiProvider, type PmAiProviderType } from "@/lib/api";
import { useLang } from "./i18n";
import {
  EmptyState,
  ErrorState,
  InlineConfirmButton,
  PmHeaderBar,
  SkeletonRows,
  plural,
} from "./shared";
import "@/styles/pm.css";

const PROVIDER_TYPES: PmAiProviderType[] = [
  "openai", "anthropic", "mistral", "google", "openrouter", "ollama", "openai_compatible",
];

// Provider/brand names — deliberately identical in both languages, the same
// way "Claude Code" stays untranslated elsewhere on the PM surface.
const PROVIDER_TYPE_LABELS: Record<PmAiProviderType, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  mistral: "Mistral",
  google: "Google Gemini",
  openrouter: "OpenRouter",
  ollama: "Ollama",
  openai_compatible: "OpenAI-compatible",
};

const BASE_URL_REQUIRED = new Set<PmAiProviderType>(["ollama", "openai_compatible"]);

// ── Create/edit form ─────────────────────────────────────────────────────

interface ProviderFormValues {
  name: string;
  provider_type: PmAiProviderType;
  base_url: string;
  /** Raw text input. Empty means "leave unchanged" when editing (the
   *  "unverändert lassen" placeholder) or "no key" when creating. */
  api_key: string;
  enabled: boolean;
}

function providerFormFrom(p?: PmAiProvider): ProviderFormValues {
  return {
    name: p?.name ?? "",
    provider_type: p?.provider_type ?? "openai",
    base_url: p?.base_url ?? "",
    api_key: "",
    enabled: p?.enabled ?? true,
  };
}

function ProviderForm({
  initial,
  submitLabel,
  busy,
  error,
  onSubmit,
  onCancel,
}: {
  initial?: PmAiProvider;
  submitLabel: string;
  busy: boolean;
  error: unknown;
  onSubmit: (draft: ProviderFormValues) => void;
  onCancel: () => void;
}) {
  const { t } = useLang();
  const [d, setD] = useState<ProviderFormValues>(() => providerFormFrom(initial));
  const set = <K extends keyof ProviderFormValues>(k: K, v: ProviderFormValues[K]) =>
    setD((prev) => ({ ...prev, [k]: v }));

  const baseUrlRequired = BASE_URL_REQUIRED.has(d.provider_type);

  return (
    <form
      className="pm-editor"
      onSubmit={(e) => {
        e.preventDefault();
        if (d.name.trim() && (!baseUrlRequired || d.base_url.trim())) onSubmit(d);
      }}
    >
      <div className="pm-editor-grid">
        <label className="pm-field">
          <span className="pm-label">{t.forms.nameLabel}</span>
          <input
            className="pm-input"
            value={d.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder={t.modelsPage.providerNamePlaceholder}
            required
          />
        </label>
        <label className="pm-field">
          <span className="pm-label">{t.modelsPage.typeSelectLabel}</span>
          <select
            className="pm-input"
            value={d.provider_type}
            onChange={(e) => set("provider_type", e.target.value as PmAiProviderType)}
          >
            {PROVIDER_TYPES.map((pt) => (
              <option key={pt} value={pt}>
                {PROVIDER_TYPE_LABELS[pt]}
              </option>
            ))}
          </select>
        </label>
        <label className="pm-field">
          <span className="pm-label">{t.modelsPage.baseUrlLabel}</span>
          <input
            className="pm-input"
            value={d.base_url}
            onChange={(e) => set("base_url", e.target.value)}
            placeholder={
              baseUrlRequired
                ? t.modelsPage.baseUrlPlaceholderRequired
                : t.modelsPage.baseUrlPlaceholderOptional
            }
            required={baseUrlRequired}
          />
          {baseUrlRequired && <span className="pm-field-hint">{t.modelsPage.baseUrlRequiredHint}</span>}
        </label>
        <label className="pm-field">
          <span className="pm-label">{t.modelsPage.apiKeyLabel}</span>
          <input
            className="pm-input"
            type="password"
            autoComplete="off"
            value={d.api_key}
            onChange={(e) => set("api_key", e.target.value)}
            placeholder={
              initial?.api_key_set
                ? t.modelsPage.apiKeyPlaceholderUnchanged
                : t.modelsPage.apiKeyPlaceholderNew
            }
          />
        </label>
        <label className="pm-provider-toggle pm-provider-toggle-field">
          <input
            type="checkbox"
            checked={d.enabled}
            onChange={(e) => set("enabled", e.target.checked)}
          />
          <span>{t.modelsPage.enabledLabel}</span>
        </label>
      </div>

      <div className="pm-editor-actions">
        <button
          type="submit"
          className="button"
          disabled={busy || !d.name.trim() || (baseUrlRequired && !d.base_url.trim())}
        >
          {busy ? t.common.saving : submitLabel}
        </button>
        <button type="button" className="button pm-button-quiet" onClick={onCancel}>
          {t.forms.cancel}
        </button>
      </div>
      {error != null && (
        <p className="pm-field-error" role="alert">
          {t.forms.saveFailed((error as Error).message)}
        </p>
      )}
    </form>
  );
}

// ── Custom-model chip editor ─────────────────────────────────────────────

function CustomModelsEditor({
  value,
  busy,
  onChange,
}: {
  value: string[];
  busy: boolean;
  onChange: (models: string[]) => void;
}) {
  const { t } = useLang();
  const [draft, setDraft] = useState("");

  function add() {
    const v = draft.trim();
    if (!v || value.includes(v)) return;
    onChange([...value, v]);
    setDraft("");
  }

  return (
    <div className="pm-doclist">
      {value.length > 0 && (
        <div className="pm-skillpicker-chips">
          {value.map((m) => (
            <button
              key={m}
              type="button"
              className="pm-chip"
              disabled={busy}
              onClick={() => onChange(value.filter((v) => v !== m))}
            >
              {m}
              <X size={12} aria-hidden />
            </button>
          ))}
        </div>
      )}
      <div className="pm-doclist-add">
        <input
          className="pm-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={t.modelsPage.customModelPlaceholder}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <button type="button" className="button pm-button-flush" onClick={add} disabled={!draft.trim() || busy}>
          {t.common.add}
        </button>
      </div>
    </div>
  );
}

// ── Row ──────────────────────────────────────────────────────────────────

function ProviderRow({ provider }: { provider: PmAiProvider }) {
  const { t } = useLang();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);

  function invalidateAll() {
    queryClient.invalidateQueries({ queryKey: ["pm-ai-providers"] });
    queryClient.invalidateQueries({ queryKey: ["pm-ai-catalog"] });
    queryClient.invalidateQueries({ queryKey: ["pm-overview"] });
  }

  const patch = useMutation({
    mutationFn: (draft: ProviderFormValues) =>
      pmApi.patchAiProvider(provider.id, {
        name: draft.name,
        provider_type: draft.provider_type,
        base_url: draft.base_url.trim() || null,
        enabled: draft.enabled,
        ...(draft.api_key.trim() ? { api_key: draft.api_key.trim() } : {}),
      }),
    onSuccess: () => {
      setEditing(false);
      invalidateAll();
    },
  });

  const toggleEnabled = useMutation({
    mutationFn: (enabled: boolean) => pmApi.patchAiProvider(provider.id, { enabled }),
    onSuccess: invalidateAll,
  });

  const patchCustomModels = useMutation({
    mutationFn: (models: string[]) => pmApi.patchAiProvider(provider.id, { custom_models: models }),
    onSuccess: invalidateAll,
  });

  const del = useMutation({
    mutationFn: () => pmApi.deleteAiProvider(provider.id),
    onSuccess: invalidateAll,
  });

  const refresh = useMutation({
    mutationFn: () => pmApi.refreshAiProviderModels(provider.id),
  });

  return (
    <li className="pm-cat-row">
      <div className="pm-cat-head">
        <span className="pm-cat-name">{provider.name}</span>
        <div className="pm-cat-head-actions">
          <button
            type="button"
            className="pm-linklike"
            aria-expanded={editing}
            onClick={() => setEditing((e) => !e)}
          >
            {editing ? t.common.close : t.common.edit}
          </button>
          <InlineConfirmButton
            className="pm-linklike pm-linklike-danger"
            disabled={del.isPending}
            pending={del.isPending}
            onConfirm={() => del.mutate()}
          >
            {del.isPending ? t.catalog.deleting : t.catalog.delete}
          </InlineConfirmButton>
        </div>
      </div>

      {editing ? (
        <ProviderForm
          initial={provider}
          submitLabel={t.catalog.saveChanges}
          busy={patch.isPending}
          error={patch.isError ? patch.error : null}
          onSubmit={(draft) => patch.mutate(draft)}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <div className="pm-cat-summary">
          <code className="pm-cat-ai">{PROVIDER_TYPE_LABELS[provider.provider_type]}</code>
          {provider.base_url && <span className="pm-cat-fact">{provider.base_url}</span>}
          <span className={`pm-provider-badge${provider.api_key_set ? " is-set" : ""}`}>
            {provider.api_key_set ? t.modelsPage.keySet : t.modelsPage.noKey}
          </span>
          <label className="pm-provider-toggle">
            <input
              type="checkbox"
              checked={provider.enabled}
              disabled={toggleEnabled.isPending}
              onChange={(e) => toggleEnabled.mutate(e.target.checked)}
            />
            <span>{t.modelsPage.enabledLabel}</span>
          </label>
          <span className="pm-cat-fact tabular">
            {plural(provider.custom_models.length, t.modelsPage.customModelOne, t.modelsPage.customModelMany)}
          </span>
        </div>
      )}
      {del.isError && (
        <p className="pm-field-error" role="alert">
          {t.catalog.deleteFailed((del.error as Error).message)}
        </p>
      )}

      <div className="pm-provider-models">
        <div className="pm-field">
          <span className="pm-label">{t.modelsPage.customModelsLabel}</span>
          <CustomModelsEditor
            value={provider.custom_models}
            busy={patchCustomModels.isPending}
            onChange={(models) => patchCustomModels.mutate(models)}
          />
        </div>

        <div>
          <button
            type="button"
            className="button pm-button-flush pm-button-quiet"
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending}
          >
            {refresh.isPending ? t.modelsPage.loadingModels : t.modelsPage.loadModels}
          </button>
          {refresh.isError && (
            <p className="pm-field-error" role="alert">
              {t.modelsPage.loadModelsFailed((refresh.error as Error).message)}
            </p>
          )}
          {refresh.isSuccess && (
            <div>
              {refresh.data.unavailable ? (
                <p className="pm-field-hint">
                  {t.modelsPage.fetchedModelsUnavailable} {refresh.data.error}
                </p>
              ) : refresh.data.models.length === 0 ? (
                <p className="pm-field-hint">{t.modelsPage.fetchedModelsEmpty}</p>
              ) : (
                <>
                  <p className="pm-field-hint">{t.modelsPage.fetchedModelsCount(String(refresh.data.models.length))}</p>
                  <ul className="pm-provider-models-list">
                    {refresh.data.models.map((m) => (
                      <li key={m}>{m}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────

export function ModelsPage() {
  const { t } = useLang();
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const { data, isPending, error, refetch } = useQuery({
    queryKey: ["pm-ai-providers"],
    queryFn: pmApi.listAiProviders,
  });

  const create = useMutation({
    mutationFn: (draft: ProviderFormValues) =>
      pmApi.createAiProvider({
        name: draft.name,
        provider_type: draft.provider_type,
        base_url: draft.base_url.trim() || null,
        api_key: draft.api_key.trim() || null,
        enabled: draft.enabled,
      }),
    onSuccess: () => {
      setCreating(false);
      queryClient.invalidateQueries({ queryKey: ["pm-ai-providers"] });
      queryClient.invalidateQueries({ queryKey: ["pm-ai-catalog"] });
      queryClient.invalidateQueries({ queryKey: ["pm-overview"] });
    },
  });

  return (
    <section className="pm-page">
      <header className="page-header">
        <PmHeaderBar items={[{ label: t.common.projectManagement, to: "/pm" }, { label: t.breadcrumb.models }]} />
        <div className="page-header-row pm-header-row">
          <div>
            <h1 className="page-title">{t.modelsPage.h1}</h1>
            <p className="page-subtitle">{t.modelsPage.subtitle}</p>
          </div>
          {!creating && (
            <button type="button" className="button pm-button-flush" onClick={() => setCreating(true)}>
              <Plus size={14} aria-hidden />
              {t.modelsPage.create}
            </button>
          )}
        </div>
      </header>

      {creating && (
        <div className="pm-cat-create">
          <ProviderForm
            submitLabel={t.modelsPage.create}
            busy={create.isPending}
            error={create.isError ? create.error : null}
            onSubmit={(draft) => create.mutate(draft)}
            onCancel={() => setCreating(false)}
          />
        </div>
      )}

      {isPending ? (
        <SkeletonRows n={3} />
      ) : error ? (
        <ErrorState title={t.modelsPage.errorTitle} error={error} onRetry={refetch} />
      ) : data.providers.length === 0 ? (
        <EmptyState title={t.modelsPage.emptyTitle}>
          <p>{t.modelsPage.emptyBody}</p>
        </EmptyState>
      ) : (
        <ul className="pm-cat-list">
          {data.providers.map((p) => (
            <ProviderRow key={p.id} provider={p} />
          ))}
        </ul>
      )}
    </section>
  );
}
