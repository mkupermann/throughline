//! Stale-knowledge digest — push what drifted instead of waiting for Review.

use anyhow::Result;
use postgres::GenericClient;
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct StaleItem {
    pub id: i64,
    pub category: String,
    pub project_name: Option<String>,
    pub content: String,
    pub reason: String,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub age_days: i64,
}

#[derive(Debug, Serialize)]
pub struct StaleDigest {
    pub generated_at: chrono::DateTime<chrono::Utc>,
    pub items: Vec<StaleItem>,
    pub expired: usize,
    pub superseded: usize,
    pub never_accessed: usize,
    pub banner: String,
}

/// Collect what went stale: expired, superseded, or long-dormant and unused.
pub fn build_digest<C: GenericClient>(client: &mut C, since_days: i32) -> Result<StaleDigest> {
    let mut items = Vec::new();

    // Expired
    let rows = client.query(
        r#"
        SELECT id, category::text, project_name, content, created_at
        FROM memory_chunks
        WHERE status = 'active'
          AND expires_at IS NOT NULL AND expires_at < now()
          AND created_at > now() - ($1 || ' days')::interval
        ORDER BY expires_at ASC
        LIMIT 25
        "#,
        &[&since_days.to_string()],
    )?;
    let mut expired = 0;
    for r in rows {
        expired += 1;
        items.push(StaleItem {
            id: r.get(0),
            category: r.get(1),
            project_name: r.get(2),
            content: r.get(3),
            reason: "expired".into(),
            created_at: r.get(4),
            age_days: 0,
        });
    }

    // Superseded recently
    let rows = client.query(
        r#"
        SELECT id, category::text, project_name, content, created_at
        FROM memory_chunks
        WHERE superseded_at IS NOT NULL
          AND superseded_at > now() - ($1 || ' days')::interval
        ORDER BY superseded_at DESC
        LIMIT 25
        "#,
        &[&since_days.to_string()],
    )?;
    let mut superseded = 0;
    for r in rows {
        superseded += 1;
        items.push(StaleItem {
            id: r.get(0),
            category: r.get(1),
            project_name: r.get(2),
            content: r.get(3),
            reason: "superseded".into(),
            created_at: r.get(4),
            age_days: 0,
        });
    }

    // Never accessed and older than 30 days
    let rows = client.query(
        r#"
        SELECT id, category::text, project_name, content, created_at,
               (EXTRACT(EPOCH FROM (now() - created_at)) / 86400)::float8 AS age_days
        FROM memory_chunks
        WHERE status = 'active'
          AND COALESCE(access_count, 0) = 0
          AND created_at < now() - interval '30 days'
        ORDER BY created_at ASC
        LIMIT 25
        "#,
        &[],
    )?;
    let mut never_accessed = 0;
    for r in rows {
        never_accessed += 1;
        items.push(StaleItem {
            id: r.get(0),
            category: r.get(1),
            project_name: r.get(2),
            content: r.get(3),
            reason: "never accessed".into(),
            created_at: r.get(4),
            age_days: {
                let d: f64 = r.get(5);
                d as i64
            },
        });
    }

    let total = items.len();
    let banner = if total == 0 {
        "Memory is fresh — nothing stale since the last session.".to_string()
    } else {
        format!(
            "{total} stale item(s): {expired} expired, {superseded} superseded, {never_accessed} never opened. Open Review to triage."
        )
    };

    Ok(StaleDigest {
        generated_at: chrono::Utc::now(),
        items,
        expired,
        superseded,
        never_accessed,
        banner,
    })
}

pub fn render_text(d: &StaleDigest) -> String {
    let mut out = String::new();
    out.push_str(&format!("stale-digest: {}\n\n", d.banner));
    for it in &d.items {
        out.push_str(&format!(
            "- [{} / {}] {} — {}\n",
            it.reason,
            it.category,
            it.content.chars().take(80).collect::<String>(),
            it.project_name.as_deref().unwrap_or("(none)")
        ));
    }
    out
}
