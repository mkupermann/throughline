import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { pmApi, request, type PmAiProvider } from "@/lib/api";
import { t } from "@/lib/ui";
import { useLanguage } from "@/lib/language";

interface Binding {
  purpose: string;
  provider_id: number | null;
  cli: string | null;
  model: string;
  embedding_dim: number | null;
}
interface Settings {
  purposes: string[];
  bindings: Binding[];
  bridge: { clis: Record<string, { installed: boolean }>; error?: string };
}
const labels: Record<string, string> = {
  answer: "Answers",
  titles: "Conversation titles",
  project_names: "Project names",
  extraction: "Knowledge and entities",
  reflection: "Reflection",
  embeddings: "Search embeddings",
};

function Purpose({
  purpose,
  binding,
  providers,
  bridge,
  testing,
  setTesting,
}: {
  testing: string | null;
  setTesting: (value: string | null) => void;
  purpose: string;
  binding?: Binding;
  providers: PmAiProvider[];
  bridge: Settings["bridge"];
}) {
  const qc = useQueryClient();
  const [choice, setChoice] = useState(
    binding?.cli
      ? `cli:${binding.cli}`
      : binding?.provider_id
        ? `api:${binding.provider_id}`
        : "",
  );
  const [model, setModel] = useState(binding?.model ?? "");
  const [dim, setDim] = useState(binding?.embedding_dim ?? 768);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const provider = providers.find((p) => `api:${p.id}` === choice);
  const cli = choice.startsWith("cli:") ? choice.slice(4) : null;
  const payload = {
    provider_id: provider?.id ?? null,
    cli,
    model,
    embedding_dim: purpose === "embeddings" ? dim : null,
  };
  const liveModels = useQuery({
    queryKey: ["purpose-models", provider?.id],
    queryFn: () => pmApi.refreshAiProviderModels(provider!.id),
    enabled: !!provider,
    staleTime: 60000,
  });
  const models = [
    ...new Set([
      ...(provider?.custom_models ?? []),
      ...(liveModels.data?.models ?? []),
    ]),
  ];
  return (
    <form
      className="ai-purpose"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setMessage("");
        try {
          await request(`/ai/settings/${purpose}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          setDirty(false);
          setMessage(t("Saved"));
          await qc.invalidateQueries({ queryKey: ["ai-settings"] });
          await qc.invalidateQueries({ queryKey: ["operate"] });
        } catch (e) {
          setMessage((e as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <h2>{t(labels[purpose] ?? purpose)}</h2>
      <div className="ai-purpose-fields">
        <label>
          {t("Provider or installed CLI")}
          <select
            value={choice}
            onChange={(e) => {
              setChoice(e.target.value);
              setModel("");
              setDirty(true);
            }}
            required
          >
            <option value="">{t("Select explicitly")}</option>
            {providers
              .filter(
                (p) =>
                  p.enabled &&
                  (purpose !== "embeddings" ||
                    [
                      "ollama",
                      "openai",
                      "openai_compatible",
                      "mistral",
                      "openrouter",
                    ].includes(p.provider_type)),
              )
              .map((p) => (
                <option key={p.id} value={`api:${p.id}`}>
                  {p.name} · {p.provider_type}
                </option>
              ))}
            {purpose !== "embeddings" &&
              ["codex", "vibe", "claude"].map((c) => (
                <option
                  key={c}
                  value={`cli:${c}`}
                  disabled={!bridge.clis[c]?.installed}
                >
                  {c} CLI
                  {bridge.clis[c]?.installed ? "" : ` · ${t("not available")}`}
                </option>
              ))}
          </select>
        </label>
        <label>
          {t("Model")}
          <input
            list={`models-${purpose}`}
            value={model}
            onChange={(e) => {
              setModel(e.target.value);
              setDirty(true);
            }}
            required={!cli}
            maxLength={200}
            placeholder={cli ? t("CLI default if empty") : t("Model ID")}
          />
          <datalist id={`models-${purpose}`}>
            {models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </label>
        {purpose === "embeddings" && (
          <label>
            {t("Vector dimensions")}
            <select
              value={dim}
              onChange={(e) => {
                setDim(Number(e.target.value));
                setDirty(true);
              }}
            >
              <option value={768}>768</option>
              <option value={1536}>1536</option>
            </select>
          </label>
        )}
        <button className="button" disabled={busy || !choice}>
          {t("Save")}
        </button>
        <button
          type="button"
          className="button"
          disabled={busy || dirty || !binding || testing !== null}
          onClick={async () => {
            setBusy(true);
            setTesting(purpose);
            setMessage("");
            try {
              const result = await request<{ ok: boolean; error?: string }>(
                `/ai/settings/${purpose}/test`,
                { method: "POST" },
              );
              setMessage(
                result.ok
                  ? t("Connection verified")
                  : t(result.error ?? "Connection failed"),
              );
            } catch (e) {
              setMessage((e as Error).message);
            } finally {
              setBusy(false);
              setTesting(null);
            }
          }}
        >
          {t(testing === purpose ? "Testing connection…" : "Test connection")}
        </button>
      </div>
      <p>
        {provider
          ? `${t("Destination")}: ${provider.base_url || provider.provider_type}`
          : cli
            ? t(
                "Uses the host CLI login and its configured service. The service may process data remotely.",
              )
            : t(
                "No explicit selection saved. The existing installation configuration is used until you save a selection.",
              )}
      </p>
      {cli === "vibe" && (
        <p>
          {t(
            "For Vibe, enter a model alias configured in Vibe. Empty uses its current default.",
          )}
        </p>
      )}
      {purpose === "embeddings" && (
        <p>
          {t(
            "Chat CLIs do not produce embeddings. Select a matching embedding model and vector dimension.",
          )}
        </p>
      )}
      {liveModels.data?.unavailable && (
        <p>
          {t("Model list unavailable. You can enter a model ID and test it.")}
        </p>
      )}
      {message && <p role="status">{message}</p>}
    </form>
  );
}
export function AiSettings() {
  useLanguage();
  const [testing, setTesting] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["ai-settings"],
    queryFn: () => request<Settings>("/ai/settings"),
  });
  const providers = useQuery({
    queryKey: ["ai-providers"],
    queryFn: pmApi.listAiProviders,
  });
  return (
    <section className="ai-settings">
      <header className="page-header">
        <h1>{t("AI settings")}</h1>
        <p>
          {t(
            "Choose where each kind of work is processed. A saved selection never silently falls back to another service.",
          )}
        </p>
      </header>
      <p>
        <Link to="/settings/providers">
          {t("Manage API providers, keys and model lists")}
        </Link>
      </p>
      <p>
        {t(
          "Connection tests send only a synthetic test prompt. Processing sends the selected source excerpts to the configured service. CLI credentials remain on the host; API keys use the existing local provider store.",
        )}
      </p>
      {(q.error || providers.error) && (
        <p role="alert">{(q.error || providers.error)?.message}</p>
      )}
      {q.isPending && <p>{t("Loading…")}</p>}
      {q.data?.bridge.error && (
        <p>
          {t(
            "Host CLI bridge is unavailable. Configure or start it on the host.",
          )}
        </p>
      )}
      {q.data?.purposes.map((p) => (
        <Purpose
          testing={testing}
          setTesting={setTesting}
          key={p}
          purpose={p}
          binding={q.data.bindings.find((b) => b.purpose === p)}
          providers={providers.data?.providers ?? []}
          bridge={q.data.bridge}
        />
      ))}
    </section>
  );
}
