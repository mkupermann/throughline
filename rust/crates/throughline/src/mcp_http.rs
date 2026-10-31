//! MCP over Streamable HTTP at `POST /mcp`: one JSON-RPC message per
//! request, answered with plain JSON. Only agents holding the agent key get
//! in: `THROUGHLINE_AGENT_KEY` in local mode, a key of their account in team
//! mode.

use crate::access::{Actor, AuthMode};
use crate::agent_key::{check_bearer, KeyCheck};
use crate::mcp::{dispatch, McpContext, Reply};
use crate::mcp_protocol::{classify_http, Transport, INVALID_REQUEST, PARSE_ERROR};
use crate::server::{account_key_actor, api_error, unauthorized, with_db, AppState};
use axum::body::Body;
use axum::extract::State;
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::{Extension, Json};
use serde_json::{json, Value};

/// Largest request body read, in bytes.
const MAX_BODY: usize = 1 << 20;

fn status_of(code: u16) -> StatusCode {
    StatusCode::from_u16(code).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR)
}

fn json_reply(status: u16, body: Value) -> Response {
    (status_of(status), Json(body)).into_response()
}

/// 400 with a JSON-RPC error for a body that is no single request; the id
/// is unknown, so it is null.
fn invalid(code: i64, message: &str) -> Response {
    json_reply(
        400,
        json!({ "jsonrpc": "2.0", "id": null, "error": { "code": code, "message": message } }),
    )
}

/// One JSON-RPC message. The key is checked before the body is read, so a
/// client without it learns nothing from body handling. Local mode:
/// `THROUGHLINE_AGENT_KEY`, and the actor is [`Actor::local`]. Team mode: the
/// key of an account, which is the actor with its role; the shared key is
/// not consulted, and no key, an unknown or revoked one or one of a disabled
/// account get the same 401.
pub async fn mcp_post(
    State(state): State<AppState>,
    actor: Option<Extension<Actor>>,
    headers: HeaderMap,
    body: Body,
) -> Response {
    let actor = match state.auth_mode {
        AuthMode::Team => match account_key_actor(&state, &headers).await {
            Ok(Some(actor)) => actor,
            Ok(None) => return unauthorized("invalid credentials"),
            Err(resp) => return resp,
        },
        AuthMode::Local => {
            // A header that is not text is checked as an empty value and fails.
            let authorization = headers
                .get(header::AUTHORIZATION)
                .map(|v| v.to_str().unwrap_or(""));
            match check_bearer(&state.agent_key, authorization) {
                KeyCheck::Accepted => {}
                KeyCheck::Off | KeyCheck::TooShort => {
                    return api_error(
                        StatusCode::SERVICE_UNAVAILABLE,
                        "agent_access_off",
                        "agent access is off: set THROUGHLINE_AGENT_KEY (at least 32 characters) and restart",
                    );
                }
                KeyCheck::Missing | KeyCheck::Wrong => return unauthorized("invalid credentials"),
            }
            actor.map_or_else(Actor::shared_only, |Extension(actor)| actor)
        }
    };
    // Any read error counts as too large: the limit is the error that can be
    // answered, a dropped connection gets no answer anyway.
    let Ok(bytes) = axum::body::to_bytes(body, MAX_BODY).await else {
        return api_error(
            StatusCode::PAYLOAD_TOO_LARGE,
            "payload_too_large",
            "the request body is over 1 MiB",
        );
    };
    let Ok(msg) = serde_json::from_slice::<Value>(&bytes) else {
        return invalid(PARSE_ERROR, "Parse error");
    };
    if !msg.is_object() {
        return invalid(
            INVALID_REQUEST,
            "Invalid Request: one JSON-RPC message per POST, no batches",
        );
    }
    // A header that is not visible ASCII counts as missing.
    let text = |name: &str| headers.get(name).and_then(|v| v.to_str().ok());
    if let Err((status, body)) = classify_http(
        text("mcp-protocol-version"),
        text("mcp-method"),
        text("mcp-name"),
        &msg,
    ) {
        return json_reply(status, body);
    }
    let ctx = McpContext {
        transport: Transport::Http,
        read_only: state.mcp_read_only,
        actor,
    };
    // vereinfacht: Einbetten der Frage läuft unter der DB-Sperre, ausbauen wenn K7 reißt
    match with_db(&state, move |client| Ok(dispatch(client, &msg, &ctx))).await {
        Ok(Reply {
            status,
            body: Some(body),
        }) => json_reply(status, body),
        Ok(Reply { status, body: None }) => status_of(status).into_response(),
        Err(resp) => resp,
    }
}

/// `GET` and `DELETE /mcp`: no event stream, no session to end.
pub async fn mcp_other() -> Response {
    (StatusCode::METHOD_NOT_ALLOWED, [(header::ALLOW, "POST")]).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agent_key::{key_state_from, KeyState};
    use crate::server::app;
    use reqwest::Method;
    use std::sync::{Arc, Mutex};

    const KEY: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    const VERSION_KEY: &str = "io.modelcontextprotocol/protocolVersion";
    const LEGACY: (&str, &str) = ("mcp-protocol-version", "2025-11-25");

    fn bearer() -> String {
        format!("Bearer {KEY}")
    }

    /// A real listener around `app`, on a transaction rolled back on drop.
    /// Field order matters: the runtime drops before the state, so the last
    /// handle on the sync client goes away outside the runtime.
    struct TestServer {
        rt: tokio::runtime::Runtime,
        state: AppState,
        base: String,
    }

    struct Answer {
        status: u16,
        headers: reqwest::header::HeaderMap,
        body: String,
    }

    impl Answer {
        fn json(&self) -> Value {
            serde_json::from_str(&self.body)
                .unwrap_or_else(|e| panic!("json body ({e}): {}", self.body))
        }

        fn header(&self, name: &str) -> Option<&str> {
            self.headers.get(name).and_then(|v| v.to_str().ok())
        }
    }

    impl TestServer {
        fn start(key: KeyState) -> Option<Self> {
            // Connect outside any runtime: the sync client blocks.
            let mut client = test_db()?;
            client.batch_execute("BEGIN").expect("begin");
            let state = AppState {
                db: Arc::new(Mutex::new(client)),
                agent_key: Arc::new(key),
                mcp_read_only: false,
                auth_mode: crate::access::AuthMode::Local,
                public_url: None,
                login: Default::default(),
            };
            let rt = tokio::runtime::Runtime::new().expect("tokio runtime");
            let listener = rt
                .block_on(tokio::net::TcpListener::bind("127.0.0.1:0"))
                .expect("bind");
            let base = format!("http://{}", listener.local_addr().expect("addr"));
            let router = app(state.clone());
            rt.spawn(async move { axum::serve(listener, router).await.expect("serve") });
            Some(Self { rt, state, base })
        }

        fn send(
            &self,
            method: Method,
            path: &str,
            headers: &[(&str, &str)],
            body: Option<Vec<u8>>,
        ) -> Answer {
            self.rt.block_on(async {
                let client = reqwest::Client::builder()
                    .no_proxy()
                    .build()
                    .expect("client");
                let mut req = client.request(method, format!("{}{path}", self.base));
                for (name, value) in headers {
                    req = req.header(*name, *value);
                }
                if let Some(body) = body {
                    req = req.body(body);
                }
                let resp = req.send().await.expect("send");
                let status = resp.status().as_u16();
                let headers = resp.headers().clone();
                let body = resp.text().await.expect("body");
                Answer {
                    status,
                    headers,
                    body,
                }
            })
        }

        /// `POST /mcp` with the `Accept` header Claude Code sends.
        fn post(&self, headers: &[(&str, &str)], body: impl Into<Vec<u8>>) -> Answer {
            let mut all = vec![("accept", "application/json, text/event-stream")];
            all.extend_from_slice(headers);
            self.send(Method::POST, "/mcp", &all, Some(body.into()))
        }

        fn uses(&self, id: i64) -> i32 {
            self.state
                .db
                .lock()
                .expect("lock")
                .query_one(
                    "SELECT COALESCE(access_count, 0) FROM memory_chunks WHERE id = $1",
                    &[&id],
                )
                .expect("row")
                .get(0)
        }
    }

    impl Drop for TestServer {
        fn drop(&mut self) {
            if let Ok(mut client) = self.state.db.lock() {
                let _ = client.batch_execute("ROLLBACK");
            }
        }
    }

    fn tools_list(id: i64) -> String {
        json!({ "jsonrpc": "2.0", "id": id, "method": "tools/list" }).to_string()
    }

    /// The JSON payload of a `tools/call` result.
    fn tool_payload(answer: &Answer) -> Value {
        let v = answer.json();
        let text = v["result"]["content"][0]["text"]
            .as_str()
            .unwrap_or_else(|| panic!("tool text: {v}"));
        serde_json::from_str(text).expect("json payload")
    }

    #[test]
    fn mcp_needs_the_key() {
        let Some(server) = TestServer::start(key_state_from(Some(KEY))) else {
            eprintln!("skipping mcp_needs_the_key: no PostgreSQL reachable");
            return;
        };
        let auth = bearer();
        let wrong = format!("Bearer {}", "f".repeat(64));
        for headers in [
            vec![LEGACY],
            vec![LEGACY, ("authorization", wrong.as_str())],
            vec![LEGACY, ("authorization", "Basic abc")],
        ] {
            let a = server.post(&headers, tools_list(1));
            assert_eq!(a.status, 401, "{headers:?}: {}", a.body);
            assert_eq!(
                a.header("www-authenticate"),
                Some("Bearer realm=\"throughline\"")
            );
            assert_eq!(a.json()["error"], "unauthorized");
            assert!(!a.body.contains(KEY), "the key must not leak");
        }
        // The key is checked before the body is read or parsed.
        let a = server.post(&[], "not json");
        assert_eq!(a.status, 401, "{}", a.body);
        let a = server.post(&[LEGACY, ("authorization", &auth)], tools_list(1));
        assert_eq!(a.status, 200, "{}", a.body);
        drop(server);

        for state in [KeyState::Off, KeyState::TooShort] {
            let Some(server) = TestServer::start(state.clone()) else {
                return;
            };
            for headers in [vec![LEGACY], vec![LEGACY, ("authorization", &auth)]] {
                let a = server.post(&headers, tools_list(1));
                assert_eq!(a.status, 503, "{state:?}: {}", a.body);
                let v = a.json();
                assert_eq!(v["error"], "agent_access_off", "{v}");
                let detail = v["detail"].as_str().expect("detail");
                assert!(detail.contains("THROUGHLINE_AGENT_KEY"), "{detail}");
                assert!(!a.body.contains(KEY), "the key must not leak");
            }
        }
    }

    #[test]
    fn mcp_foreign_origin_is_403_even_with_key() {
        let Some(server) = TestServer::start(key_state_from(Some(KEY))) else {
            eprintln!("skipping mcp_foreign_origin_is_403_even_with_key: no PostgreSQL reachable");
            return;
        };
        let auth = bearer();
        for foreign in [
            ("origin", "https://evil.example"),
            ("origin", "null"),
            ("sec-fetch-site", "cross-site"),
        ] {
            let a = server.post(&[LEGACY, ("authorization", &auth), foreign], tools_list(1));
            assert_eq!(a.status, 403, "{foreign:?}: {}", a.body);
            assert_eq!(a.json()["error"], "forbidden_request");
        }
        // Its own origin passes.
        let a = server.post(
            &[LEGACY, ("authorization", &auth), ("origin", &server.base)],
            tools_list(1),
        );
        assert_eq!(a.status, 200, "{}", a.body);
    }

    #[test]
    fn mcp_rejects_batches_and_garbage() {
        let Some(server) = TestServer::start(key_state_from(Some(KEY))) else {
            eprintln!("skipping mcp_rejects_batches_and_garbage: no PostgreSQL reachable");
            return;
        };
        let auth = bearer();
        let batch = format!("[{}]", tools_list(1));
        for (body, code) in [
            ("not json", -32700),
            ("", -32700),
            ("{\"jsonrpc\":", -32700),
            (batch.as_str(), -32600),
            ("[]", -32600),
            ("42", -32600),
            ("\"tools/list\"", -32600),
            ("null", -32600),
        ] {
            let a = server.post(&[LEGACY, ("authorization", &auth)], body);
            assert_eq!(a.status, 400, "{body}: {}", a.body);
            assert_eq!(a.header("content-type"), Some("application/json"));
            let v = a.json();
            assert_eq!(v["jsonrpc"], "2.0", "{v}");
            assert_eq!(v["error"]["code"], code, "{body}: {v}");
            assert!(v["id"].is_null(), "{v}");
        }
        // Exactly 1 MiB is read, one byte more is refused.
        let mut fits = tools_list(2).into_bytes();
        fits.resize(1 << 20, b' ');
        let a = server.post(&[LEGACY, ("authorization", &auth)], fits);
        assert_eq!(a.status, 200, "{}", a.body);
        assert_eq!(a.json()["id"], 2);
        let mut over = tools_list(3).into_bytes();
        over.resize((1 << 20) + 1, b' ');
        let a = server.post(&[LEGACY, ("authorization", &auth)], over);
        assert_eq!(a.status, 413, "{}", a.body);
    }

    #[test]
    fn mcp_get_and_delete_are_405() {
        let Some(server) = TestServer::start(key_state_from(Some(KEY))) else {
            eprintln!("skipping mcp_get_and_delete_are_405: no PostgreSQL reachable");
            return;
        };
        let auth = bearer();
        for method in [Method::GET, Method::DELETE] {
            for headers in [vec![], vec![("authorization", auth.as_str())]] {
                let a = server.send(method.clone(), "/mcp", &headers, None);
                assert_eq!(a.status, 405, "{method}: {}", a.body);
                assert!(a.body.is_empty(), "{method}: {}", a.body);
                assert_eq!(a.header("allow"), Some("POST"));
            }
        }
    }

    #[test]
    fn mcp_notification_is_202_without_body() {
        let Some(server) = TestServer::start(key_state_from(Some(KEY))) else {
            eprintln!("skipping mcp_notification_is_202_without_body: no PostgreSQL reachable");
            return;
        };
        let auth = bearer();
        let note = json!({ "jsonrpc": "2.0", "method": "notifications/initialized" });
        let a = server.post(&[LEGACY, ("authorization", &auth)], note.to_string());
        assert_eq!(a.status, 202, "{}", a.body);
        assert!(a.body.is_empty(), "{}", a.body);
        assert_eq!(a.header("content-type"), None);
    }

    #[test]
    fn mcp_legacy_handshake_then_tools_list() {
        let Some(server) = TestServer::start(key_state_from(Some(KEY))) else {
            eprintln!("skipping mcp_legacy_handshake_then_tools_list: no PostgreSQL reachable");
            return;
        };
        let auth = bearer();
        let auth = ("authorization", auth.as_str());

        // What Claude Code 2.1.312 sends first.
        let discover = json!({
            "jsonrpc": "2.0", "id": 0, "method": "server/discover",
            "params": { "_meta": {
                VERSION_KEY: "2026-07-28",
                "io.modelcontextprotocol/clientInfo": { "name": "claude-code", "version": "2.1.312" },
                "io.modelcontextprotocol/clientCapabilities": {}
            } }
        });
        let a = server.post(
            &[
                auth,
                ("mcp-protocol-version", "2026-07-28"),
                ("mcp-method", "server/discover"),
            ],
            discover.to_string(),
        );
        assert_eq!(a.status, 200, "{}", a.body);
        assert_eq!(a.header("content-type"), Some("application/json"));
        let v = a.json();
        assert_eq!(v["id"], 0);
        assert_eq!(v["result"]["resultType"], "complete", "{v}");
        assert_eq!(
            v["result"]["supportedVersions"],
            json!(["2026-07-28", "2025-11-25", "2025-06-18"]),
            "{v}"
        );

        let init = json!({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": { "name": "claude-code", "version": "2.1.312" }
            }
        });
        let a = server.post(&[auth], init.to_string());
        assert_eq!(a.status, 200, "{}", a.body);
        let v = a.json();
        assert_eq!(v["id"], 1);
        assert_eq!(v["result"]["protocolVersion"], "2025-11-25", "{v}");
        assert_eq!(v["result"]["serverInfo"]["name"], "throughline");
        assert!(v["result"].get("resultType").is_none(), "{v}");

        let note = json!({ "jsonrpc": "2.0", "method": "notifications/initialized" });
        let a = server.post(&[auth, LEGACY], note.to_string());
        assert_eq!(a.status, 202, "{}", a.body);
        assert!(a.body.is_empty());

        let a = server.post(&[auth, LEGACY], tools_list(2));
        assert_eq!(a.status, 200, "{}", a.body);
        let v = a.json();
        assert_eq!(v["id"], 2);
        assert!(v["result"].get("resultType").is_none(), "{v}");
        let names: Vec<&str> = v["result"]["tools"]
            .as_array()
            .expect("tools")
            .iter()
            .filter_map(|t| t["name"].as_str())
            .collect();
        for name in ["recall", "project_context", "search"] {
            assert!(names.contains(&name), "{name} in {names:?}");
        }
    }

    #[test]
    fn mcp_http_recall_counts_like_stdio() {
        let Some(server) = TestServer::start(key_state_from(Some(KEY))) else {
            eprintln!("skipping mcp_http_recall_counts_like_stdio: no PostgreSQL reachable");
            return;
        };
        let auth = bearer();
        let auth = ("authorization", auth.as_str());
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let project = format!("proj-{tag}");
        let write = |id: i64, content: String| {
            let msg = json!({
                "jsonrpc": "2.0", "id": id, "method": "tools/call",
                "params": { "name": "write",
                            "arguments": { "content": content, "project": project } }
            });
            let a = server.post(&[auth, LEGACY], msg.to_string());
            assert_eq!(a.status, 200, "{}", a.body);
            tool_payload(&a)["id"].as_i64().expect("id")
        };
        let hit = write(1, format!("quokka{tag} rollout"));
        let miss = write(2, format!("numbat{tag} burrow"));

        // The recall goes the modern way, with every header checked.
        let recall = json!({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {
                "name": "recall",
                "arguments": { "query": format!("quokka{tag}"), "project": project, "limit": 1 },
                "_meta": { VERSION_KEY: "2026-07-28" }
            }
        });
        let a = server.post(
            &[
                auth,
                ("mcp-protocol-version", "2026-07-28"),
                ("mcp-method", "tools/call"),
                ("mcp-name", "recall"),
            ],
            recall.to_string(),
        );
        assert_eq!(a.status, 200, "{}", a.body);
        assert_eq!(a.json()["result"]["resultType"], "complete");
        let out = tool_payload(&a);
        let ids: Vec<i64> = out["hits"]
            .as_array()
            .expect("hits")
            .iter()
            .map(|h| h["id"].as_i64().expect("id"))
            .collect();
        assert_eq!(ids, vec![hit], "{out}");
        assert_eq!(server.uses(hit), 1, "the hit counts, as over stdio");
        assert_eq!(server.uses(miss), 0, "the miss does not");
    }

    #[test]
    fn ui_routes_unchanged() {
        let Some(server) = TestServer::start(KeyState::Off) else {
            eprintln!("skipping ui_routes_unchanged: no PostgreSQL reachable");
            return;
        };
        let a = server.send(Method::GET, "/api/health", &[], None);
        assert_eq!(a.status, 200, "{}", a.body);
        assert_eq!(a.json()["status"], "ok");
        let a = server.send(Method::GET, "/", &[], None);
        assert_eq!(a.status, 200);
        assert!(a.body.contains("<html"), "the page itself");
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
}
