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

#[derive(Debug, Clone)]
pub struct EmbeddingBackend {
    pub name: String,
    pub model: String,
    pub base_url: String,
    /// Which embeddings column to use (768 for nomic-class local models).
    pub column: &'static str,
    pub dim: usize,
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
    let ollama_model = std::env::var("OLLAMA_EMBED_MODEL")
        .or_else(|_| std::env::var("THROUGHLINE_EMBED_MODEL"))
        .unwrap_or_else(|_| "nomic-embed-text".into());

    match preference.as_str() {
        "openai" | "openai-compatible" | "hosted" => Err(anyhow!(
            "hosted OpenAI embeddings are disabled; use Ollama (nomic-embed-text) locally"
        )),
        _ => ollama(&ollama_url, &ollama_model),
    }
}

fn ollama(base: &str, model: &str) -> Result<EmbeddingBackend> {
    // Fail early: no request, not even the model list, goes to a remote server.
    crate::llm::ensure_ollama_local_or_allowed(base, model)?;
    let base = base.to_string();
    let model = model.to_string();
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
        Ok(EmbeddingBackend {
            name: "ollama".into(),
            model: model.to_string(),
            base_url: base.to_string(),
            column: "embedding_768",
            dim: 768,
        })
    })
}

/// Ollama `/api/embed` returns `{"embeddings":[[...]]}`; legacy
/// `/api/embeddings` returned `{"embedding":[...]}`. Accept both.
#[derive(Deserialize)]
struct OllamaEmbedResponse {
    embeddings: Option<Vec<Vec<f32>>>,
    embedding: Option<Vec<f32>>,
}

pub fn embed_text(backend: &EmbeddingBackend, text: &str) -> Result<Vec<f32>> {
    // Every caller sends text through here, so the lock is checked before the POST.
    crate::llm::ensure_ollama_local_or_allowed(&backend.base_url, &backend.model)?;
    let backend = backend.clone();
    let text = text.to_string();
    isolated_blocking(move || {
        let client = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(60))
            .build()?;
        let body = serde_json::json!({ "model": backend.model, "input": text });
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

    #[test]
    fn embed_text_refuses_before_sending() {
        let backend = EmbeddingBackend {
            name: "ollama".into(),
            model: "nomic-embed-text".into(),
            base_url: "http://ollama.com:1".into(),
            column: "embedding_768",
            dim: 768,
        };
        let err = embed_text(&backend, "secret").unwrap_err();
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
