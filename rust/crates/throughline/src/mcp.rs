//! Minimal MCP stdio server (JSON-RPC 2.0) exposing memory tools.

use crate::search;
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
            }
        ]
    })
}

fn call_tool<C: GenericClient>(client: &mut C, params: &Value) -> Result<Value, Value> {
    let name = params
        .get("name")
        .and_then(|n| n.as_str())
        .unwrap_or_default();
    let args = params.get("arguments").cloned().unwrap_or(Value::Object(Default::default()));
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
            let hits = search::search(client, query, limit).map_err(|e| {
                json!({ "code": -32000, "message": e.to_string() })
            })?;
            let payload = serde_json::to_string(&hits.iter().map(|h| json!({
                "conversation_id": h.conversation_id,
                "project": h.project_name,
                "role": h.role,
                "snippet": h.snippet,
            })).collect::<Vec<_>>())
            .unwrap_or_else(|_| "[]".into());
            Ok(text_result(payload))
        }
        "list_projects" => {
            let rows = client
                .query("SELECT name FROM projects ORDER BY name", &[])
                .map_err(|e| json!({ "code": -32000, "message": e.to_string() }))?;
            let names: Vec<String> = rows.into_iter().map(|r| r.get(0)).collect();
            Ok(text_result(serde_json::to_string(&names).unwrap_or_default()))
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
            let id: i64 = client
                .query_one(
                    r#"
                    INSERT INTO memory_chunks (source_type, content, category, project_name, confidence, status)
                    VALUES ('mcp_write', $1, $2::memory_category, $3, 0.9, 'active')
                    RETURNING id
                    "#,
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
        other => Err(json!({
            "code": -32601,
            "message": format!("unknown tool: {other}")
        })),
    }
}
