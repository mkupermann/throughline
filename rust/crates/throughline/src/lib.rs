//! Throughline library — shared by the CLI binary and integration tests.

pub mod adapters;
pub mod answer_eval;
pub mod ask;
pub mod audit;
pub mod conflicts;
pub mod config;
pub mod costs;
pub mod decisions;
pub mod doctor;
pub mod embedding;
pub mod export_markdown;
pub mod extract_memory;
pub mod generate_embeddings;
pub mod generate_titles;
pub mod handoff;
pub mod ingest_run;
pub mod llm;
pub mod agent_memory;
pub mod mcp;
pub mod memory_writeback;
pub mod migrate;
pub mod ops_jobs;
pub mod pii;
pub mod project_instructions;
pub mod reflect_memory;
pub mod search;
pub mod self_referential;
pub mod server;
pub mod stale_digest;
pub mod types;
pub mod writer;

pub use migrate as migrate_module;
pub use search as search_module;
