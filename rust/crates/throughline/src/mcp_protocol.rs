//! Protocol constants and version negotiation shared by every MCP transport.

use serde_json::{json, Value};

/// The modern protocol era (`_meta`-based, `resultType`).
pub const MODERN_VERSION: &str = "2026-07-28";
/// Legacy versions served on every transport, newest first.
pub const LEGACY_VERSIONS: [&str; 2] = ["2025-11-25", "2025-06-18"];
/// A legacy version that only stdio still answers.
pub const STDIO_EXTRA_VERSION: &str = "2024-11-05";

pub const HEADER_MISMATCH: i64 = -32020;
pub const UNSUPPORTED_VERSION: i64 = -32022;
pub const METHOD_NOT_FOUND: i64 = -32601;
pub const INVALID_PARAMS: i64 = -32602;
pub const RESOURCE_NOT_FOUND: i64 = -32002;
pub const PARSE_ERROR: i64 = -32700;
pub const INVALID_REQUEST: i64 = -32600;

const VERSION_META_KEY: &str = "io.modelcontextprotocol/protocolVersion";

/// The protocol version a request names in `params._meta`, if any.
pub fn request_version(msg: &Value) -> Option<&str> {
    msg.get("params")?
        .get("_meta")?
        .get(VERSION_META_KEY)?
        .as_str()
}

/// Every version this transport answers: modern first, then legacy newest first.
pub fn supported_versions(transport: Transport) -> Vec<&'static str> {
    std::iter::once(MODERN_VERSION)
        .chain(LEGACY_VERSIONS)
        .chain((transport == Transport::Stdio).then_some(STDIO_EXTRA_VERSION))
        .collect()
}

/// The `-32022` error body for a request naming a version we do not serve.
pub fn unsupported_version(id: &Value, requested: &str, transport: Transport) -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": id,
        "error": {
            "code": UNSUPPORTED_VERSION,
            "message": "Unsupported protocol version",
            "data": { "supported": supported_versions(transport), "requested": requested }
        }
    })
}

/// The result of `server/discover`; `capabilities` is shared with `initialize`.
pub fn discover_result(capabilities: Value, transport: Transport) -> Value {
    json!({
        "resultType": "complete",
        "supportedVersions": supported_versions(transport),
        "capabilities": capabilities,
        "_meta": { "io.modelcontextprotocol/serverInfo": {
            "name": "throughline", "version": env!("CARGO_PKG_VERSION")
        } },
        "instructions": "Throughline is a local memory server over past AI sessions. Recall notes, pack project context and search the corpus."
    })
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Transport {
    Stdio,
    Http,
}

/// The legacy version to answer `initialize` with: the requested one when
/// this transport supports it, otherwise the newest legacy version.
pub fn negotiate_legacy(requested: Option<&str>, transport: Transport) -> &'static str {
    let supported = LEGACY_VERSIONS
        .iter()
        .copied()
        .chain((transport == Transport::Stdio).then_some(STDIO_EXTRA_VERSION));
    requested
        .and_then(|r| supported.into_iter().find(|v| *v == r))
        .unwrap_or(LEGACY_VERSIONS[0])
}

/// Which protocol era an HTTP request is answered in.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Era {
    Legacy,
    Modern,
}

/// A header value as sent. The form `=?base64?…?=` is decoded (standard
/// alphabet, padded, UTF-8); any other value is taken as it is. `None` when
/// the Base64 form does not decode.
pub fn decode_header_value(raw: &str) -> Option<String> {
    match raw
        .strip_prefix("=?base64?")
        .and_then(|r| r.strip_suffix("?="))
    {
        Some(encoded) => String::from_utf8(base64_decode(encoded)?).ok(),
        None => Some(raw.to_string()),
    }
}

/// Standard Base64 with padding; `None` on a bad length, character or pad.
fn base64_decode(input: &str) -> Option<Vec<u8>> {
    let bytes = input.as_bytes();
    if !bytes.len().is_multiple_of(4) {
        return None;
    }
    let quads = bytes.len() / 4;
    let mut out = Vec::with_capacity(quads * 3);
    for (i, quad) in bytes.chunks_exact(4).enumerate() {
        let pad = quad.iter().rev().take_while(|&&c| c == b'=').count();
        if pad > 2 || (pad > 0 && i + 1 < quads) {
            return None;
        }
        let mut n = 0u32;
        for &c in &quad[..4 - pad] {
            let sextet = match c {
                b'A'..=b'Z' => c - b'A',
                b'a'..=b'z' => c - b'a' + 26,
                b'0'..=b'9' => c - b'0' + 52,
                b'+' => 62,
                b'/' => 63,
                _ => return None,
            };
            n = (n << 6) | u32::from(sextet);
        }
        n <<= 6 * pad;
        out.extend_from_slice(&n.to_be_bytes()[1..4 - pad]);
    }
    Some(out)
}

/// The body field a method's `Mcp-Name` header must repeat.
fn name_field(method: &str) -> Option<&'static str> {
    match method {
        "tools/call" | "prompts/get" => Some("name"),
        "resources/read" => Some("uri"),
        _ => None,
    }
}

fn header_mismatch(id: &Value, message: &str, data: Option<Value>) -> (u16, Value) {
    let mut error = json!({ "code": HEADER_MISMATCH, "message": message });
    if let Some(data) = data {
        error["data"] = data;
    }
    (400, json!({ "jsonrpc": "2.0", "id": id, "error": error }))
}

/// The era of an HTTP request, from its headers and body. `Err` is the HTTP
/// status with the JSON-RPC error body. Header values that are not visible
/// ASCII must be passed as `None`.
///
/// In this order: `initialize` is legacy and needs no header. A `_meta`
/// version must equal `MCP-Protocol-Version`. A served legacy version is
/// legacy. `2026-07-28` is modern and needs `_meta`, `Mcp-Method` equal to
/// the method and, for `tools/call`, `resources/read` and `prompts/get`,
/// `Mcp-Name` equal to the name or URI. Any other version is unsupported,
/// and no version at all is refused.
pub fn classify_http(
    protocol_header: Option<&str>,
    method_header: Option<&str>,
    name_header: Option<&str>,
    msg: &Value,
) -> Result<Era, (u16, Value)> {
    let id = msg.get("id").cloned().unwrap_or(Value::Null);
    let method = msg.get("method").and_then(Value::as_str).unwrap_or("");
    if method == "initialize" {
        return Ok(Era::Legacy);
    }
    let meta = request_version(msg);
    let Some(version) = protocol_header else {
        if meta.is_some() {
            return Err(header_mismatch(
                &id,
                "Header mismatch: MCP-Protocol-Version header is missing",
                None,
            ));
        }
        let supported = supported_versions(Transport::Http);
        return Err(header_mismatch(
            &id,
            &format!(
                "MCP-Protocol-Version header is required; supported versions: {}",
                supported.join(", ")
            ),
            Some(json!({ "supported": supported })),
        ));
    };
    if meta.is_some_and(|m| m != version) {
        return Err(header_mismatch(
            &id,
            "Header mismatch: MCP-Protocol-Version differs from _meta",
            None,
        ));
    }
    if LEGACY_VERSIONS.contains(&version) {
        return Ok(Era::Legacy);
    }
    if version != MODERN_VERSION {
        return Err((400, unsupported_version(&id, version, Transport::Http)));
    }
    if meta.is_none() {
        return Err(header_mismatch(
            &id,
            "Header mismatch: _meta carries no protocol version",
            None,
        ));
    }
    if method_header != Some(method) {
        return Err(header_mismatch(
            &id,
            "Header mismatch: Mcp-Method differs from the method",
            None,
        ));
    }
    if let Some(field) = name_field(method) {
        let expected = msg
            .get("params")
            .and_then(|p| p.get(field))
            .and_then(Value::as_str);
        let named = name_header.and_then(decode_header_value);
        if expected.is_none() || named.as_deref() != expected {
            return Err(header_mismatch(
                &id,
                &format!("Header mismatch: Mcp-Name differs from params.{field}"),
                None,
            ));
        }
    }
    Ok(Era::Modern)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn negotiate_keeps_supported_versions() {
        for t in [Transport::Stdio, Transport::Http] {
            for v in ["2025-06-18", "2025-11-25"] {
                assert_eq!(negotiate_legacy(Some(v), t), v);
            }
        }
    }

    #[test]
    fn negotiate_falls_back_to_latest() {
        for t in [Transport::Stdio, Transport::Http] {
            for v in [Some("2025-03-26"), None, Some("x")] {
                assert_eq!(negotiate_legacy(v, t), "2025-11-25");
            }
        }
    }

    #[test]
    fn stdio_keeps_2024_11_05() {
        assert_eq!(
            negotiate_legacy(Some("2024-11-05"), Transport::Stdio),
            "2024-11-05"
        );
        assert_eq!(
            negotiate_legacy(Some("2024-11-05"), Transport::Http),
            "2025-11-25"
        );
    }

    const UMLAUT_URI: &str = "throughline://project/Übersicht/context";
    const UMLAUT_URI_B64: &str =
        "=?base64?dGhyb3VnaGxpbmU6Ly9wcm9qZWN0L8OcYmVyc2ljaHQvY29udGV4dA==?=";

    /// A modern request body: `_meta` names 2026-07-28.
    fn modern_msg(method: &str, params: Value) -> Value {
        let mut params = params;
        params["_meta"] = json!({ VERSION_META_KEY: MODERN_VERSION });
        json!({ "jsonrpc": "2.0", "id": 3, "method": method, "params": params })
    }

    fn header_error_code(result: Result<Era, (u16, Value)>) -> i64 {
        let (status, body) = result.expect_err("refused");
        assert_eq!(status, 400, "{body}");
        assert_eq!(body["jsonrpc"], "2.0", "{body}");
        body["error"]["code"].as_i64().expect("code")
    }

    #[test]
    fn mcp_name_base64_header_matches_body() {
        assert_eq!(
            decode_header_value(UMLAUT_URI_B64).as_deref(),
            Some(UMLAUT_URI)
        );
        assert_eq!(decode_header_value("recall").as_deref(), Some("recall"));
        assert_eq!(
            decode_header_value("=?base64?cmVjYWxs?=").as_deref(),
            Some("recall")
        );
        assert_eq!(decode_header_value("=?base64?YQ==?=").as_deref(), Some("a"));
        assert_eq!(
            decode_header_value("=?base64?YWI=?=").as_deref(),
            Some("ab")
        );
        let msg = modern_msg("resources/read", json!({ "uri": UMLAUT_URI }));
        assert_eq!(
            classify_http(
                Some(MODERN_VERSION),
                Some("resources/read"),
                Some(UMLAUT_URI_B64),
                &msg
            ),
            Ok(Era::Modern)
        );
    }

    #[test]
    fn broken_base64_is_none() {
        for raw in [
            "=?base64?cmVjYWx?=",  // length not a multiple of 4
            "=?base64?cmVj!Wxs?=", // not in the alphabet
            "=?base64?YQ==YQ==?=", // padding in the middle
            "=?base64?Y===?=",     // too much padding
            "=?base64?/w==?=",     // 0xFF is not UTF-8
        ] {
            assert_eq!(decode_header_value(raw), None, "{raw}");
        }
        let msg = modern_msg("tools/call", json!({ "name": "recall" }));
        assert_eq!(
            header_error_code(classify_http(
                Some(MODERN_VERSION),
                Some("tools/call"),
                Some("=?base64?cmVj!Wxs?="),
                &msg
            )),
            HEADER_MISMATCH
        );
    }

    #[test]
    fn missing_mcp_method_is_header_mismatch() {
        let msg = modern_msg("tools/list", json!({}));
        assert_eq!(
            header_error_code(classify_http(Some(MODERN_VERSION), None, None, &msg)),
            HEADER_MISMATCH
        );
        assert_eq!(
            header_error_code(classify_http(
                Some(MODERN_VERSION),
                Some("prompts/list"),
                None,
                &msg
            )),
            HEADER_MISMATCH
        );
        assert_eq!(
            classify_http(Some(MODERN_VERSION), Some("tools/list"), None, &msg),
            Ok(Era::Modern)
        );
    }

    #[test]
    fn mcp_name_differs_is_header_mismatch() {
        let call = modern_msg("tools/call", json!({ "name": "recall" }));
        let get = modern_msg("prompts/get", json!({ "name": "context" }));
        let read = modern_msg("resources/read", json!({ "uri": "throughline://note/1" }));
        for (msg, method, name) in [
            (&call, "tools/call", Some("search")),
            (&call, "tools/call", None),
            (&get, "prompts/get", Some("recall")),
            (&read, "resources/read", Some("throughline://note/2")),
            (&read, "resources/read", None),
        ] {
            assert_eq!(
                header_error_code(classify_http(Some(MODERN_VERSION), Some(method), name, msg)),
                HEADER_MISMATCH,
                "{method} {name:?}"
            );
        }
        assert_eq!(
            classify_http(
                Some(MODERN_VERSION),
                Some("tools/call"),
                Some("recall"),
                &call
            ),
            Ok(Era::Modern)
        );
        assert_eq!(
            classify_http(
                Some(MODERN_VERSION),
                Some("prompts/get"),
                Some("context"),
                &get
            ),
            Ok(Era::Modern)
        );
    }

    #[test]
    fn initialize_without_header_is_legacy() {
        let msg = json!({ "jsonrpc": "2.0", "id": 0, "method": "initialize",
                          "params": { "protocolVersion": "2025-11-25" } });
        assert_eq!(classify_http(None, None, None, &msg), Ok(Era::Legacy));
        // whatever the headers say (R4: initialize always selects legacy)
        assert_eq!(
            classify_http(Some("1999-01-01"), Some("tools/list"), None, &msg),
            Ok(Era::Legacy)
        );
    }

    #[test]
    fn request_without_any_version_is_rejected() {
        let msg = json!({ "jsonrpc": "2.0", "id": 9, "method": "tools/list" });
        let (status, body) = classify_http(None, None, None, &msg).expect_err("refused");
        assert_eq!(status, 400);
        assert_eq!(body["id"], 9, "{body}");
        assert_eq!(body["error"]["code"], HEADER_MISMATCH, "{body}");
        let message = body["error"]["message"].as_str().expect("message");
        for v in supported_versions(Transport::Http) {
            assert!(message.contains(v), "{message}");
        }
        // a notification has no id to echo
        let note = json!({ "jsonrpc": "2.0", "method": "notifications/initialized" });
        let (_, body) = classify_http(None, None, None, &note).expect_err("refused");
        assert!(body["id"].is_null(), "{body}");
    }

    #[test]
    fn legacy_header_is_legacy() {
        let bare = json!({ "jsonrpc": "2.0", "id": 1, "method": "tools/list" });
        for v in LEGACY_VERSIONS {
            assert_eq!(
                classify_http(Some(v), None, None, &bare),
                Ok(Era::Legacy),
                "{v}"
            );
            let with_meta = json!({ "jsonrpc": "2.0", "id": 1, "method": "tools/list",
                                    "params": { "_meta": { VERSION_META_KEY: v } } });
            assert_eq!(
                classify_http(Some(v), None, None, &with_meta),
                Ok(Era::Legacy),
                "{v}"
            );
        }
    }

    #[test]
    fn meta_without_header_is_header_mismatch() {
        let msg = modern_msg("tools/list", json!({}));
        assert_eq!(
            header_error_code(classify_http(None, Some("tools/list"), None, &msg)),
            HEADER_MISMATCH
        );
    }

    #[test]
    fn header_and_meta_differ_is_header_mismatch() {
        let msg = modern_msg("tools/list", json!({}));
        assert_eq!(
            header_error_code(classify_http(
                Some("2025-11-25"),
                Some("tools/list"),
                None,
                &msg
            )),
            HEADER_MISMATCH
        );
    }

    #[test]
    fn modern_header_needs_meta() {
        let bare = json!({ "jsonrpc": "2.0", "id": 1, "method": "tools/list" });
        assert_eq!(
            header_error_code(classify_http(
                Some(MODERN_VERSION),
                Some("tools/list"),
                None,
                &bare
            )),
            HEADER_MISMATCH
        );
    }

    #[test]
    fn unknown_header_version_is_unsupported() {
        let bare = json!({ "jsonrpc": "2.0", "id": 5, "method": "tools/list" });
        // 2024-11-05 is served over stdio only
        for v in ["2030-01-01", "2024-11-05", "2025-03-26"] {
            let (status, body) =
                classify_http(Some(v), Some("tools/list"), None, &bare).expect_err("refused");
            assert_eq!(status, 400);
            assert_eq!(
                body,
                unsupported_version(&json!(5), v, Transport::Http),
                "{v}"
            );
            assert_eq!(body["error"]["code"], UNSUPPORTED_VERSION);
        }
        // an equal `_meta` does not make an unknown version acceptable
        let with_meta = json!({ "jsonrpc": "2.0", "id": 5, "method": "tools/list",
                                "params": { "_meta": { VERSION_META_KEY: "2030-01-01" } } });
        assert_eq!(
            header_error_code(classify_http(Some("2030-01-01"), None, None, &with_meta)),
            UNSUPPORTED_VERSION
        );
    }
}
