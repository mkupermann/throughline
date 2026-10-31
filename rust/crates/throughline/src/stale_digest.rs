//! Stale-knowledge digest — push what drifted instead of waiting for Review.

use crate::access::{push_visibility, Actor};
use anyhow::Result;
use postgres::types::ToSql;
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
pub fn build_digest<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    since_days: i32,
) -> Result<StaleDigest> {
    let mut items = Vec::new();
    let since = since_days.to_string();
    let mut params: Vec<&(dyn ToSql + Sync)> = vec![&since];
    let seen = push_visibility(actor, "mc", &mut params);

    // Expired
    let rows = client.query(
        &format!(
            r#"
        SELECT id, category::text, project_name, content, created_at
        FROM memory_chunks mc
        WHERE status = 'active'
          AND expires_at IS NOT NULL AND expires_at < now()
          AND created_at > now() - ($1 || ' days')::interval
          AND {seen}
        ORDER BY expires_at ASC
        LIMIT 25
        "#
        ),
        &params,
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
        &format!(
            r#"
        SELECT id, category::text, project_name, content, created_at
        FROM memory_chunks mc
        WHERE superseded_at IS NOT NULL
          AND superseded_at > now() - ($1 || ' days')::interval
          AND {seen}
        ORDER BY superseded_at DESC
        LIMIT 25
        "#
        ),
        &params,
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
    let mut old_params: Vec<&(dyn ToSql + Sync)> = Vec::new();
    let old_seen = push_visibility(actor, "mc", &mut old_params);
    let rows = client.query(
        &format!(
            r#"
        SELECT id, category::text, project_name, content, created_at,
               (EXTRACT(EPOCH FROM (now() - created_at)) / 86400)::float8 AS age_days
        FROM memory_chunks mc
        WHERE status = 'active'
          AND COALESCE(access_count, 0) = 0
          AND created_at < now() - interval '30 days'
          AND {old_seen}
        ORDER BY created_at ASC
        LIMIT 25
        "#
        ),
        &old_params,
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::access::team_test::{assert_hidden, note, two_accounts};

    #[test]
    fn stale_digest_hides() {
        let Some(mut client) = crate::config::DbConfig::from_env().connect().ok() else {
            eprintln!("skipping stale_digest_hides: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let (a, b) = two_accounts(&mut tx);
        let secret = format!("secret {}", uuid::Uuid::new_v4().simple());
        let owner = a.user_id;
        // One private note for each list: expired, replaced, never opened.
        let expired = note(
            &mut tx,
            owner,
            "private",
            &format!("expired {secret}"),
            "insight",
            None,
        );
        let replaced = note(
            &mut tx,
            owner,
            "private",
            &format!("replaced {secret}"),
            "insight",
            None,
        );
        let dormant = note(
            &mut tx,
            owner,
            "private",
            &format!("dormant {secret}"),
            "insight",
            None,
        );
        tx.execute(
            "UPDATE memory_chunks SET expires_at = now() - interval '1 hour' WHERE id = $1",
            &[&expired],
        )
        .expect("expire");
        tx.execute(
            "UPDATE memory_chunks SET superseded_at = now() WHERE id = $1",
            &[&replaced],
        )
        .expect("replace");
        tx.execute(
            "UPDATE memory_chunks SET created_at = '1990-01-01T00:00:00Z' WHERE id = $1",
            &[&dormant],
        )
        .expect("age");
        let mine = build_digest(&mut tx, &a, 14).expect("digest");
        for id in [expired, replaced, dormant] {
            assert!(mine.items.iter().any(|i| i.id == id), "A sees {id}");
        }
        let theirs = build_digest(&mut tx, &b, 14).expect("digest");
        assert_hidden(
            &serde_json::to_string(&theirs).unwrap(),
            &secret,
            "the stale digest",
        );
        assert_hidden(&render_text(&theirs), &secret, "the stale digest text");
    }
}
