//! Recognise Throughline's own model-call transcripts so ingest drops them.

/// First user-message markers used by Throughline's own `claude -p` / model calls.
/// Must match what is already on disk from past runs.
pub const MARKERS: &[&str] = &[
    "Du bekommst einen Auszug aus einer Claude Code Session.",
    "You are extracting durable memory",
    "You are titling a Claude Code session",
    "You are reflecting on memory chunks",
    "You are extracting entities",
    "Generate a concise title",
    "Extract durable facts",
];

pub fn is_agent_call_prompt(first_user_text: &str) -> bool {
    let t = first_user_text.trim_start();
    MARKERS.iter().any(|m| t.starts_with(m))
}

pub fn self_referential_reason(first_user_text: &str) -> Option<&'static str> {
    if is_agent_call_prompt(first_user_text) {
        Some("throughline agent call")
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recognises_title_generator_marker() {
        assert!(is_agent_call_prompt(
            "Du bekommst einen Auszug aus einer Claude Code Session. Generiere einen Titel"
        ));
    }

    #[test]
    fn leaves_normal_user_prompts() {
        assert!(!is_agent_call_prompt("fix the ingestion queue bug"));
    }
}
