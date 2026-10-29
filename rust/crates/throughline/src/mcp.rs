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
            let hits = search::search(client, query, limit)
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
            let kind = match args.get("kind").and_then(|k| k.as_str()).map(str::trim) {
                None | Some("") => SupersedeKind::Update,
                Some(k) => match SupersedeKind::parse(k) {
                    Some(k) => k,
                    None => {
                        return Err(json!({
                            "code": -32602,
                            "message": "kind must be update or correction"
                        }))
                    }
                },
            };
            let valid_from = match args
                .get("valid_from")
                .and_then(|v| v.as_str())
                .map(str::trim)
            {
                None | Some("") => None,
                Some(v) => match crate::temporal::parse_instant(v) {
                    Some(t) => Some(t),
                    None => {
                        return Err(json!({
                            "code": -32602,
                            "message": "valid_from must be an RFC 3339 timestamp or a YYYY-MM-DD date"
                        }))
                    }
                },
            };
            let successor = Successor {
                content: &content,
                category: category.as_deref(),
                source_type: "mcp_write",
                kind,
                valid_from,
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

    #[test]
    fn supersede_arg_validation_rejects_empty_content() {
        let err = validate_supersede("", Some("insight")).expect_err("empty content");
        assert!(matches!(err, SupersedeError::EmptyContent), "{err:?}");
        let err = validate_supersede("body", Some("not-a-category")).expect_err("bad category");
        assert!(matches!(err, SupersedeError::InvalidCategory(_)), "{err:?}");
    }
}
