//! Text generation backends: local Ollama first, then any explicitly named
//! OpenAI-*compatible* endpoint (vLLM, LM Studio, LiteLLM, any OpenAI-compatible server…).
//!
//! Hosted OpenAI is never used and is not a fallback. Set
//! `THROUGHLINE_ANSWER_BASE_URL` + `THROUGHLINE_ANSWER_MODEL` (+ optional
//! `THROUGHLINE_API_KEY`) to route generation to a non-OpenAI provider.

use anyhow::{anyhow, Context, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BackendKind {
    Ollama,
    /// OpenAI Chat Completions wire protocol against a non-OpenAI endpoint.
    OpenAiCompatible,
}

#[derive(Debug, Clone)]
pub struct GenerationBackend {
    pub kind: BackendKind,
    pub label: String,
    pub base_url: String,
    pub model: String,
    pub api_key: Option<String>,
}

fn env_truthy(name: &str) -> bool {
    matches!(
        std::env::var(name)
            .unwrap_or_default()
            .to_lowercase()
            .as_str(),
        "1" | "true" | "yes" | "on"
    )
}

fn env_falsey_or_unset(name: &str) -> bool {
    !env_truthy(name)
}

fn answer_api_key() -> Option<String> {
    std::env::var("THROUGHLINE_API_KEY")
        .or_else(|_| std::env::var("THROUGHLINE_ANSWER_API_KEY"))
        .ok()
        .filter(|k| !k.trim().is_empty())
}

/// Resolve the generation backend: local Ollama, then a named compatible endpoint.
pub fn resolve_backend() -> Result<GenerationBackend> {
    let preference = std::env::var("THROUGHLINE_ANSWER_BACKEND")
        .unwrap_or_else(|_| "auto".into())
        .to_lowercase();
    let model_override = std::env::var("THROUGHLINE_ANSWER_MODEL").ok();
    let base_url_override = std::env::var("THROUGHLINE_ANSWER_BASE_URL")
        .ok()
        .map(|u| u.trim_end_matches('/').to_string());
    let ollama_url = std::env::var("OLLAMA_HOST")
        .unwrap_or_else(|_| "http://localhost:11434".into())
        .trim_end_matches('/')
        .to_string();

    match preference.as_str() {
        "ollama" => Ok(ollama_backend(
            &ollama_url,
            model_override.unwrap_or_else(default_ollama_model),
        )?),
        "openai" | "hosted" => Err(anyhow!(
            "hosted OpenAI generation is disabled; set THROUGHLINE_ANSWER_BASE_URL to a local or Xiaomi endpoint"
        )),
        "compatible" | "openai-compatible" => {
            let base = base_url_override.ok_or_else(|| {
                anyhow!("THROUGHLINE_ANSWER_BASE_URL is required for the compatible backend")
            })?;
            let model = model_override.unwrap_or_else(|| "default".into());
            Ok(compat_backend(&base, model, answer_api_key()))
        }
        _ => {
            // auto: local Ollama, then any explicitly named compatible endpoint.
            if let Ok(model) = probe_ollama(&ollama_url, model_override.clone()) {
                return Ok(model);
            }
            if let Some(base) = base_url_override {
                let model = model_override.unwrap_or_else(|| "default".into());
                return Ok(compat_backend(&base, model, answer_api_key()));
            }
            Err(anyhow!(
                "no generation backend: start Ollama, or set THROUGHLINE_ANSWER_BASE_URL + THROUGHLINE_ANSWER_MODEL (e.g. your model name)"
            ))
        }
    }
}

fn default_ollama_model() -> String {
    std::env::var("THROUGHLINE_ANSWER_MODEL").unwrap_or_else(|_| "qwen3.5:9b".into())
}

fn ollama_backend(base: &str, model: String) -> Result<GenerationBackend> {
    Ok(GenerationBackend {
        kind: BackendKind::Ollama,
        label: format!("ollama/{model}"),
        base_url: base.to_string(),
        model,
        api_key: None,
    })
}

fn compat_backend(base: &str, model: String, api_key: Option<String>) -> GenerationBackend {
    let host = base.to_lowercase();
    let vendor = if host.contains("127.0.0.1") || host.contains("localhost") {
        "xiaomi"
    } else {
        "compatible"
    };
    GenerationBackend {
        kind: BackendKind::OpenAiCompatible,
        label: format!("{vendor}/{model}"),
        base_url: base.trim_end_matches('/').to_string(),
        model,
        api_key,
    }
}

fn probe_ollama(base: &str, model_override: Option<String>) -> Result<GenerationBackend> {
    let base = base.to_string();
    crate::embedding::isolated_blocking(move || {
        let client = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(3))
            .build()?;
        let tags: serde_json::Value = client
            .get(format!("{base}/api/tags"))
            .send()
            .context("ollama /api/tags")?
            .json()
            .context("ollama tags json")?;
        let names: Vec<String> = tags["models"]
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(|m| m["name"].as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();
        if names.is_empty() {
            return Err(anyhow!("ollama has no models"));
        }
        let model = model_override.unwrap_or_else(|| {
            names
                .iter()
                .find(|n| n.starts_with("qwen") || n.contains("llama") || n.contains("mistral"))
                .cloned()
                .unwrap_or_else(|| names[0].clone())
        });
        ollama_backend(&base, model)
    })
}

#[derive(Serialize)]
struct ChatMessage {
    role: String,
    content: String,
}

#[derive(Serialize)]
struct OllamaChat {
    model: String,
    messages: Vec<ChatMessage>,
    stream: bool,
    /// Unset leaves the model's own default: thinking models then reason at
    /// length before the answer and can outlast the request timeout.
    #[serde(skip_serializing_if = "Option::is_none")]
    think: Option<bool>,
    /// Unset lets Ollama allocate the model's full context window.
    #[serde(skip_serializing_if = "Option::is_none")]
    options: Option<OllamaOptions>,
}

#[derive(Serialize)]
struct OllamaOptions {
    num_ctx: u32,
}

#[derive(Deserialize)]
struct OllamaChatResponse {
    message: Option<OllamaMessage>,
}

#[derive(Deserialize)]
struct OllamaMessage {
    content: String,
}

#[derive(Serialize)]
struct CompatChat {
    model: String,
    messages: Vec<ChatMessage>,
}

#[derive(Deserialize)]
struct CompatChatResponse {
    choices: Vec<CompatChoice>,
}

#[derive(Deserialize)]
struct CompatChoice {
    message: OllamaMessage,
}

/// One chat completion. `system` is optional.
pub fn complete(backend: &GenerationBackend, system: Option<&str>, user: &str) -> Result<String> {
    let mut messages = Vec::new();
    if let Some(sys) = system {
        messages.push(ChatMessage {
            role: "system".into(),
            content: sys.into(),
        });
    }
    messages.push(ChatMessage {
        role: "user".into(),
        content: user.into(),
    });

    let kind = backend.kind;
    let model = backend.model.clone();
    let base_url = backend.base_url.clone();
    let api_key = backend.api_key.clone();
    let timeout_secs = std::env::var("THROUGHLINE_LLM_TIMEOUT_SECS")
        .ok()
        .and_then(|v| v.trim().parse().ok())
        .filter(|&s: &u64| s > 0)
        .unwrap_or(180);
    let think = parse_think(std::env::var("THROUGHLINE_OLLAMA_THINK").ok().as_deref());
    let num_ctx = parse_num_ctx(std::env::var("THROUGHLINE_OLLAMA_NUM_CTX").ok().as_deref());
    crate::embedding::isolated_blocking(move || {
        let client = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(timeout_secs))
            .build()?;

        match kind {
            BackendKind::Ollama => {
                let body = ollama_chat(model, messages, think, num_ctx);
                let resp: OllamaChatResponse = client
                    .post(format!("{base_url}/api/chat"))
                    .json(&body)
                    .send()
                    .context("ollama chat request")?
                    .error_for_status()
                    .context("ollama chat status")?
                    .json()
                    .context("ollama chat json")?;
                resp.message
                    .map(|m| m.content)
                    .ok_or_else(|| anyhow!("ollama returned no message"))
            }
            BackendKind::OpenAiCompatible => {
                let body = CompatChat { model, messages };
                let mut req = client.post(format!("{base_url}/chat/completions"));
                if let Some(key) = &api_key {
                    req = req.bearer_auth(key);
                }
                let resp: CompatChatResponse = req
                    .json(&body)
                    .send()
                    .context("compatible chat request")?
                    .error_for_status()
                    .context("compatible chat status")?
                    .json()
                    .context("compatible chat json")?;
                resp.choices
                    .into_iter()
                    .next()
                    .map(|c| c.message.content)
                    .ok_or_else(|| anyhow!("compatible endpoint returned no choices"))
            }
        }
    })
}

/// Whether this backend keeps data on the machine.
pub fn is_local(backend: &GenerationBackend) -> bool {
    let host = backend.base_url.to_lowercase();
    backend.kind == BackendKind::Ollama
        || host.contains("localhost")
        || host.contains("127.0.0.1")
        || host.contains("::1")
}

/// `THROUGHLINE_REDACT_PII` defaults to on.
pub fn redact_prompts_enabled() -> bool {
    env_falsey_or_unset("THROUGHLINE_REDACT_PII") || env_truthy("THROUGHLINE_REDACT_PII")
}

/// `THROUGHLINE_OLLAMA_THINK`: on/off words; anything else leaves it unset.
fn parse_think(value: Option<&str>) -> Option<bool> {
    match value?.trim().to_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => Some(true),
        "0" | "false" | "no" | "off" => Some(false),
        _ => None,
    }
}

/// `THROUGHLINE_OLLAMA_NUM_CTX`: a positive token count; anything else leaves it unset.
fn parse_num_ctx(value: Option<&str>) -> Option<u32> {
    value?.trim().parse().ok().filter(|&n| n > 0)
}

fn ollama_chat(
    model: String,
    messages: Vec<ChatMessage>,
    think: Option<bool>,
    num_ctx: Option<u32>,
) -> OllamaChat {
    OllamaChat {
        model,
        messages,
        stream: false,
        think,
        options: num_ctx.map(|num_ctx| OllamaOptions { num_ctx }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ollama_body_leaves_think_and_options_out_when_unset() {
        let body = serde_json::to_value(ollama_chat("m".into(), vec![], None, None)).unwrap();
        assert!(body.get("think").is_none(), "{body}");
        assert!(body.get("options").is_none(), "{body}");
    }

    #[test]
    fn ollama_body_carries_think_and_num_ctx_when_set() {
        let body = serde_json::to_value(ollama_chat("m".into(), vec![], Some(false), Some(16384)))
            .unwrap();
        assert_eq!(body["think"], serde_json::json!(false));
        assert_eq!(body["options"]["num_ctx"], serde_json::json!(16384));
    }

    #[test]
    fn think_and_num_ctx_parse_env_values() {
        assert_eq!(parse_think(Some("false")), Some(false));
        assert_eq!(parse_think(Some("Off")), Some(false));
        assert_eq!(parse_think(Some("1")), Some(true));
        assert_eq!(parse_think(Some("")), None);
        assert_eq!(parse_think(Some("maybe")), None);
        assert_eq!(parse_think(None), None);
        assert_eq!(parse_num_ctx(Some("16384")), Some(16384));
        assert_eq!(parse_num_ctx(Some("0")), None);
        assert_eq!(parse_num_ctx(Some("lots")), None);
        assert_eq!(parse_num_ctx(None), None);
    }
}
