//! R6 — agent accountability: what Throughline did, what agents wrote.

use anyhow::Result;
use postgres::GenericClient;
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct AuditEntry {
    pub kind: String,
    pub detail: String,
    pub count: i64,
    pub when: Option<chrono::DateTime<chrono::Utc>>,
}

#[derive(Debug, Serialize)]
pub struct AuditReport {
    pub since_days: i32,
    pub entries: Vec<AuditEntry>,
    pub mcp_writes: i64,
    pub reflections: i64,
    pub forgotten: i64,
    pub superseded: i64,
    pub notes: Vec<String>,
}

/// Summarise Throughline self-actions and agent (MCP) writes.
pub fn audit<C: GenericClient>(client: &mut C, since_days: i32) -> Result<AuditReport> {
    let mut notes = Vec::new();
    let since = format!("{since_days} days");

    let mcp_writes: i64 = client
        .query_one(
            "SELECT COUNT(*) FROM memory_chunks WHERE source_type = 'mcp_write' AND created_at > now() - ($1 || ' days')::interval",
            &[&since],
        )
        .map(|r| r.get(0))
        .unwrap_or(0);

    let forgotten: i64 = client
        .query_one(
            "SELECT COUNT(*) FROM memory_chunks WHERE status = 'forgotten' AND created_at > now() - ($1 || ' days')::interval",
            &[&since],
        )
        .map(|r| r.get(0))
        .unwrap_or(0);

    let superseded: i64 = client
        .query_one(
            "SELECT COUNT(*) FROM memory_chunks WHERE superseded_at IS NOT NULL AND superseded_at > now() - ($1 || ' days')::interval",
            &[&since],
        )
        .map(|r| r.get(0))
        .unwrap_or(0);

    let reflections: i64 = client
        .query_one(
            "SELECT COUNT(*) FROM memory_reflections WHERE created_at > now() - ($1 || ' days')::interval",
            &[&since],
        )
        .map(|r| r.get(0))
        .unwrap_or(0);

    let mut entries = Vec::new();

    // Reflection actions by type
    if let Ok(rows) = client.query(
        r#"
        SELECT reflection_type, action_taken, COUNT(*), MAX(created_at)
        FROM memory_reflections
        WHERE created_at > now() - ($1 || ' days')::interval
        GROUP BY 1, 2
        ORDER BY 3 DESC
        LIMIT 30
        "#,
        &[&since],
    ) {
        for r in rows {
            entries.push(AuditEntry {
                kind: format!("reflection/{}", r.get::<_, String>(0)),
                detail: r.get::<_, Option<String>>(1).unwrap_or_default(),
                count: r.get(2),
                when: r.get(3),
            });
        }
    }

    // MCP writes by project
    if let Ok(rows) = client.query(
        r#"
        SELECT COALESCE(project_name, '(none)'), COUNT(*), MAX(created_at)
        FROM memory_chunks
        WHERE source_type = 'mcp_write'
          AND created_at > now() - ($1 || ' days')::interval
        GROUP BY 1
        ORDER BY 2 DESC
        LIMIT 20
        "#,
        &[&since],
    ) {
        for r in rows {
            entries.push(AuditEntry {
                kind: "mcp_write".into(),
                detail: format!("project {}", r.get::<_, String>(0)),
                count: r.get(1),
                when: r.get(2),
            });
        }
    }

    // Ingest activity
    if let Ok(rows) = client.query(
        r#"
        SELECT COALESCE(c.source_tool, 'unknown'), COUNT(DISTINCT il.file_path),
               MAX(il.ingested_at)
        FROM ingestion_log il
        LEFT JOIN conversations c ON c.session_id::text = il.file_path
        WHERE il.ingested_at > now() - ($1 || ' days')::interval
        GROUP BY 1
        ORDER BY 2 DESC
        LIMIT 15
        "#,
        &[&since],
    ) {
        for r in rows {
            entries.push(AuditEntry {
                kind: "ingest".into(),
                detail: r.get::<_, String>(0),
                count: r.get(1),
                when: r.get(2),
            });
        }
    }

    // Generated-by tool: how many of our own model calls got stored
    if let Ok(rows) = client.query(
        r#"
        SELECT generated_by, COUNT(*), MAX(created_at)
        FROM conversations
        WHERE generated_by IS NOT NULL
          AND created_at > now() - ($1 || ' days')::interval
        GROUP BY 1
        ORDER BY 2 DESC
        LIMIT 10
        "#,
        &[&since],
    ) {
        for r in rows {
            entries.push(AuditEntry {
                kind: "self_referential".into(),
                detail: format!("generated_by={}", r.get::<_, String>(0)),
                count: r.get(1),
                when: r.get(2),
            });
        }
    }

    if mcp_writes == 0 && reflections == 0 {
        notes.push("no agent writes or reflections in this window".into());
    }
    notes.push(
        "audit is read-only: it reports history and never mutates memory".into(),
    );

    Ok(AuditReport {
        since_days,
        entries,
        mcp_writes,
        reflections,
        forgotten,
        superseded,
        notes,
    })
}

pub fn render_text(a: &AuditReport) -> String {
    let mut out = String::new();
    out.push_str(&format!(
        "audit ({}d): mcp_writes={} reflections={} forgotten={} superseded={}\n\n",
        a.since_days, a.mcp_writes, a.reflections, a.forgotten, a.superseded
    ));
    for e in &a.entries {
        let when = e
            .when
            .map(|t| t.format("%Y-%m-%d %H:%M").to_string())
            .unwrap_or_else(|| "-".into());
        out.push_str(&format!(
            "  [{when}] {} ×{} — {}\n",
            e.kind, e.count, e.detail
        ));
    }
    for n in &a.notes {
        out.push_str(&format!("note: {n}\n"));
    }
    out
}
