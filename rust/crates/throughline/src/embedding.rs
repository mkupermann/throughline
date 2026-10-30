//! Embedding backends. Local-first only: Ollama (or any Ollama-compatible
//! `/api/embed` server). Hosted OpenAI embeddings are intentionally not a
//! fallback — transcripts stay on this machine.

use anyhow::{anyhow, Result};
use serde::Deserialize;

/// Run blocking HTTP on a dedicated OS thread.
///
/// `reqwest::blocking` starts its own Tokio runtime. Calling it from inside the
/// server's runtime (even via `spawn_blocking`) can trip
/// "Cannot start a runtime from within a runtime" and kill the process.
pub(crate) fn isolated_blocking<T, F>(f: F) -> Result<T>
where
    T: Send + 'static,
    F: FnOnce() -> Result<T> + Send + 'static,
{
    match std::thread::spawn(f).join() {
        Ok(result) => result,
        Err(_) => Err(anyhow!("blocking http thread panicked")),
    }
}

/// Model used when `OLLAMA_EMBED_MODEL` is not set. It stays the English-first
/// model so an existing install does not switch on restart; the multilingual
/// model is opt-in (see the `granite-embedding` profile).
pub const DEFAULT_EMBED_MODEL: &str = "nomic-embed-text";

/// Columns of `embeddings` that exist today. A dimension without a column is
/// refused until a migration adds one.
const AVAILABLE_COLUMNS: [(usize, &str); 2] = [(768, "embedding_768"), (1536, "embedding_1536")];

/// Per-model settings: prefixes, vector size, column and the two cosine
/// thresholds that depend on how the model spreads its scores.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ModelProfile {
    pub query_prefix: &'static str,
    pub document_prefix: &'static str,
    pub dim: usize,
    pub column: &'static str,
    /// `as_of` recall admits a note at or above this cosine.
    pub as_of_min_cosine: f64,
    /// Consolidation treats two notes at or above this cosine as one.
    pub consolidation_min_similarity: f64,
}

/// Baseline thresholds: today's constants, measured for `nomic-embed-text`.
const BASE_AS_OF: f64 = 0.5;
const BASE_CONSOLIDATION: f64 = 0.92;

const fn profile(
    query_prefix: &'static str,
    document_prefix: &'static str,
    dim: usize,
    column: &'static str,
    as_of_min_cosine: f64,
    consolidation_min_similarity: f64,
) -> ModelProfile {
    ModelProfile {
        query_prefix,
        document_prefix,
        dim,
        column,
        as_of_min_cosine,
        consolidation_min_similarity,
    }
}

/// Profile by model name. The tag (`:278m`) is ignored, except for the
/// Ollama library name `granite-embedding`, whose tags are different models.
pub fn model_profile(model: &str) -> Option<ModelProfile> {
    let (name, tag) = model.split_once(':').unwrap_or((model, ""));
    Some(match (name, tag) {
        ("nomic-embed-text", _) => {
            profile("", "", 768, "embedding_768", BASE_AS_OF, BASE_CONSOLIDATION)
        }
        // Winner of the multilingual measurement; thresholds measured on the
        // Docker Hub file. `granite-embedding:278m` from the Ollama library is
        // the same model and quantisation, not a byte-identical file. Its
        // `30m` tag is an English model with another dimension and takes the
        // unknown-model path.
        ("granite-embedding-multilingual", _) | ("granite-embedding", "278m") => {
            profile("", "", 768, "embedding_768", 0.68, 0.93)
        }
        // The three profiles below were measured for ranking only; their
        // thresholds were not, so they keep the baseline values.
        ("embeddinggemma", _) => profile(
            "task: search result | query: ",
            "title: none | text: ",
            768,
            "embedding_768",
            BASE_AS_OF,
            BASE_CONSOLIDATION,
        ),
        ("nomic-embed-text-v2-moe", _) => profile(
            "search_query: ",
            "search_document: ",
            768,
            "embedding_768",
            BASE_AS_OF,
            BASE_CONSOLIDATION,
        ),
        ("qwen3-embedding", _) => profile(
            "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:",
            "",
            1024,
            "embedding_1024",
            BASE_AS_OF,
            BASE_CONSOLIDATION,
        ),
        _ => return None,
    })
}

/// Profile for `model` with the vector size its first embed call returned.
/// A known model keeps its profile (a size mismatch is an error); an unknown
/// one gets empty prefixes, the baseline thresholds and the column of its
/// dimension, if there is one.
pub fn profile_for(model: &str, dim: usize) -> Result<ModelProfile> {
    let p = match model_profile(model) {
        Some(p) => p,
        None => {
            let column = AVAILABLE_COLUMNS
                .iter()
                .find(|(d, _)| *d == dim)
                .map(|(_, c)| *c)
                .ok_or_else(|| {
                    anyhow!(
                        "embedding model {model} returns {dim} dimensions; \
                         supported are 768 and 1536"
                    )
                })?;
            profile("", "", dim, column, BASE_AS_OF, BASE_CONSOLIDATION)
        }
    };
    if p.dim != dim {
        return Err(anyhow!(
            "embedding model {model} returned {dim} dimensions, its profile says {}",
            p.dim
        ));
    }
    if !AVAILABLE_COLUMNS.iter().any(|(_, c)| *c == p.column) {
        return Err(anyhow!(
            "embedding model {model} needs column {} ({dim} dimensions), which no migration \
             has added yet",
            p.column
        ));
    }
    Ok(p)
}

/// Whether a text is a search question or a stored text; some models want a
/// different prefix for each.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TextKind {
    Query,
    Document,
}

#[derive(Debug, Clone)]
pub struct EmbeddingBackend {
    pub name: String,
    pub model: String,
    pub base_url: String,
    /// Which embeddings column to use (from the profile).
    pub column: &'static str,
    pub dim: usize,
    pub profile: ModelProfile,
}

pub fn resolve() -> Result<EmbeddingBackend> {
    let preference = std::env::var("THROUGHLINE_EMBED_BACKEND")
        .or_else(|_| std::env::var("EMBEDDING_BACKEND"))
        .unwrap_or_else(|_| "auto".into())
        .to_lowercase();
    let ollama_url = std::env::var("OLLAMA_HOST")
        .or_else(|_| std::env::var("OLLAMA_URL"))
        .unwrap_or_else(|_| "http://localhost:11434".into())
        .trim_end_matches('/')
        .to_string();
    let ollama_model = configured_model();

    match preference.as_str() {
        "openai" | "openai-compatible" | "hosted" => Err(anyhow!(
            "hosted OpenAI embeddings are disabled; use Ollama ({DEFAULT_EMBED_MODEL}) locally"
        )),
        _ => ollama(&ollama_url, &ollama_model),
    }
}

/// The embedding model this process is set to: `OLLAMA_EMBED_MODEL`, else
/// `THROUGHLINE_EMBED_MODEL`, else [`DEFAULT_EMBED_MODEL`]. Compose forwards an
/// unset variable as an empty one, so an empty value counts as unset.
pub fn configured_model() -> String {
    let set = |name: &str| std::env::var(name).ok().filter(|v| !v.trim().is_empty());
    selected_model(
        set("OLLAMA_EMBED_MODEL")
            .or_else(|| set("THROUGHLINE_EMBED_MODEL"))
            .as_deref(),
    )
}

/// The trimmed value, or the default when it is missing or blank.
fn selected_model(value: Option<&str>) -> String {
    match value.map(str::trim) {
        Some(m) if !m.is_empty() => m.to_string(),
        _ => DEFAULT_EMBED_MODEL.to_string(),
    }
}

/// Model that `THROUGHLINE_EMBED_TARGET_MODEL` names, to fill beside the
/// active one before switching. Empty or equal to the active model means
/// there is no target; an unusable target is an error naming it.
pub fn resolve_target() -> Result<Option<EmbeddingBackend>> {
    let active = resolve()?;
    let value = std::env::var("THROUGHLINE_EMBED_TARGET_MODEL").ok();
    resolve_target_from(value.as_deref(), &active)
}

/// [`resolve_target`] for an explicit value. The target uses the active
/// backend's server, so the same remote-model lock and `/api/tags` check apply.
pub fn resolve_target_from(
    value: Option<&str>,
    active: &EmbeddingBackend,
) -> Result<Option<EmbeddingBackend>> {
    let model = match value.map(str::trim) {
        None | Some("") => return Ok(None),
        Some(m) if m == active.model => return Ok(None),
        Some(m) => m,
    };
    ollama(&active.base_url, model)
        .map(Some)
        .map_err(|e| anyhow!("{model}: {e}"))
}

fn ollama(base: &str, model: &str) -> Result<EmbeddingBackend> {
    // Fail early: no request, not even the model list, goes to a remote server.
    crate::llm::ensure_ollama_local_or_allowed(base, model)?;
    let base = base.to_string();
    let model = model.to_string();
    let found = {
        let (base, model) = (base.clone(), model.clone());
        isolated_blocking(move || {
            let client = reqwest::blocking::Client::builder()
                .timeout(std::time::Duration::from_secs(5))
                .build()?;
            let tags: serde_json::Value = client
                .get(format!("{base}/api/tags"))
                .send()
                .map_err(|_| anyhow!("ollama unreachable at {base}"))?
                .json()?;
            let available = tags["models"]
                .as_array()
                .map(|a| {
                    a.iter().any(|m| {
                        m["name"]
                            .as_str()
                            .map(|n| n.starts_with(&model) || n == model)
                            == Some(true)
                    })
                })
                .unwrap_or(false);
            if !available {
                return Err(anyhow!("ollama is missing embedding model {model}"));
            }
            Ok(())
        })
    };
    found?;
    let mut backend = EmbeddingBackend {
        name: "ollama".into(),
        model: model.clone(),
        base_url: base,
        column: "embedding_768",
        dim: 768,
        profile: profile("", "", 768, "embedding_768", BASE_AS_OF, BASE_CONSOLIDATION),
    };
    let profile = match model_profile(&model) {
        // Known model: trust the profile, no extra request.
        Some(p) => profile_for(&model, p.dim)?,
        // Unknown model: its dimension comes from one real embed call.
        None => {
            let dim = embed_text(&backend, "dimension probe", TextKind::Document)?.len();
            profile_for(&model, dim)?
        }
    };
    backend.column = profile.column;
    backend.dim = profile.dim;
    backend.profile = profile;
    Ok(backend)
}

/// Ollama `/api/embed` returns `{"embeddings":[[...]]}`; legacy
/// `/api/embeddings` returned `{"embedding":[...]}`. Accept both.
#[derive(Deserialize)]
struct OllamaEmbedResponse {
    embeddings: Option<Vec<Vec<f32>>>,
    embedding: Option<Vec<f32>>,
}

/// Request body for `/api/embed`: the text with the profile's prefix for `kind`.
pub fn embed_body(backend: &EmbeddingBackend, text: &str, kind: TextKind) -> serde_json::Value {
    let prefix = match kind {
        TextKind::Query => backend.profile.query_prefix,
        TextKind::Document => backend.profile.document_prefix,
    };
    serde_json::json!({ "model": backend.model, "input": format!("{prefix}{text}") })
}

pub fn embed_text(backend: &EmbeddingBackend, text: &str, kind: TextKind) -> Result<Vec<f32>> {
    // Every caller sends text through here, so the lock is checked before the POST.
    crate::llm::ensure_ollama_local_or_allowed(&backend.base_url, &backend.model)?;
    let backend = backend.clone();
    let body = embed_body(&backend, text, kind);
    isolated_blocking(move || {
        let client = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(60))
            .build()?;
        let resp: OllamaEmbedResponse = client
            .post(format!("{}/api/embed", backend.base_url))
            .json(&body)
            .send()?
            .error_for_status()?
            .json()?;
        if let Some(mut rows) = resp.embeddings {
            return rows
                .pop()
                .filter(|v| !v.is_empty())
                .ok_or_else(|| anyhow!("ollama embed returned no vector"));
        }
        resp.embedding
            .filter(|v| !v.is_empty())
            .ok_or_else(|| anyhow!("ollama embed returned no vector"))
    })
}

pub fn label(backend: &EmbeddingBackend) -> String {
    format!("{}/{} ({}d)", backend.name, backend.model, backend.dim)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn backend_for(model: &str) -> EmbeddingBackend {
        let profile = profile_for(model, 768).expect("profile");
        EmbeddingBackend {
            name: "ollama".into(),
            model: model.into(),
            base_url: "http://127.0.0.1:11434".into(),
            column: profile.column,
            dim: profile.dim,
            profile,
        }
    }

    #[test]
    fn empty_model_variable_counts_as_unset() {
        assert_eq!(selected_model(None), DEFAULT_EMBED_MODEL);
        assert_eq!(selected_model(Some("")), DEFAULT_EMBED_MODEL);
        assert_eq!(selected_model(Some("  ")), DEFAULT_EMBED_MODEL);
        assert_eq!(selected_model(Some("x")), "x");
        assert_eq!(selected_model(Some(" x ")), "x");
    }

    #[test]
    fn target_equal_to_active_is_ignored() {
        let active = backend_for(DEFAULT_EMBED_MODEL);
        assert!(resolve_target_from(None, &active).unwrap().is_none());
        assert!(resolve_target_from(Some(""), &active).unwrap().is_none());
        assert!(resolve_target_from(Some("  "), &active).unwrap().is_none());
        assert!(resolve_target_from(Some(DEFAULT_EMBED_MODEL), &active)
            .unwrap()
            .is_none());
        assert!(
            resolve_target_from(Some(&format!(" {DEFAULT_EMBED_MODEL} ")), &active)
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn bogus_target_names_the_model() {
        let base = std::env::var("OLLAMA_HOST").unwrap_or_else(|_| "http://127.0.0.1:11434".into());
        let reachable = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(2))
            .build()
            .unwrap()
            .get(format!("{base}/api/tags"))
            .send()
            .is_ok();
        if !reachable {
            eprintln!("skipped: ollama unreachable at {base}");
            return;
        }
        let mut active = backend_for(DEFAULT_EMBED_MODEL);
        active.base_url = base;
        let err = resolve_target_from(Some("does-not-exist"), &active).unwrap_err();
        assert!(err.to_string().contains("does-not-exist"), "{err}");
    }

    #[test]
    fn profile_known_models() {
        let base = model_profile("nomic-embed-text").expect("base");
        assert_eq!((base.dim, base.column), (768, "embedding_768"));
        assert_eq!((base.query_prefix, base.document_prefix), ("", ""));
        assert_eq!(
            (base.as_of_min_cosine, base.consolidation_min_similarity),
            (0.5, 0.92)
        );
        let win = model_profile("granite-embedding-multilingual:278m").expect("winner");
        assert_eq!((win.dim, win.column), (768, "embedding_768"));
        assert_eq!(
            (win.as_of_min_cosine, win.consolidation_min_similarity),
            (0.68, 0.93)
        );
        assert_eq!(win.query_prefix, "");
        let v2 = model_profile("nomic-embed-text-v2-moe:475m").expect("v2");
        assert_eq!(v2.query_prefix, "search_query: ");
        assert_eq!(v2.document_prefix, "search_document: ");
        let gemma = model_profile("embeddinggemma:300m-q8").expect("gemma");
        assert_eq!(gemma.document_prefix, "title: none | text: ");
        let qwen = model_profile("qwen3-embedding:0.6b-f16").expect("qwen");
        assert_eq!(qwen.dim, 1024);
        assert!(qwen.query_prefix.starts_with("Instruct: "));
        assert!(model_profile("something-else").is_none());
        assert_eq!(DEFAULT_EMBED_MODEL, "nomic-embed-text");
    }

    #[test]
    fn granite_profile_matches_both_names_but_not_30m() {
        let measured = model_profile("granite-embedding-multilingual:278m").expect("hub name");
        assert_eq!(
            model_profile("granite-embedding-multilingual"),
            Some(measured)
        );
        assert_eq!(model_profile("granite-embedding:278m"), Some(measured));
        // The 30m tag is an English model with 384 dimensions: unknown path.
        assert!(model_profile("granite-embedding:30m").is_none());
        assert!(model_profile("granite-embedding").is_none());
        assert!(profile_for("granite-embedding:30m", 384).is_err());
    }

    #[test]
    fn unknown_model_falls_back_to_default_profile_only_when_dim_matches() {
        let p = profile_for("mystery-model", 768).expect("768");
        assert_eq!((p.query_prefix, p.document_prefix), ("", ""));
        assert_eq!(p.column, "embedding_768");
        assert_eq!(
            (p.as_of_min_cosine, p.consolidation_min_similarity),
            (0.5, 0.92)
        );
        assert_eq!(
            profile_for("mystery-model", 1536).unwrap().column,
            "embedding_1536"
        );
    }

    #[test]
    fn unknown_dimension_is_refused() {
        let err = profile_for("mystery-model", 384).unwrap_err();
        assert!(err.to_string().contains("384"), "{err}");
        // 1024 has no column until a migration adds one.
        let err = profile_for("mystery-model", 1024).unwrap_err();
        assert!(err.to_string().contains("1024"), "{err}");
    }

    #[test]
    fn embed_text_adds_the_prefix() {
        let b = backend_for("embeddinggemma:300m-q8");
        let q = embed_body(&b, "wo liegt es", TextKind::Query);
        assert_eq!(q["input"], "task: search result | query: wo liegt es");
        assert_eq!(q["model"], "embeddinggemma:300m-q8");
        let d = embed_body(&b, "ein Text", TextKind::Document);
        assert_eq!(d["input"], "title: none | text: ein Text");
        let g = backend_for(DEFAULT_EMBED_MODEL);
        assert_eq!(embed_body(&g, "x", TextKind::Query)["input"], "x");
    }

    #[test]
    fn long_document_still_embeds() {
        let base = std::env::var("OLLAMA_HOST").unwrap_or_else(|_| "http://127.0.0.1:11434".into());
        let reachable = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(2))
            .build()
            .unwrap()
            .get(format!("{base}/api/tags"))
            .send()
            .is_ok();
        if !reachable {
            eprintln!("skipped: ollama unreachable at {base}");
            return;
        }
        let mut b = backend_for(DEFAULT_EMBED_MODEL);
        b.base_url = base;
        let text = "Ein langer Absatz über Speicherorte und Entscheidungen. ".repeat(80);
        assert!(text.chars().count() >= 4000);
        match embed_text(
            &b,
            &text[..text.char_indices().nth(4000).unwrap().0],
            TextKind::Document,
        ) {
            Ok(v) => assert_eq!(v.len(), 768),
            Err(e) => eprintln!("skipped: model not usable here: {e}"),
        }
    }

    #[test]
    fn embed_text_refuses_before_sending() {
        let backend = EmbeddingBackend {
            name: "ollama".into(),
            model: "nomic-embed-text".into(),
            base_url: "http://ollama.com:1".into(),
            column: "embedding_768",
            dim: 768,
            profile: model_profile("nomic-embed-text").unwrap(),
        };
        let err = embed_text(&backend, "secret", TextKind::Document).unwrap_err();
        assert!(err
            .downcast_ref::<crate::llm::RemoteLlmNotAllowed>()
            .is_some());
    }

    #[test]
    fn isolated_blocking_returns_value() {
        let v = isolated_blocking(|| Ok(42)).expect("ok");
        assert_eq!(v, 42);
    }

    #[test]
    fn isolated_blocking_propagates_error() {
        let err = isolated_blocking(|| Err::<(), _>(anyhow!("boom"))).unwrap_err();
        assert!(err.to_string().contains("boom"));
    }

    #[test]
    fn isolated_blocking_survives_nested_tokio_runtime() {
        // Regression: reqwest::blocking used to panic with
        // "Cannot start a runtime from within a runtime" when called from
        // inside the server's Tokio context. isolated_blocking must not.
        let rt = tokio::runtime::Runtime::new().expect("runtime");
        let v = rt.block_on(async {
            tokio::task::spawn_blocking(|| isolated_blocking(|| Ok(7)))
                .await
                .expect("join")
                .expect("inner")
        });
        assert_eq!(v, 7);
    }
}
