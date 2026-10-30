//! Minimal MCP stdio server (JSON-RPC 2.0) exposing memory tools.

use crate::search;
use crate::server::{
    supersede_memory, validate_supersede, Successor, SupersedeError, SupersedeKind,
};
use anyhow::Result;
use postgres::GenericClient;
use serde_json::{json, Value};
use std::io::{BufRead, Write};

pub fn run_stdio<C: GenericClient>(client: &mut C) -> Result<()> {
    let stdin = std::io::stdin();
    let mut stdout = std::io::stdout();
    for line in stdin.lock().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let Ok(msg) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        let id = msg.get("id").cloned();
        let method = msg.get("method").and_then(|m| m.as_str()).unwrap_or("");
        let params = msg.get("params").cloned().unwrap_or(Value::Null);

        let result = match method {
            "initialize" => Ok(json!({
                "protocolVersion": "2024-11-05",
                "capabilities": { "tools": {} },
                "serverInfo": { "name": "throughline", "version": env!("CARGO_PKG_VERSION") }
            })),
            "notifications/initialized" => {
                // no response
                continue;
            }
            "tools/list" => Ok(tools_list()),
            "tools/call" => call_tool(client, &params),
            "ping" => Ok(json!({})),
            other => Err(json!({
                "code": -32601,
                "message": format!("method not found: {other}")
            })),
        };

        let response = match result {
            Ok(value) => json!({ "jsonrpc": "2.0", "id": id, "result": value }),
            Err(err) => json!({ "jsonrpc": "2.0", "id": id, "error": err }),
        };
        writeln!(stdout, "{response}")?;
        stdout.flush()?;
    }
    Ok(())
}

fn tools_list() -> Value {
    json!({
        "tools": [
            {
                "name": "search",
                "description": "Search messages in the local memory corpus",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": { "type": "string" },
                        "limit": { "type": "integer", "default": 10 },
                        "project": { "type": "string" }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "recall",
                "description": "Recall memory notes, with the reason for each hit. Modes: current (valid now), as_of (what was valid or known on a date), associative (plus related notes), grounded (cites the source message). Rank: fulltext (default, word match) or weighted (meaning, recency, reliability; needs embeddings)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": { "type": "string" },
                        "project": { "type": "string" },
                        "mode": {
                            "type": "string",
                            "enum": ["current", "as_of", "associative", "grounded"],
                            "default": "current"
                        },
                        "valid_at": { "type": "string", "description": "YYYY-MM-DD; for as_of" },
                        "known_at": { "type": "string", "description": "YYYY-MM-DD; for as_of" },
                        "limit": { "type": "integer", "default": 10 },
                        "rank": {
                            "type": "string",
                            "enum": ["fulltext", "weighted"],
                            "default": "fulltext"
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "project_context",
                "description": "Pack the current decisions, notes, recent sessions and open questions of a project into a token budget, with citations. Give a task to pull in the notes that match it",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project": { "type": "string" },
                        "task": { "type": "string" },
                        "budget": { "type": "integer", "default": 2000, "description": "estimated tokens, 64 to 32768" }
                    },
                    "required": ["project"]
                }
            },
            {
                "name": "list_projects",
                "description": "List known project names",
                "inputSchema": { "type": "object", "properties": {} }
            },
            {
                "name": "stats",
                "description": "Corpus counts",
                "inputSchema": { "type": "object", "properties": {} }
            },
            {
                "name": "write",
                "description": "Write a manual memory chunk",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "content": { "type": "string" },
                        "category": { "type": "string" },
                        "project": { "type": "string" }
                    },
                    "required": ["content"]
                }
            },
            {
                "name": "forget",
                "description": "Mark memory chunks as forgotten",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "ids": { "type": "array", "items": { "type": "integer" } },
                        "reason": { "type": "string" }
                    },
                    "required": ["ids"]
                }
            },
            {
                "name": "supersede",
                "description": "Replace a memory note with a new active one; the old note stays as history",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "id": { "type": "integer" },
                        "content": { "type": "string" },
                        "category": { "type": "string" },
                        "valid_from": { "type": "string" },
                        "kind": { "type": "string", "enum": ["update", "correction"] }
                    },
                    "required": ["id", "content"]
                }
            }
        ]
    })
}

/// An optional string argument, trimmed. Absent or null is `None`; any other
/// non-string is -32602 with `message`, never a silent default.
fn optional_str<'a>(args: &'a Value, name: &str, message: &str) -> Result<Option<&'a str>, Value> {
    match args.get(name) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(s)) => Ok(Some(s.trim())),
        Some(_) => Err(json!({ "code": -32602, "message": message })),
    }
}

fn call_tool<C: GenericClient>(client: &mut C, params: &Value) -> Result<Value, Value> {
    let name = params
        .get("name")
        .and_then(|n| n.as_str())
        .unwrap_or_default();
    let args = params
        .get("arguments")
        .cloned()
        .unwrap_or(Value::Object(Default::default()));
    let text_result = |s: String| {
        json!({
            "content": [{ "type": "text", "text": s }],
            "isError": false
        })
    };

    match name {
        "search" => {
            let query = args.get("query").and_then(|q| q.as_str()).unwrap_or("");
            let limit = args.get("limit").and_then(|l| l.as_i64()).unwrap_or(10) as i32;
            let project = optional_str(&args, "project", "project must be a string")?
                .filter(|p| !p.is_empty());
            let hits = search::search(client, query, limit, project)
                .map_err(|e| json!({ "code": -32000, "message": e.to_string() }))?;
            let payload = serde_json::to_string(
                &hits
                    .iter()
                    .map(|h| {
                        json!({
                            "conversation_id": h.conversation_id,
                            "project": h.project_name,
                            "role": h.role,
                            "snippet": h.snippet,
                        })
                    })
                    .collect::<Vec<_>>(),
            )
            .unwrap_or_else(|_| "[]".into());
            Ok(text_result(payload))
        }
        "recall" => {
            let invalid = |m: &str| json!({ "code": -32602, "message": m });
            let query = match args.get("query").and_then(|q| q.as_str()) {
                Some(q) if !q.trim().is_empty() => q,
                _ => return Err(invalid("query required")),
            };
            crate::recall::check_query(query).map_err(|e| invalid(&e))?;
            let project = optional_str(&args, "project", "project must be a string")?
                .filter(|p| !p.is_empty());
            let mode = match optional_str(&args, "mode", "mode must be a string")? {
                None | Some("") => crate::recall::RecallMode::Current,
                Some(m) => m.parse().map_err(|e: String| invalid(&e))?,
            };
            let rank = match optional_str(&args, "rank", "rank must be a string")? {
                None | Some("") => crate::recall::Rank::default(),
                Some(r) => r.parse().map_err(|e: String| invalid(&e))?,
            };
            let date = |name: &str| -> Result<Option<chrono::DateTime<chrono::Utc>>, Value> {
                match optional_str(&args, name, &format!("{name} must be a string"))? {
                    None | Some("") => Ok(None),
                    Some(s) => crate::temporal::parse_as_of_instant(s)
                        .map(Some)
                        .ok_or_else(|| {
                            invalid(&format!(
                                "{name} must be YYYY-MM-DD or an RFC 3339 timestamp, got {s:?}"
                            ))
                        }),
                }
            };
            let (valid_at, known_at) = (date("valid_at")?, date("known_at")?);
            if mode == crate::recall::RecallMode::AsOf && valid_at.is_none() && known_at.is_none() {
                return Err(invalid("as_of needs valid_at or known_at"));
            }
            let limit = args
                .get("limit")
                .and_then(|l| l.as_i64())
                .unwrap_or(crate::recall::DEFAULT_LIMIT as i64)
                .clamp(1, 50) as usize;
            let emb = crate::recall::needs_embedding(mode, rank)
                .then(|| crate::recall::query_embedding(query))
                .flatten();
            let q = crate::recall::RecallQuery {
                text: query,
                project,
                mode,
                valid_at,
                known_at,
                limit,
                rank,
            };
            let result = crate::recall::recall(client, &q, emb.as_ref())
                .map_err(|e| json!({ "code": -32000, "message": e.to_string() }))?;
            Ok(text_result(serde_json::to_string(&result).map_err(
                |e| json!({ "code": -32000, "message": e.to_string() }),
            )?))
        }
        "project_context" => {
            let invalid = |m: &str| json!({ "code": -32602, "message": m });
            let project = match optional_str(&args, "project", "project must be a string")? {
                Some(p) if !p.is_empty() => p,
                _ => return Err(invalid("project required")),
            };
            let task =
                optional_str(&args, "task", "task must be a string")?.filter(|t| !t.is_empty());
            if let Some(t) = task {
                crate::recall::check_query(t).map_err(|e| invalid(&e))?;
            }
            let budget = match args.get("budget") {
                None | Some(Value::Null) => crate::context_pack::DEFAULT_BUDGET,
                Some(v) => v
                    .as_u64()
                    .map(|b| b as usize)
                    .filter(|b| {
                        (crate::context_pack::MIN_BUDGET..=crate::context_pack::MAX_BUDGET)
                            .contains(b)
                    })
                    .ok_or_else(|| {
                        invalid(&format!(
                            "budget must be an integer between {} and {}",
                            crate::context_pack::MIN_BUDGET,
                            crate::context_pack::MAX_BUDGET
                        ))
                    })?,
            };
            let pack = crate::context_pack::build_context(
                client,
                &crate::context_pack::ContextRequest {
                    project,
                    task,
                    budget,
                },
                None,
            )
            .map_err(|e| json!({ "code": -32000, "message": e.to_string() }))?;
            Ok(text_result(serde_json::to_string(&pack).map_err(
                |e| json!({ "code": -32000, "message": e.to_string() }),
            )?))
        }
        "list_projects" => {
            let rows = client
                .query("SELECT name FROM projects ORDER BY name", &[])
                .map_err(|e| json!({ "code": -32000, "message": e.to_string() }))?;
            let names: Vec<String> = rows.into_iter().map(|r| r.get(0)).collect();
            Ok(text_result(
                serde_json::to_string(&names).unwrap_or_default(),
            ))
        }
        "stats" => {
            let c = search::count_conversations(client).unwrap_or(0);
            let m = search::count_messages(client).unwrap_or(0);
            Ok(text_result(format!(
                "{{\"conversations\":{c},\"messages\":{m}}}"
            )))
        }
        "write" => {
            let content = args
                .get("content")
                .and_then(|c| c.as_str())
                .unwrap_or_default()
                .to_string();
            let category = args
                .get("category")
                .and_then(|c| c.as_str())
                .unwrap_or("insight")
                .to_string();
            let project = args
                .get("project")
                .and_then(|c| c.as_str())
                .map(|s| s.to_string());
            if content.trim().is_empty() {
                return Err(json!({ "code": -32602, "message": "content required" }));
            }
            let category_type = crate::extract_memory::category_column_type(client)
                .map_err(|e| json!({ "code": -32000, "message": e.to_string() }))?;
            let id: i64 = client
                .query_one(
                    &format!(
                        r#"
                    INSERT INTO memory_chunks (source_type, content, category, project_name, confidence, status, source_at)
                    VALUES ('mcp_write', $1, $2::text::{category_type}, $3, 0.9, 'active', now())
                    RETURNING id
                    "#
                    ),
                    &[&content, &category, &project],
                )
                .map_err(|e| json!({ "code": -32000, "message": e.to_string() }))?
                .get(0);
            Ok(text_result(format!("{{\"id\":{id}}}")))
        }
        "forget" => {
            let ids: Vec<i64> = args
                .get("ids")
                .and_then(|v| v.as_array())
                .map(|a| a.iter().filter_map(|x| x.as_i64()).collect())
                .unwrap_or_default();
            if ids.is_empty() {
                return Err(json!({ "code": -32602, "message": "ids required" }));
            }
            let n = client
                .execute(
                    "UPDATE memory_chunks SET status = 'forgotten', updated_at = now() WHERE id = ANY($1)",
                    &[&ids],
                )
                .map_err(|e| json!({ "code": -32000, "message": e.to_string() }))?;
            Ok(text_result(format!("{{\"forgotten\":{n}}}")))
        }
        "supersede" => {
            let id = match args.get("id").and_then(|v| v.as_i64()) {
                Some(id) => id,
                None => return Err(json!({ "code": -32602, "message": "id required" })),
            };
            let content = args
                .get("content")
                .and_then(|c| c.as_str())
                .unwrap_or_default()
                .to_string();
            let category = args
                .get("category")
                .and_then(|c| c.as_str())
                .map(|s| s.to_string());
            if let Err(e) = validate_supersede(&content, category.as_deref()) {
                return Err(json!({ "code": -32602, "message": e.to_string() }));
            }
            let kind_error = "kind must be update or correction";
            let kind = match optional_str(&args, "kind", kind_error)? {
                None | Some("") => SupersedeKind::Update,
                Some(k) => match SupersedeKind::parse(k) {
                    Some(k) => k,
                    None => return Err(json!({ "code": -32602, "message": kind_error })),
                },
            };
            let from_error = "valid_from must be an RFC 3339 timestamp or a YYYY-MM-DD date";
            let valid_from_raw = optional_str(&args, "valid_from", from_error)?;
            let valid_from_is_date = valid_from_raw.is_some_and(crate::temporal::is_plain_date);
            let valid_from = match valid_from_raw {
                None | Some("") => None,
                Some(v) => match crate::temporal::parse_instant(v) {
                    Some(t) => Some(t),
                    None => return Err(json!({ "code": -32602, "message": from_error })),
                },
            };
            let successor = Successor {
                content: &content,
                category: category.as_deref(),
                source_type: "mcp_write",
                kind,
                valid_from,
                valid_from_is_date,
                confidence: 1.0,
                tags: Vec::new(),
                source_at: None,
            };
            match supersede_memory(client, id, &successor) {
                Ok((old_id, new_id)) => Ok(text_result(format!(
                    "{{\"old_id\":{old_id},\"new_id\":{new_id}}}"
                ))),
                Err(SupersedeError::NotFound) => {
                    Err(json!({ "code": -32000, "message": "memory chunk not found" }))
                }
                Err(SupersedeError::AlreadySuperseded) => {
                    Err(json!({ "code": -32000, "message": "memory chunk already superseded" }))
                }
                Err(
                    e @ (SupersedeError::ValidFromInFuture
                    | SupersedeError::ValidFromBeforePredecessor),
                ) => Err(json!({ "code": -32602, "message": e.to_string() })),
                Err(e) => Err(json!({ "code": -32000, "message": e.to_string() })),
            }
        }
        other => Err(json!({
            "code": -32601,
            "message": format!("unknown tool: {other}")
        })),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn list_names() -> Vec<String> {
        let tools = tools_list();
        tools["tools"]
            .as_array()
            .expect("tools array")
            .iter()
            .map(|t| t["name"].as_str().unwrap_or_default().to_string())
            .collect()
    }

    #[test]
    fn tools_list_includes_supersede() {
        let names = list_names();
        assert!(names.contains(&"search".to_string()), "{names:?}");
        assert!(names.contains(&"list_projects".to_string()), "{names:?}");
        assert!(names.contains(&"stats".to_string()), "{names:?}");
        assert!(names.contains(&"write".to_string()), "{names:?}");
        assert!(names.contains(&"forget".to_string()), "{names:?}");
        assert!(
            names.contains(&"supersede".to_string()),
            "supersede missing from {names:?}"
        );
    }

    #[test]
    fn tools_list_includes_recall() {
        let tools = tools_list();
        let tool = tools["tools"]
            .as_array()
            .unwrap()
            .iter()
            .find(|t| t["name"] == "recall")
            .expect("recall tool");
        let schema = &tool["inputSchema"];
        assert_eq!(schema["required"], json!(["query"]));
        let modes = schema["properties"]["mode"]["enum"]
            .as_array()
            .expect("enum");
        assert_eq!(modes.len(), 4);
        assert_eq!(
            schema["properties"]["rank"]["enum"],
            json!(["fulltext", "weighted"])
        );
        for k in ["project", "valid_at", "known_at", "limit"] {
            assert!(schema["properties"].get(k).is_some(), "{k}");
        }
    }

    #[test]
    fn tools_list_includes_project_context() {
        let tools = tools_list();
        let tool = tools["tools"]
            .as_array()
            .unwrap()
            .iter()
            .find(|t| t["name"] == "project_context")
            .expect("project_context tool");
        let schema = &tool["inputSchema"];
        assert_eq!(schema["required"], json!(["project"]));
        assert_eq!(schema["properties"]["budget"]["type"], "integer");
        assert_eq!(schema["properties"]["project"]["type"], "string");
        assert_eq!(schema["properties"]["task"]["type"], "string");
    }

    #[test]
    fn mcp_project_context_returns_a_pack() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping mcp_project_context_returns_a_pack: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("proj-{tag}");
        let written = call_text(
            &mut tx,
            "write",
            json!({
                "content": format!("we chose heron storage {tag}"),
                "project": project,
                "category": "decision"
            }),
        );
        let id = written["id"].as_i64().unwrap();
        let out = call_text(
            &mut tx,
            "project_context",
            json!({ "project": project, "budget": 1000 }),
        );
        assert_eq!(out["within_budget"], true, "{out}");
        let cited = out["citations"]
            .as_array()
            .expect("citations")
            .iter()
            .filter(|c| c["kind"] == "memory" && c["id"].as_i64() == Some(id))
            .count();
        assert_eq!(cited, 1, "{out}");
        for bad in [
            json!({}),
            json!({ "project": 5 }),
            json!({ "project": "" }),
            json!({ "project": project, "budget": "big" }),
            json!({ "project": project, "budget": 63 }),
            json!({ "project": project, "budget": 40000 }),
            json!({ "project": project, "task": 7 }),
            json!({ "project": project, "task": "x".repeat(2001) }),
        ] {
            let err = call_tool(
                &mut tx,
                &json!({ "name": "project_context", "arguments": bad }),
            )
            .expect_err("refused");
            assert_eq!(err["code"], -32602, "{bad}: {err}");
        }
    }

    fn call_text(client: &mut impl GenericClient, name: &str, args: Value) -> Value {
        let out = call_tool(client, &json!({ "name": name, "arguments": args })).expect("ok");
        let text = out["content"][0]["text"].as_str().expect("text");
        serde_json::from_str(text).expect("json payload")
    }

    #[test]
    fn mcp_recall_finds_a_project_note() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping mcp_recall_finds_a_project_note: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("proj-{tag}");
        let written = call_text(
            &mut tx,
            "write",
            json!({ "content": format!("zebrafinch rollout {tag}"), "project": project }),
        );
        let id = written["id"].as_i64().unwrap();
        let out = call_text(
            &mut tx,
            "recall",
            json!({ "query": format!("zebrafinch {tag}"), "project": project }),
        );
        let ids: Vec<i64> = out["hits"]
            .as_array()
            .expect("hits")
            .iter()
            .map(|h| h["id"].as_i64().unwrap())
            .collect();
        assert!(ids.contains(&id), "{out}");
        // current + full text needs no question vector, Ollama or not
        assert!(out["degraded"].is_null(), "{out}");
    }

    #[test]
    fn mcp_recall_rejects_bad_mode() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping mcp_recall_rejects_bad_mode: no PostgreSQL reachable");
            return;
        };
        let err = call_tool(
            &mut client,
            &json!({ "name": "recall", "arguments": { "query": "x", "mode": "foo" } }),
        )
        .unwrap_err();
        assert_eq!(err["code"], -32602);
    }

    #[test]
    fn mcp_recall_rejects_bad_rank() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping mcp_recall_rejects_bad_rank: no PostgreSQL reachable");
            return;
        };
        for rank in [json!("foo"), json!(5)] {
            let err = call_tool(
                &mut client,
                &json!({ "name": "recall", "arguments": { "query": "x", "rank": rank } }),
            )
            .err()
            .unwrap_or_else(|| panic!("rank {rank} must be refused"));
            assert_eq!(err["code"], -32602);
        }
    }

    #[test]
    fn mcp_recall_rejects_an_overlong_query() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping mcp_recall_rejects_an_overlong_query: no PostgreSQL reachable");
            return;
        };
        let query = format!("{}x", "abc ".repeat(500));
        let err = call_tool(
            &mut client,
            &json!({ "name": "recall", "arguments": { "query": query } }),
        )
        .expect_err("2001 characters must be refused");
        assert_eq!(err["code"], -32602);
    }

    #[test]
    fn mcp_search_honours_project() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping mcp_search_honours_project: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let (px, py) = (format!("px-{tag}"), format!("py-{tag}"));
        for (proj, word) in [(&px, "alpha"), (&py, "beta")] {
            let conv: i64 = tx
                .query_one(
                    "INSERT INTO conversations (session_id, project_path, started_at) \
                     VALUES ($1, '/tmp/' || $2::text, now()) RETURNING id",
                    &[&uuid::Uuid::new_v4(), proj],
                )
                .unwrap()
                .get(0);
            tx.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) \
                 VALUES ($1, 'user', $2, now())",
                &[&conv, &format!("{word} marker {tag}")],
            )
            .unwrap();
        }
        let out = call_text(&mut tx, "search", json!({ "query": tag, "project": px }));
        let rows = out.as_array().unwrap();
        assert_eq!(rows.len(), 1, "{out}");
        assert_eq!(rows[0]["project"], json!(px));
    }

    #[test]
    fn supersede_tool_schema_requires_id_and_content() {
        let tools = tools_list();
        let tool = tools["tools"]
            .as_array()
            .unwrap()
            .iter()
            .find(|t| t["name"] == "supersede")
            .expect("supersede tool");
        let schema = &tool["inputSchema"];
        assert_eq!(schema["type"], "object");
        let required = schema["required"]
            .as_array()
            .expect("required")
            .iter()
            .map(|v| v.as_str().unwrap_or_default().to_string())
            .collect::<Vec<_>>();
        assert_eq!(required, vec!["id".to_string(), "content".to_string()]);
        let props = schema["properties"].as_object().expect("properties");
        assert!(props.contains_key("id"), "{props:?}");
        assert!(props.contains_key("content"), "{props:?}");
        assert!(props.contains_key("category"), "{props:?}");
        assert_eq!(props["valid_from"]["type"], "string");
        assert_eq!(
            props["kind"]["enum"],
            serde_json::json!(["update", "correction"])
        );
    }

    /// Connect to the database (`PG*` env). Skip when unreachable so offline
    /// `cargo test --lib` stays green. Mirrors the server.rs test helper.
    fn test_db() -> Option<postgres::Client> {
        use postgres::NoTls;
        let host = std::env::var("PGHOST").unwrap_or_else(|_| "localhost".into());
        let user = std::env::var("PGUSER")
            .unwrap_or_else(|_| std::env::var("USERNAME").unwrap_or_else(|_| "postgres".into()));
        let dbname = std::env::var("PGDATABASE").unwrap_or_else(|_| "throughline".into());
        let mut cfg = postgres::Config::new();
        cfg.host(&host)
            .port(
                std::env::var("PGPORT")
                    .ok()
                    .and_then(|p| p.parse().ok())
                    .unwrap_or(5432),
            )
            .user(&user)
            .dbname(&dbname);
        if let Ok(pw) = std::env::var("PGPASSWORD") {
            cfg.password(pw);
        }
        cfg.connect(NoTls).ok()
    }

    /// One active note that starts at `from`.
    fn seed_note<C: GenericClient>(client: &mut C, from: chrono::DateTime<chrono::Utc>) -> i64 {
        let category_type =
            crate::extract_memory::category_column_type(client).expect("category_column_type");
        client
            .query_one(
                &format!(
                    "INSERT INTO memory_chunks (source_type, content, category, confidence, status, valid_from)
                     VALUES ('manual', 'mcp supersede seed note', 'decision'::text::{category_type}, 1.0, 'active', $1)
                     RETURNING id"
                ),
                &[&from],
            )
            .expect("seed note")
            .get(0)
    }

    fn supersede_call(args: Value) -> Value {
        json!({ "name": "supersede", "arguments": args })
    }

    #[test]
    fn supersede_from_today_on_a_note_of_today_starts_at_its_start() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping supersede_from_today_on_a_note_of_today: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let today = chrono::Utc::now().date_naive();
        let start = today.and_hms_opt(10, 0, 0).unwrap().and_utc();
        let old = seed_note(&mut tx, start);
        let out = call_tool(
            &mut tx,
            &supersede_call(json!({
                "id": old,
                "content": "mcp same-day successor",
                "valid_from": today.format("%Y-%m-%d").to_string(),
            })),
        )
        .expect("a plain date on the old note's first day is accepted");
        let text = out["content"][0]["text"].as_str().expect("text");
        let ids: Value = serde_json::from_str(text).expect("ids json");
        let new_id = ids["new_id"].as_i64().expect("new_id");
        let from: chrono::DateTime<chrono::Utc> = tx
            .query_one(
                "SELECT valid_from FROM memory_chunks WHERE id = $1",
                &[&new_id],
            )
            .expect("successor")
            .get(0);
        assert_eq!(from, start, "the successor starts at the old note's start");
    }

    #[test]
    fn supersede_rejects_non_string_kind_and_valid_from() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping supersede_rejects_non_string_kind: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let old = seed_note(&mut tx, "2026-01-01T00:00:00Z".parse().unwrap());
        for (field, value) in [
            ("kind", json!(5)),
            ("kind", json!(["correction"])),
            ("valid_from", json!(20260301)),
            ("valid_from", json!({ "date": "2026-03-01" })),
        ] {
            let mut args = json!({ "id": old, "content": "mcp typed successor" });
            args[field] = value.clone();
            let err = call_tool(&mut tx, &supersede_call(args))
                .expect_err("a non-string must not fall back to the default");
            assert_eq!(err["code"], -32602, "{field}={value}: {err}");
            assert!(
                err["message"].as_str().unwrap_or_default().contains(field),
                "{field}={value}: {err}"
            );
        }
        let by: Option<i64> = tx
            .query_one(
                "SELECT superseded_by FROM memory_chunks WHERE id = $1",
                &[&old],
            )
            .unwrap()
            .get(0);
        assert_eq!(by, None, "nothing was replaced");
        // null counts as absent
        call_tool(
            &mut tx,
            &supersede_call(json!({
                "id": old, "content": "mcp typed successor", "kind": null, "valid_from": null,
            })),
        )
        .expect("null means the default");
    }

    #[test]
    fn supersede_arg_validation_rejects_empty_content() {
        let err = validate_supersede("", Some("insight")).expect_err("empty content");
        assert!(matches!(err, SupersedeError::EmptyContent), "{err:?}");
        let err = validate_supersede("body", Some("not-a-category")).expect_err("bad category");
        assert!(matches!(err, SupersedeError::InvalidCategory(_)), "{err:?}");
    }
}
