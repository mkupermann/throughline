//! Built-in adapter registry.

use super::claude_code::ClaudeCodeAdapter;
use super::Adapter;

/// All built-in adapters. Order is the `ingest --all` order.
pub fn built_ins() -> Vec<Box<dyn Adapter>> {
    let mut list: Vec<Box<dyn Adapter>> = vec![
        Box::new(ClaudeCodeAdapter::default()),
        Box::new(super::codex::CodexAdapter::default()),
        Box::new(super::cursor::CursorAdapter::default()),
        Box::new(super::zed::ZedAdapter::default()),
        Box::new(super::continue_dev::ContinueAdapter::default()),
        Box::new(super::cline::ClineAdapter::default()),
        Box::new(super::windsurf::WindsurfAdapter::default()),
        Box::new(super::vibe::VibeAdapter::default()),
        Box::new(super::hermes::HermesAdapter::default()),
        Box::new(super::opencode::OpenCodeAdapter::default()),
        Box::new(super::aider::AiderAdapter::default()),
    ];
    list.extend(registry_hook());
    list
}

/// Extension point as adapters land.
fn registry_hook() -> Vec<Box<dyn Adapter>> {
    Vec::new()
}

pub fn by_name(name: &str) -> Option<Box<dyn Adapter>> {
    let normalized = name.replace('-', "_");
    built_ins().into_iter().find(|a| a.name() == normalized)
}
