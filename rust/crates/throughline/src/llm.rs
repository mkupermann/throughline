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

/// Ollama names its cloud models with a ":cloud" or "-cloud" suffix.
fn has_cloud_suffix(name: &str) -> bool {
    let n = name.trim().to_lowercase();
    n.ends_with(":cloud") || n.ends_with("-cloud")
}

/// A model served by Ollama's cloud: it runs off this machine.
fn is_cloud_model(name: &str, entry: &serde_json::Value) -> bool {
    has_cloud_suffix(name)
        || entry["remote_host"]
            .as_str()
            .is_some_and(|h| !h.trim().is_empty())
}

/// Choose the chat model from an Ollama `/api/tags` answer. Automatic choice
/// never lands on a cloud model (note text would leave the machine) nor on an
/// embedding model; an explicit `model_override` is taken as given.
fn pick_ollama_model(tags: &serde_json::Value, model_override: Option<String>) -> Result<String> {
    if let Some(m) = model_override {
        return Ok(m);
    }
    let names: Vec<String> = tags["models"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|m| {
                    let name = m["name"].as_str()?;
                    (!is_cloud_model(name, m) && !name.contains("embed")).then(|| name.to_string())
                })
                .collect()
        })
        .unwrap_or_default();
    names
        .iter()
        .find(|n| n.starts_with("qwen") || n.contains("llama") || n.contains("mistral"))
        .or_else(|| names.first())
        .cloned()
        .ok_or_else(|| anyhow!("ollama has no local chat model"))
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
        let model = pick_ollama_model(&tags, model_override)?;
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

/// The host of `scheme://[userinfo@]host[:port]/...`, lowercased, without
/// IPv6 brackets. `None` when there is no scheme or no host.
fn url_host(url: &str) -> Option<String> {
    let url = url.trim();
    // A value like `host:11434` has no scheme: its authority starts at the front.
    let rest = url.split_once("://").map_or(url, |(_, rest)| rest);
    let authority = rest.split(['/', '?', '#']).next()?;
    let hostport = authority.rsplit('@').next()?;
    let host = if let Some(inner) = hostport.strip_prefix('[') {
        inner.split(']').next()?
    } else {
        hostport.split(':').next()?
    };
    let host = host.trim_end_matches('.').to_lowercase();
    (!host.is_empty()).then_some(host)
}

fn is_loopback_host(host: &str) -> bool {
    host == "localhost"
        || host == "::1"
        || host == "host.docker.internal"
        || host
            .parse::<std::net::Ipv4Addr>()
            .is_ok_and(|ip| ip.octets()[0] == 127)
}

/// Whether this backend keeps data on the machine. An Ollama server counts as
/// local wherever it runs, unless the model is a cloud model or the server is
/// ollama.com. An OpenAI-compatible endpoint is local only on this machine.
pub fn is_local(backend: &GenerationBackend) -> bool {
    let host = url_host(&backend.base_url);
    if backend.kind == BackendKind::Ollama {
        // An unparsable host fails closed: it is not treated as local.
        let local_host = host
            .as_deref()
            .is_some_and(|h| h != "ollama.com" && !h.ends_with(".ollama.com"));
        return !has_cloud_suffix(&backend.model) && local_host;
    }
    host.as_deref().is_some_and(is_loopback_host)
}

/// `THROUGHLINE_ALLOW_REMOTE_LLM`: only `1`, `true`, `yes`, `on` allow it.
fn remote_allowed(value: Option<&str>) -> bool {
    matches!(
        value.map(|v| v.trim().to_lowercase()).as_deref(),
        Some("1" | "true" | "yes" | "on")
    )
}

/// A text-sending path was refused because the backend is not local and
/// `THROUGHLINE_ALLOW_REMOTE_LLM` is not set. Handlers recognise it by type.
#[derive(Debug)]
pub struct RemoteLlmNotAllowed {
    pub label: String,
}

impl std::fmt::Display for RemoteLlmNotAllowed {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "refusing to send text to {}: the model is not local. Set THROUGHLINE_ALLOW_REMOTE_LLM=1 to allow it, or use a local model",
            self.label
        )
    }
}

impl std::error::Error for RemoteLlmNotAllowed {}

fn check_local_or_allowed(backend: &GenerationBackend, allowed: bool) -> Result<()> {
    if allowed || is_local(backend) {
        return Ok(());
    }
    Err(anyhow::Error::new(RemoteLlmNotAllowed {
        label: backend.label.clone(),
    }))
}

/// Refuse a non-local backend unless `THROUGHLINE_ALLOW_REMOTE_LLM` is truthy.
/// Call this before any transcript or note text is sent to `backend`.
pub fn ensure_local_or_allowed(backend: &GenerationBackend) -> Result<()> {
    let flag = std::env::var("THROUGHLINE_ALLOW_REMOTE_LLM").ok();
    check_local_or_allowed(backend, remote_allowed(flag.as_deref()))
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

    fn backend(kind: BackendKind, base: &str, model: &str) -> GenerationBackend {
        GenerationBackend {
            kind,
            label: format!("t/{model}"),
            base_url: base.into(),
            model: model.into(),
            api_key: None,
        }
    }

    #[test]
    fn is_local_compares_the_host_exactly() {
        let compat = |u: &str| backend(BackendKind::OpenAiCompatible, u, "m");
        for u in [
            "http://127.0.0.1:8000/v1",
            "http://localhost:1234",
            "http://[::1]:8080",
            "http://host.docker.internal:11434",
            "http://user:pw@127.0.0.5/v1",
        ] {
            assert!(is_local(&compat(u)), "{u} should be local");
        }
        for u in [
            "https://localhost.evil.example/v1",
            "https://x.example/?h=localhost",
            "https://127.0.0.1.evil.example/v1",
            "http://localhost@evil.example/v1",
            "https://api.example.com/v1",
        ] {
            assert!(!is_local(&compat(u)), "{u} should not be local");
        }
        let ollama = |u: &str, m: &str| backend(BackendKind::Ollama, u, m);
        assert!(is_local(&ollama(
            "http://gpu-box.corp:11434",
            "qwen3.6:27b"
        )));
        assert!(!is_local(&ollama("https://ollama.com", "qwen3.6:27b")));
        assert!(!is_local(&ollama(
            "https://api.ollama.com:443/x",
            "qwen3.6:27b"
        )));
        assert!(is_local(&ollama(
            "http://notollama.com.corp:11434",
            "qwen3.6:27b"
        )));
        assert!(!is_local(&ollama(
            "http://localhost:11434",
            "gpt-oss:120b-cloud"
        )));
    }

    #[test]
    fn a_base_url_without_a_scheme_is_checked_too() {
        let ollama = |u: &str| backend(BackendKind::Ollama, u, "qwen3.6:27b");
        let compat = |u: &str| backend(BackendKind::OpenAiCompatible, u, "m");
        assert!(!is_local(&ollama("ollama.com:11434")));
        assert!(!is_local(&ollama("api.ollama.com")));
        assert!(is_local(&ollama("gpu-box.corp:11434")));
        assert!(is_local(&compat("localhost:8000/v1")));
        assert!(!is_local(&compat("evil.example:8000")));
        // No host at all: fail closed.
        assert!(!is_local(&ollama("")));
        assert!(!is_local(&ollama("http://")));
    }

    #[test]
    fn refusal_is_a_typed_error() {
        let b = backend(
            BackendKind::OpenAiCompatible,
            "https://api.example.com/v1",
            "m",
        );
        let err = check_local_or_allowed(&b, false).unwrap_err();
        assert!(err.downcast_ref::<RemoteLlmNotAllowed>().is_some());
    }

    #[test]
    fn remote_allowed_parses_the_flag() {
        for v in ["1", "true", "TRUE", "yes", "On", " on "] {
            assert!(remote_allowed(Some(v)), "{v:?} should allow");
        }
        for v in ["", "0", "false", "no", "off", "maybe"] {
            assert!(!remote_allowed(Some(v)), "{v:?} should not allow");
        }
        assert!(!remote_allowed(None));
    }

    #[test]
    fn non_local_backend_is_refused_unless_allowed() {
        let cloud = backend(
            BackendKind::Ollama,
            "http://localhost:11434",
            "glm-5.2:cloud",
        );
        let remote = backend(
            BackendKind::OpenAiCompatible,
            "https://api.example.com/v1",
            "m",
        );
        let local = backend(BackendKind::Ollama, "http://localhost:11434", "qwen3.6:27b");
        let loopback = backend(
            BackendKind::OpenAiCompatible,
            "http://127.0.0.1:8000/v1",
            "m",
        );
        for b in [&cloud, &remote] {
            let err = check_local_or_allowed(b, false).unwrap_err().to_string();
            assert!(err.contains("THROUGHLINE_ALLOW_REMOTE_LLM"), "{err}");
            assert!(check_local_or_allowed(b, true).is_ok());
        }
        for b in [&local, &loopback] {
            assert!(check_local_or_allowed(b, false).is_ok());
            assert!(check_local_or_allowed(b, true).is_ok());
        }
    }

    fn mac_tags() -> serde_json::Value {
        serde_json::json!({"models": [
            {"name": "glm-5.2:cloud", "remote_host": "https://ollama.com"},
            {"name": "nomic-embed-text:latest"},
            {"name": "qwen3.6:27b"}
        ]})
    }

    #[test]
    fn auto_selection_skips_cloud_models() {
        assert_eq!(pick_ollama_model(&mac_tags(), None).unwrap(), "qwen3.6:27b");
        // A cloud model that sorts first and matches no preferred name must not win.
        let tags = serde_json::json!({"models": [
            {"name": "glm-5.2:cloud", "remote_host": "https://ollama.com"},
            {"name": "gemma3:4b"}
        ]});
        assert_eq!(pick_ollama_model(&tags, None).unwrap(), "gemma3:4b");
        // Only a cloud model and an embedding model: nothing local to chat with.
        let tags = serde_json::json!({"models": [
            {"name": "glm-5.2:cloud", "remote_host": "https://ollama.com"},
            {"name": "nomic-embed-text:latest"}
        ]});
        assert!(pick_ollama_model(&tags, None).is_err());
        // A remote_host alone marks a model as remote, whatever its name says.
        let tags = serde_json::json!({"models": [
            {"name": "qwen-big", "remote_host": "https://ollama.com"}
        ]});
        assert!(pick_ollama_model(&tags, None).is_err());
    }

    #[test]
    fn cloud_ollama_models_are_not_local() {
        let local = ollama_backend("http://localhost:11434", "qwen3.6:27b".into()).unwrap();
        assert!(is_local(&local));
        let cloud = ollama_backend("http://localhost:11434", "glm-5.2:cloud".into()).unwrap();
        assert!(!is_local(&cloud));
    }

    #[test]
    fn dash_cloud_models_and_ollama_com_are_not_local() {
        let dash = ollama_backend("http://localhost:11434", "gpt-oss:120b-cloud".into()).unwrap();
        assert!(!is_local(&dash));
        let upper = ollama_backend("http://localhost:11434", "GPT-OSS:120B-CLOUD".into()).unwrap();
        assert!(!is_local(&upper));
        let local = ollama_backend("http://localhost:11434", "qwen3.6:27b".into()).unwrap();
        assert!(is_local(&local));
        let hosted = ollama_backend("https://ollama.com", "qwen3.6:27b".into()).unwrap();
        assert!(!is_local(&hosted));
        let tags = serde_json::json!({"models": [
            {"name": "gpt-oss:120b-cloud"},
            {"name": "qwen3.6:27b"}
        ]});
        assert_eq!(pick_ollama_model(&tags, None).unwrap(), "qwen3.6:27b");
        let only = serde_json::json!({"models": [{"name": "gpt-oss:120b-cloud"}]});
        assert!(pick_ollama_model(&only, None).is_err());
    }

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
