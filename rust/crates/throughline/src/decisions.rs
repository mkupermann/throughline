//! R2 — decision continuity across tools.
//!
//! Lists active decisions/preferences with the tool that produced them and
//! any supersession chain, so a Claude session can see what Codex decided.

use crate::access::{push_visibility, Actor};
use anyhow::Result;
use postgres::types::ToSql;
use postgres::GenericClient;
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct DecisionRow {
    pub id: i64,
    pub category: String,
    pub content: String,
    pub project_name: Option<String>,
    pub source_tool: Option<String>,
    pub source_type: String,
    pub confidence: f32,
    pub status: String,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub superseded_by: Option<i64>,
    pub supersession_note: Option<String>,
    pub chain: Vec<ChainLink>,
}

#[derive(Debug, Serialize)]
pub struct ChainLink {
    pub id: i64,
    pub source_tool: Option<String>,
    pub content: String,
}

#[derive(Debug, Serialize)]
pub struct DecisionReport {
    pub rows: Vec<DecisionRow>,
    pub tools: Vec<String>,
}

/// Render a single chain link for text output.
fn render_chain_link(link: &ChainLink, is_current: bool, is_latest: bool) -> String {
    let marker = if is_current && is_latest {
        "→"
    } else if is_current {
        "·"
    } else if is_latest {
        "⇒"
    } else {
        " "
    };
    let tool = link.source_tool.as_deref().unwrap_or("?");
    let content: String = link.content.chars().take(120).collect();
    format!("  {marker} [{}] (id {}) {}", tool, link.id, content)
}

/// Build a human-readable supersession chain note.
fn chain_note(chain: &[ChainLink]) -> Option<String> {
    if chain.len() <= 1 {
        return None;
    }
    let hops = chain.len() - 1;
    let first_tool = chain[0].source_tool.as_deref().unwrap_or("?");
    let last_tool = chain[chain.len() - 1].source_tool.as_deref().unwrap_or("?");
    let tools: Vec<&str> = chain
        .iter()
        .map(|l| l.source_tool.as_deref().unwrap_or("?"))
        .collect();
    let tool_path = tools.join(" → ");
    Some(format!(
        "supersession chain ({hops} hops): {tool_path} (origin: {first_tool}, latest: {last_tool})"
    ))
}

/// Active (and recently superseded) decisions, optionally scoped to a project.
pub fn list_decisions<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    project: Option<&str>,
    include_superseded: bool,
    limit: usize,
) -> Result<DecisionReport> {
    let status_filter = if include_superseded {
        "ch.status IN ('active','superseded','stale')"
    } else {
        "ch.status = 'active'"
    };
    let limit64 = limit as i64;
    let mut params: Vec<&(dyn ToSql + Sync)> = vec![&project, &limit64];
    let seen = push_visibility(actor, "ch", &mut params);
    // A successor the actor does not see ends the chain.
    let seen_next = push_visibility(actor, "s", &mut params);
    let sql = format!(
        r#"
        SELECT ch.id,
               ch.category::text,
               ch.content,
               ch.project_name,
               COALESCE(c.source_tool, ch.source_type) AS source_tool,
               ch.source_type,
               COALESCE(ch.confidence, 0.5)::float8,
               ch.status::text,
               ch.created_at,
               (SELECT s.id FROM memory_chunks s WHERE s.id = ch.superseded_by AND {seen_next})
        FROM memory_chunks ch
        LEFT JOIN conversations c
          ON ch.source_type = 'conversation' AND c.id = ch.source_id
        WHERE {status_filter}
          AND ch.category IN ('decision','preference','pattern','project_context','workflow')
          AND ($1::text IS NULL OR ch.project_name = $1)
          AND {seen}
        ORDER BY ch.created_at DESC
        LIMIT $2::bigint
        "#
    );
    let rows = client.query(&sql, &params)?;

    let mut out = Vec::new();
    let mut tools = std::collections::BTreeSet::new();
    for r in rows {
        let id: i64 = r.get(0);
        let superseded_by: Option<i64> = r.get(9);
        let source_tool: Option<String> = r.get(4);
        if let Some(t) = &source_tool {
            tools.insert(t.clone());
        }

        // Walk the supersession chain (bounded).
        let mut chain = vec![ChainLink {
            id,
            source_tool: source_tool.clone(),
            content: {
                let c: String = r.get(2);
                c.chars().take(120).collect()
            },
        }];
        let mut next = superseded_by;
        let mut hops = 0;
        while let Some(nid) = next {
            if hops > 5 {
                break;
            }
            let mut step: Vec<&(dyn ToSql + Sync)> = vec![&nid];
            let seen = push_visibility(actor, "ch", &mut step);
            let Ok(parts) = client.query(
                &format!(
                    r#"
                SELECT ch.id, ch.content, COALESCE(c.source_tool, ch.source_type)
                FROM memory_chunks ch
                LEFT JOIN conversations c
                  ON ch.source_type = 'conversation' AND c.id = ch.source_id
                WHERE ch.id = $1 AND {seen}
                "#
                ),
                &step,
            ) else {
                break;
            };
            if let Some(p) = parts.into_iter().next() {
                chain.push(ChainLink {
                    id: p.get(0),
                    source_tool: p.get(2),
                    content: {
                        let c: String = p.get(1);
                        c.chars().take(120).collect()
                    },
                });
                next = client
                    .query_one(
                        "SELECT superseded_by FROM memory_chunks WHERE id = $1",
                        &[&nid],
                    )
                    .ok()
                    .and_then(|row| row.get(0));
                hops += 1;
            } else {
                break;
            }
        }
        let supersession_note = chain_note(&chain);

        out.push(DecisionRow {
            id,
            category: r.get(1),
            content: r.get(2),
            project_name: r.get(3),
            source_tool,
            source_type: r.get(5),
            confidence: {
                let c: f64 = r.get(6);
                c as f32
            },
            status: r.get(7),
            created_at: r.get(8),
            superseded_by,
            supersession_note,
            chain,
        });
    }

    Ok(DecisionReport {
        rows: out,
        tools: tools.into_iter().collect(),
    })
}

pub fn render_text(report: &DecisionReport) -> String {
    let mut out = String::new();
    out.push_str(&format!(
        "decisions: {} (tools: {})\n\n",
        report.rows.len(),
        report.tools.join(", ")
    ));
    for d in &report.rows {
        out.push_str(&format!(
            "- [{} | {}] {}\n",
            d.category,
            d.source_tool.as_deref().unwrap_or("?"),
            d.content.replace('\n', " ")
        ));
        if let Some(n) = &d.supersession_note {
            out.push_str(&format!("    {n}\n"));
        }
        // Render the chain when it has more than one link.
        if d.chain.len() > 1 {
            for (i, link) in d.chain.iter().enumerate() {
                let is_current = i == 0;
                let is_latest = i == d.chain.len() - 1;
                out.push_str(&format!(
                    "    {}\n",
                    render_chain_link(link, is_current, is_latest)
                ));
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_link(id: i64, tool: Option<&str>, content: &str) -> ChainLink {
        ChainLink {
            id,
            source_tool: tool.map(|t| t.to_string()),
            content: content.to_string(),
        }
    }

    #[test]
    fn chain_note_none_for_single_link() {
        let chain = vec![make_link(1, Some("claude"), "use sqlx")];
        assert!(chain_note(&chain).is_none());
    }

    #[test]
    fn chain_note_some_for_multi_link() {
        let chain = vec![
            make_link(1, Some("codex"), "use diesel"),
            make_link(2, Some("claude"), "use sqlx"),
        ];
        let note = chain_note(&chain).unwrap();
        assert!(note.contains("supersession chain"));
        assert!(note.contains("1 hops"));
        assert!(note.contains("codex"));
        assert!(note.contains("claude"));
    }

    #[test]
    fn chain_note_handles_missing_tools() {
        let chain = vec![
            make_link(1, None, "a"),
            make_link(2, None, "b"),
            make_link(3, None, "c"),
        ];
        let note = chain_note(&chain).unwrap();
        assert!(note.contains("2 hops"));
        assert!(note.contains("? → ? → ?"));
    }

    #[test]
    fn render_chain_link_markers() {
        let link = make_link(1, Some("claude"), "content");
        let current = render_chain_link(&link, true, true);
        assert!(current.contains('→'));
        let past = render_chain_link(&link, false, true);
        assert!(past.contains('⇒'));
    }

    #[test]
    fn render_text_shows_chain() {
        let report = DecisionReport {
            rows: vec![DecisionRow {
                id: 1,
                category: "decision".into(),
                content: "use sqlx".into(),
                project_name: None,
                source_tool: Some("claude".into()),
                source_type: "conversation".into(),
                confidence: 0.9,
                status: "active".into(),
                created_at: chrono::Utc::now(),
                superseded_by: Some(2),
                supersession_note: Some("supersession chain (1 hops): claude → codex".into()),
                chain: vec![
                    make_link(1, Some("claude"), "use sqlx"),
                    make_link(2, Some("codex"), "use sqlx instead"),
                ],
            }],
            tools: vec!["claude".into(), "codex".into()],
        };
        let text = render_text(&report);
        assert!(text.contains("decisions: 1"));
        assert!(text.contains("supersession chain"));
        assert!(text.contains("→"));
    }

    #[test]
    fn decisions_hide() {
        use crate::access::team_test::{assert_hidden, note, two_accounts};
        let Some(mut client) = crate::config::DbConfig::from_env().connect().ok() else {
            eprintln!("skipping decisions_hide: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let (a, b) = two_accounts(&mut tx);
        let project = format!("dec-{}", uuid::Uuid::new_v4().simple());
        let secret = format!("secret {project}");
        let p = Some(project.as_str());
        let old = note(
            &mut tx,
            None,
            "shared",
            &format!("use diesel {project}"),
            "decision",
            p,
        );
        let private = note(
            &mut tx,
            a.user_id,
            "private",
            &format!("use sqlx {secret}"),
            "decision",
            p,
        );
        tx.execute(
            "UPDATE memory_chunks SET status = 'superseded', superseded_by = $2 WHERE id = $1",
            &[&old, &private],
        )
        .expect("chain");
        let mine = list_decisions(&mut tx, &a, p, true, 50).expect("decisions");
        assert!(mine.rows.iter().any(|r| r.id == private));
        let row = mine.rows.iter().find(|r| r.id == old).expect("old row");
        assert_eq!(row.superseded_by, Some(private));
        let theirs = list_decisions(&mut tx, &b, p, true, 50).expect("decisions");
        assert_hidden(&render_text(&theirs), &secret, "the decision text");
        assert_hidden(
            &serde_json::to_string(&theirs).unwrap(),
            &secret,
            "the decision JSON",
        );
        assert!(theirs.rows.iter().all(|r| r.id != private));
        let row = theirs.rows.iter().find(|r| r.id == old).expect("old row");
        assert_eq!(
            row.superseded_by, None,
            "the chain ends before the private note"
        );
        assert_eq!(row.chain.len(), 1);
    }
}
