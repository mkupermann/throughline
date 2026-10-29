//! Doctor: report whether the pipeline can run here.

use crate::embedding;
use crate::llm;
use anyhow::Result;
use postgres::GenericClient;

#[derive(Debug, serde::Serialize)]
pub struct DoctorReport {
    pub database: String,
    pub generation: String,
    pub embeddings: String,
    pub generation_local: bool,
    pub notes: Vec<String>,
}

pub fn run<C: GenericClient>(client: &mut C) -> Result<DoctorReport> {
    let mut notes = Vec::new();

    let database = match client.batch_execute("SELECT 1") {
        Ok(()) => "ok".to_string(),
        Err(e) => {
            notes.push(format!("database: {e}"));
            "failed".to_string()
        }
    };

    let (generation, generation_local) = match llm::resolve_backend() {
        Ok(b) => {
            let local = llm::is_local(&b);
            (
                format!(
                    "{}{}",
                    b.label,
                    if local { " (local)" } else { " (remote)" }
                ),
                local,
            )
        }
        Err(e) => {
            notes.push(format!(
                "generation unavailable ({e}). extract/ask/titles need a model."
            ));
            ("unavailable".to_string(), false)
        }
    };

    let embeddings = match embedding::resolve() {
        Ok(b) => embedding::label(&b),
        Err(e) => {
            notes.push(format!(
                "embeddings unavailable ({e}). semantic search falls back to lexical."
            ));
            "unavailable".to_string()
        }
    };

    Ok(DoctorReport {
        database,
        generation,
        embeddings,
        generation_local,
        notes,
    })
}
