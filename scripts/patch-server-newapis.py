from pathlib import Path

p = Path("rust/crates/throughline/src/server.rs")
s = p.read_text(encoding="utf-8")

handlers = '''
async fn stale_digest_handler(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| crate::stale_digest::build_digest(client, 14)).await {
        Ok(d) => Json(d).into_response(),
        Err(resp) => resp,
    }
}

async fn answer_eval_handler(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| {
        let backend = crate::llm::resolve_backend()?;
        let rows = client.query(
            "SELECT content FROM memory_chunks WHERE status='active' AND category='decision' ORDER BY created_at DESC LIMIT 10",
            &[],
        )?;
        let cases: Vec<crate::answer_eval::EvalCase> = rows
            .into_iter()
            .enumerate()
            .map(|(i, r)| {
                let c: String = r.get(0);
                crate::answer_eval::EvalCase {
                    id: format!("mem-{i}"),
                    question: format!("What did we decide about: {}", c.chars().take(60).collect::<String>()),
                    must_cite_conversation_ids: vec![],
                    expected_phrases: vec![],
                    forbidden_phrases: vec!["probably".into()],
                }
            })
            .collect();
        crate::answer_eval::run_eval(client, &backend, &cases)
    })
    .await
    {
        Ok(r) => Json(r).into_response(),
        Err(resp) => resp,
    }
}

async fn writeback_handler(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| {
        let root = std::env::current_dir().unwrap_or_default();
        crate::memory_writeback::writeback(client, &root, None, 15)
    })
    .await
    {
        Ok(s) => Json(s).into_response(),
        Err(resp) => resp,
    }
}

async fn health_recovery_handler(State(state): State<AppState>) -> Response {
    match with_db(&state, |client| crate::doctor::run(client)).await {
        Ok(r) => {
            let mut actions = Vec::new();
            if r.database != "ok" {
                actions.push(serde_json::json!({"id":"db","label":"Check Postgres","hint":"docker compose up -d postgres"}));
            }
            if r.embeddings.contains("unavailable") {
                actions.push(serde_json::json!({"id":"ollama","label":"Start Ollama embeddings","hint":"ollama serve && ollama pull nomic-embed-text"}));
            }
            if r.generation.contains("unavailable") {
                actions.push(serde_json::json!({"id":"llm","label":"Configure generation","hint":"set THROUGHLINE_ANSWER_BASE_URL or run Ollama"}));
            }
            Json(serde_json::json!({
                "database": r.database,
                "generation": r.generation,
                "embeddings": r.embeddings,
                "generation_local": r.generation_local,
                "notes": r.notes,
                "actions": actions,
                "healthy": r.database == "ok",
            }))
            .into_response()
        }
        Err(resp) => resp,
    }
}
'''

if "stale_digest_handler" not in s:
    s = s.replace("async fn audit_handler", handlers + "\nasync fn audit_handler")

if '/api/stale-digest' not in s:
    s = s.replace(
        '.route("/api/audit", get(audit_handler))',
        '.route("/api/audit", get(audit_handler))\n        .route("/api/stale-digest", get(stale_digest_handler))\n        .route("/api/answer-eval", get(answer_eval_handler))\n        .route("/api/writeback", get(writeback_handler))\n        .route("/api/health-recovery", get(health_recovery_handler))',
    )

p.write_text(s, encoding="utf-8")
print("handlers", "stale_digest_handler" in s)
print("routes", "/api/stale-digest" in s)
