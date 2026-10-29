//! Export the corpus as OTLP/JSON traces (OpenTelemetry GenAI conventions).
//!
//! Pure structure logic lives in [`build_otlp_export`] and the id/attribute
//! helpers; unit tests exercise those against in-memory data only. The DB
//! layer ([`export_otel`]) loads rows and delegates to the builder.

use anyhow::{Context, Result};
use chrono::{DateTime, NaiveDate, Utc};
use postgres::GenericClient;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::Path;

/// One message row, source-agnostic, ready for span building.
#[derive(Debug, Clone)]
pub struct OtelMessage {
    pub uuid: Option<String>,
    pub role: String,
    pub content: String,
    pub created_at: Option<DateTime<Utc>>,
    pub model: Option<String>,
    pub token_count: Option<i64>,
}

/// One conversation row plus its messages, ready for trace building.
#[derive(Debug, Clone)]
pub struct OtelConversation {
    pub session_id: String,
    pub source_tool: Option<String>,
    pub model: Option<String>,
    pub started_at: DateTime<Utc>,
    pub ended_at: Option<DateTime<Utc>>,
    pub messages: Vec<OtelMessage>,
}

#[derive(Debug, Default)]
pub struct ExportSummary {
    pub traces: usize,
    pub spans: usize,
}

fn sha256_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    format!("{:x}", hasher.finalize())
}

/// Deterministic 32-hex trace id = SHA-256 prefix of the session_id.
pub fn trace_id(session_id: &str) -> String {
    sha256_hex(session_id.as_bytes())[..32].to_string()
}

/// Deterministic 16-hex root span id = SHA-256 prefix of session_id + "root".
pub fn root_span_id(session_id: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(session_id.as_bytes());
    hasher.update(b"root");
    format!("{:x}", hasher.finalize())[..16].to_string()
}

/// Deterministic 16-hex message span id = SHA-256 prefix of the message UUID.
pub fn message_span_id(message_uuid: &str) -> String {
    sha256_hex(message_uuid.as_bytes())[..16].to_string()
}

/// OTLP AnyValue attribute with a string value.
fn attr_string(key: &str, value: &str) -> Value {
    json!({"key": key, "value": {"stringValue": value}})
}

/// OTLP AnyValue attribute with an integer value (encoded as decimal string).
fn attr_int(key: &str, value: i64) -> Value {
    json!({"key": key, "value": {"intValue": value.to_string()}})
}

/// Nanoseconds since the Unix epoch as a decimal string.
fn unix_nanos(dt: DateTime<Utc>) -> String {
    let nanos = (dt.timestamp() as i128) * 1_000_000_000 + (dt.timestamp_subsec_nanos() as i128);
    nanos.to_string()
}

/// Optional string attribute: skipped when the field is absent.
fn opt_attr_string(key: &str, value: Option<&str>) -> Option<Value> {
    value.map(|v| attr_string(key, v))
}

/// Build one message child-span (parent = conversation root span).
fn message_span(
    conv: &OtelConversation,
    msg: &OtelMessage,
    span_id: &str,
    root_id: &str,
    trace: &str,
    include_content: bool,
    fallback_start: DateTime<Utc>,
    fallback_end: DateTime<Utc>,
) -> Value {
    let start = msg.created_at.unwrap_or(fallback_start);
    let end = msg.created_at.unwrap_or(fallback_end);
    let mut attributes = vec![
        attr_string("gen_ai.conversation.id", &conv.session_id),
        attr_string("gen_ai.message.role", &msg.role),
    ];
    if let Some(a) = opt_attr_string("gen_ai.system", conv.source_tool.as_deref()) {
        attributes.push(a);
    }
    let model = msg.model.as_deref().or(conv.model.as_deref());
    if let Some(a) = opt_attr_string("gen_ai.request.model", model) {
        attributes.push(a);
    }
    if let Some(n) = msg.token_count {
        if msg.role == "user" {
            attributes.push(attr_int("gen_ai.usage.input_tokens", n));
        } else if msg.role == "assistant" {
            attributes.push(attr_int("gen_ai.usage.output_tokens", n));
        }
    }
    if include_content {
        attributes.push(attr_string("gen_ai.message.content", &msg.content));
    }
    json!({
        "traceId": trace,
        "spanId": span_id,
        "parentSpanId": root_id,
        "name": format!("{} message", msg.role),
        "startTimeUnixNano": unix_nanos(start),
        "endTimeUnixNano": unix_nanos(end),
        "attributes": attributes,
    })
}

/// Build the conversation root span.
fn root_span(
    conv: &OtelConversation,
    span_id: &str,
    trace: &str,
    fallback_end: DateTime<Utc>,
) -> Value {
    let end = conv.ended_at.unwrap_or(fallback_end);
    let mut attributes = vec![attr_string("gen_ai.conversation.id", &conv.session_id)];
    if let Some(a) = opt_attr_string("gen_ai.system", conv.source_tool.as_deref()) {
        attributes.push(a);
    }
    if let Some(a) = opt_attr_string("gen_ai.request.model", conv.model.as_deref()) {
        attributes.push(a);
    }
    json!({
        "traceId": trace,
        "spanId": span_id,
        "name": format!("conversation {}", conv.session_id),
        "startTimeUnixNano": unix_nanos(conv.started_at),
        "endTimeUnixNano": unix_nanos(end),
        "attributes": attributes,
    })
}

/// Assemble the full OTLP/JSON export document.
///
/// Pure: takes in-memory conversations, no I/O. Field order of the envelope is
/// fixed by serde_json's map (alphabetical); arrays are ordered explicitly
/// (conversations by started_at then session_id, spans by start time then id)
/// so two runs over the same corpus are byte-identical.
pub fn build_otlp_export(
    conversations: &[OtelConversation],
    include_content: bool,
    tool_version: &str,
) -> Value {
    let mut sorted: Vec<&OtelConversation> = conversations.iter().collect();
    sorted.sort_by(|a, b| {
        a.started_at
            .cmp(&b.started_at)
            .then_with(|| a.session_id.cmp(&b.session_id))
    });

    let mut spans: Vec<Value> = Vec::new();
    for conv in &sorted {
        let trace = trace_id(&conv.session_id);
        let root_id = root_span_id(&conv.session_id);
        let fallback_end = conv
            .ended_at
            .or_else(|| conv.messages.iter().filter_map(|m| m.created_at).max())
            .unwrap_or(conv.started_at);

        spans.push(root_span(conv, &root_id, &trace, fallback_end));

        let mut msgs: Vec<(String, &OtelMessage)> = conv
            .messages
            .iter()
            .enumerate()
            .map(|(i, m)| {
                let sid = match &m.uuid {
                    Some(u) => message_span_id(u),
                    None => {
                        let mut h = Sha256::new();
                        h.update(conv.session_id.as_bytes());
                        h.update(b":");
                        h.update(i.to_string().as_bytes());
                        format!("{:x}", h.finalize())[..16].to_string()
                    }
                };
                (sid, m)
            })
            .collect();
        msgs.sort_by(|a, b| {
            let ta = a.1.created_at.unwrap_or(conv.started_at);
            let tb = b.1.created_at.unwrap_or(conv.started_at);
            ta.cmp(&tb).then_with(|| a.0.cmp(&b.0))
        });

        for (sid, m) in &msgs {
            spans.push(message_span(
                conv,
                m,
                sid,
                &root_id,
                &trace,
                include_content,
                conv.started_at,
                fallback_end,
            ));
        }
    }

    json!({
        "resourceSpans": [{
            "resource": {
                "attributes": [
                    attr_string("service.name", "throughline"),
                    attr_string("service.version", tool_version),
                ]
            },
            "scopeSpans": [{
                "scope": {
                    "name": "throughline",
                    "version": tool_version,
                },
                "spans": spans,
            }]
        }]
    })
}

/// Load conversations + messages and write the OTLP/JSON export to `out`.
pub fn export_otel<C: GenericClient>(
    client: &mut C,
    out: &Path,
    project: Option<&str>,
    since: Option<NaiveDate>,
    include_content: bool,
) -> Result<ExportSummary> {
    let since_ts = since.map(|d| d.and_hms_opt(0, 0, 0).unwrap().and_utc());
    let rows = client.query(
        r#"
        SELECT id, session_id::text, source_tool, model, started_at, ended_at
        FROM conversations
        WHERE ($1::text IS NULL OR project_name = $1)
          AND ($2::timestamptz IS NULL OR started_at >= $2)
        ORDER BY started_at ASC, session_id ASC
        "#,
        &[&project, &since_ts],
    )?;

    let mut conversations = Vec::with_capacity(rows.len());
    for row in rows {
        let id: i64 = row.get(0);
        let session_id: String = row.get(1);
        let source_tool: Option<String> = row.get(2);
        let model: Option<String> = row.get(3);
        let started_at: DateTime<Utc> = row.get(4);
        let ended_at: Option<DateTime<Utc>> = row.get(5);

        let msgs = client.query(
            r#"
            SELECT uuid::text, role::text, COALESCE(content, ''),
                   created_at, model, token_count
            FROM messages
            WHERE conversation_id = $1
            ORDER BY created_at ASC, id ASC
            "#,
            &[&id],
        )?;
        let messages = msgs
            .into_iter()
            .map(|m| OtelMessage {
                uuid: m.get(0),
                role: m.get(1),
                content: m.get(2),
                created_at: m.get(3),
                model: m.get(4),
                token_count: m.get(5),
            })
            .collect();

        conversations.push(OtelConversation {
            session_id,
            source_tool,
            model,
            started_at,
            ended_at,
            messages,
        });
    }

    let summary = ExportSummary {
        traces: conversations.len(),
        spans: conversations.iter().map(|c| 1 + c.messages.len()).sum(),
    };
    let doc = build_otlp_export(&conversations, include_content, env!("CARGO_PKG_VERSION"));
    let text = serde_json::to_string_pretty(&doc).context("serialize OTLP export")?;
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent).with_context(|| format!("create {}", parent.display()))?;
        }
    }
    fs::write(out, text).with_context(|| format!("write {}", out.display()))?;
    Ok(summary)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn msg(uuid: &str, role: &str, content: &str, at: &str, tokens: Option<i64>) -> OtelMessage {
        OtelMessage {
            uuid: Some(uuid.into()),
            role: role.into(),
            content: content.into(),
            created_at: Some(at.parse().unwrap()),
            model: Some("m-test".into()),
            token_count: tokens,
        }
    }

    fn conv(
        session: &str,
        started: &str,
        ended: Option<&str>,
        messages: Vec<OtelMessage>,
    ) -> OtelConversation {
        OtelConversation {
            session_id: session.into(),
            source_tool: Some("claude_code".into()),
            model: Some("m-test".into()),
            started_at: started.parse().unwrap(),
            ended_at: ended.map(|e| e.parse().unwrap()),
            messages,
        }
    }

    fn sample() -> Vec<OtelConversation> {
        vec![conv(
            "d0000000-0000-4000-8000-000000000001",
            "2026-01-02T00:00:00Z",
            Some("2026-01-02T00:10:00Z"),
            vec![
                msg(
                    "e0000000-0000-4000-8000-000000000001",
                    "user",
                    "hello",
                    "2026-01-02T00:01:00Z",
                    Some(3),
                ),
                msg(
                    "e0000000-0000-4000-8000-000000000002",
                    "assistant",
                    "world",
                    "2026-01-02T00:02:00Z",
                    Some(5),
                ),
            ],
        )]
    }

    #[test]
    fn ids_are_deterministic_hex_of_expected_length() {
        let s = "d0000000-0000-4000-8000-000000000001";
        let t1 = trace_id(s);
        let t2 = trace_id(s);
        assert_eq!(t1, t2, "traceId must be deterministic");
        assert_eq!(t1.len(), 32, "traceId must be 32 hex chars");
        assert!(t1.chars().all(|c| c.is_ascii_hexdigit()));

        let r1 = root_span_id(s);
        let r2 = root_span_id(s);
        assert_eq!(r1, r2);
        assert_eq!(r1.len(), 16);
        assert!(r1.chars().all(|c| c.is_ascii_hexdigit()));

        let m = "e0000000-0000-4000-8000-000000000001";
        let n1 = message_span_id(m);
        let n2 = message_span_id(m);
        assert_eq!(n1, n2);
        assert_eq!(n1.len(), 16);
        assert!(n1.chars().all(|c| c.is_ascii_hexdigit()));

        // Root and message ids derived from different inputs must differ.
        assert_ne!(r1, n1);
    }

    #[test]
    fn required_span_attributes_present() {
        let doc = build_otlp_export(&sample(), false, "0.1.0");
        let spans = &doc["resourceSpans"][0]["scopeSpans"][0]["spans"];
        assert_eq!(spans.as_array().unwrap().len(), 3);

        let root = &spans[0];
        let root_attrs: Vec<&str> = root["attributes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|a| a["key"].as_str().unwrap())
            .collect();
        assert!(root_attrs.contains(&"gen_ai.conversation.id"));
        assert!(root_attrs.contains(&"gen_ai.system"));
        assert!(root_attrs.contains(&"gen_ai.request.model"));

        let user = &spans[1];
        let user_attrs: Vec<&str> = user["attributes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|a| a["key"].as_str().unwrap())
            .collect();
        for key in [
            "gen_ai.system",
            "gen_ai.request.model",
            "gen_ai.conversation.id",
            "gen_ai.message.role",
            "gen_ai.usage.input_tokens",
        ] {
            assert!(user_attrs.contains(&key), "missing user attr {key}");
        }

        let asst = &spans[2];
        let asst_attrs: Vec<&str> = asst["attributes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|a| a["key"].as_str().unwrap())
            .collect();
        assert!(asst_attrs.contains(&"gen_ai.usage.output_tokens"));
        assert!(!asst_attrs.contains(&"gen_ai.usage.input_tokens"));
    }

    #[test]
    fn int_values_are_decimal_strings() {
        let doc = build_otlp_export(&sample(), false, "0.1.0");
        let user = &doc["resourceSpans"][0]["scopeSpans"][0]["spans"][1];
        let attrs = user["attributes"].as_array().unwrap();
        let tok = attrs
            .iter()
            .find(|a| a["key"] == "gen_ai.usage.input_tokens")
            .unwrap();
        assert_eq!(tok["value"]["intValue"], "3");
        assert!(tok["value"]["intValue"].is_string());
    }

    #[test]
    fn content_absent_without_flag_present_with_flag() {
        let without = build_otlp_export(&sample(), false, "0.1.0");
        let with = build_otlp_export(&sample(), true, "0.1.0");

        fn has_content(doc: &Value) -> bool {
            let spans = doc["resourceSpans"][0]["scopeSpans"][0]["spans"]
                .as_array()
                .unwrap();
            spans.iter().any(|s| {
                s["attributes"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|a| a["key"] == "gen_ai.message.content")
            })
        }

        assert!(
            !has_content(&without),
            "content must be absent without flag"
        );
        assert!(has_content(&with), "content must be present with flag");
        let text = serde_json::to_string(&with).unwrap();
        assert!(text.contains("hello"));
        let text_off = serde_json::to_string(&without).unwrap();
        assert!(!text_off.contains("hello"));
    }

    #[test]
    fn sort_is_stable_regardless_of_input_order() {
        let mut c1 = sample();
        c1.push(conv(
            "d0000000-0000-4000-8000-000000000002",
            "2026-01-01T00:00:00Z",
            Some("2026-01-01T00:05:00Z"),
            vec![msg(
                "e0000000-0000-4000-8000-000000000003",
                "user",
                "early",
                "2026-01-01T00:01:00Z",
                None,
            )],
        ));
        let mut c2 = c1.clone();
        c2.reverse();
        let a = build_otlp_export(&c1, false, "0.1.0");
        let b = build_otlp_export(&c2, false, "0.1.0");
        assert_eq!(
            serde_json::to_string(&a).unwrap(),
            serde_json::to_string(&b).unwrap(),
            "output must not depend on input order"
        );
        // Earlier conversation comes first in the spans array (its root is first).
        let spans = a["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .unwrap();
        assert!(spans[0]["name"].as_str().unwrap().contains("000000000002"));
    }

    #[test]
    fn otlp_nesting_and_nano_times() {
        let doc = build_otlp_export(&sample(), false, "0.1.0");
        assert!(doc.get("resourceSpans").is_some());
        let rs = &doc["resourceSpans"][0];
        assert!(rs.get("resource").is_some());
        assert!(rs.get("scopeSpans").is_some());
        let ss = &rs["scopeSpans"][0];
        assert_eq!(ss["scope"]["name"], "throughline");
        assert_eq!(ss["scope"]["version"], "0.1.0");
        assert!(ss.get("spans").is_some());

        // Nano times as decimal strings taken from the timestamps.
        let t0: DateTime<Utc> = "2026-01-02T00:00:00Z".parse().unwrap();
        let t1: DateTime<Utc> = "2026-01-02T00:10:00Z".parse().unwrap();
        let t2: DateTime<Utc> = "2026-01-02T00:01:00Z".parse().unwrap();
        let root = &ss["spans"][0];
        assert!(root["startTimeUnixNano"].is_string());
        assert!(root["endTimeUnixNano"].is_string());
        assert_eq!(root["startTimeUnixNano"], unix_nanos(t0));
        assert_eq!(root["endTimeUnixNano"], unix_nanos(t1));

        // Message span times from message timestamps (zero duration).
        let user = &ss["spans"][1];
        assert_eq!(user["startTimeUnixNano"], unix_nanos(t2));
        assert_eq!(user["endTimeUnixNano"], unix_nanos(t2));
    }

    #[test]
    fn span_names_follow_contract() {
        let doc = build_otlp_export(&sample(), false, "0.1.0");
        let spans = doc["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .unwrap();
        assert_eq!(
            spans[0]["name"],
            "conversation d0000000-0000-4000-8000-000000000001"
        );
        assert_eq!(spans[1]["name"], "user message");
        assert_eq!(spans[2]["name"], "assistant message");
    }

    #[test]
    fn parent_links_message_spans_to_root() {
        let doc = build_otlp_export(&sample(), false, "0.1.0");
        let spans = doc["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .unwrap();
        let root_id = spans[0]["spanId"].as_str().unwrap();
        assert!(spans[0].get("parentSpanId").is_none());
        for s in &spans[1..] {
            assert_eq!(s["parentSpanId"], root_id);
            assert_eq!(s["traceId"], spans[0]["traceId"]);
        }
    }

    #[test]
    fn resource_attributes_set() {
        let doc = build_otlp_export(&sample(), false, "0.1.0");
        let attrs = doc["resourceSpans"][0]["resource"]["attributes"]
            .as_array()
            .unwrap();
        let map: Vec<(&str, &str)> = attrs
            .iter()
            .map(|a| {
                (
                    a["key"].as_str().unwrap(),
                    a["value"]["stringValue"].as_str().unwrap(),
                )
            })
            .collect();
        assert!(map.contains(&("service.name", "throughline")));
        assert!(map.contains(&("service.version", "0.1.0")));
    }
}
