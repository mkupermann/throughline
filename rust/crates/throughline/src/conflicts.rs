//! Cross-tool conflict detection (read-only).

use crate::access::{push_visibility, Actor};
use anyhow::Result;
use postgres::types::ToSql;
use postgres::GenericClient;
use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct Conflict {
    pub kind: String,
    pub project_name: Option<String>,
    pub left_id: Option<i64>,
    pub left_content: String,
    pub right_id: Option<i64>,
    pub right_content: String,
    pub detail: String,
}

#[derive(Debug, Default, Serialize)]
pub struct ConflictReport {
    pub conflicts: Vec<Conflict>,
    pub checked: usize,
}

/// Markers that suggest a later chunk reverses an earlier one.
const CONTRADICTION_MARKERS: &[&str] = &[
    "instead",
    "no longer",
    "switched from",
    "actually",
    "rejected",
    "deprecated",
    "rolled back",
    "doch nicht",
    "nicht mehr",
    "stattdessen",
];

pub fn find_conflicts<C: GenericClient>(
    client: &mut C,
    actor: &Actor,
    project: Option<&str>,
    min_similarity: f32,
) -> Result<ConflictReport> {
    let mut report = ConflictReport::default();
    // Both sides of a pair must be notes the actor sees. Each query binds
    // the project as $1, then the filter of the older and the newer side.
    let pair = |old: &str, new: &str| -> (Vec<&(dyn ToSql + Sync)>, String) {
        let mut params: Vec<&(dyn ToSql + Sync)> = vec![&project];
        let seen_old = push_visibility(actor, old, &mut params);
        let seen_new = push_visibility(actor, new, &mut params);
        (params, format!("{seen_old} AND {seen_new}"))
    };

    // 1. Documented supersession (cross-tool when both sides map to different source tools).
    let (params, seen) = pair("o", "n");
    let rows = client.query(
        &format!(
            r#"
        SELECT o.id, o.content, o.project_name,
               n.id, n.content,
               COALESCE(co.source_tool, o.source_type, ''),
               COALESCE(cn.source_tool, n.source_type, '')
        FROM memory_chunks o
        JOIN memory_chunks n ON n.id = o.superseded_by
        LEFT JOIN conversations co ON co.id = o.source_id AND o.source_type = 'conversation'
        LEFT JOIN conversations cn ON cn.id = n.source_id AND n.source_type = 'conversation'
        WHERE o.superseded_by IS NOT NULL
          AND ($1::text IS NULL OR o.project_name = $1)
          AND COALESCE(co.source_tool, o.source_type, '') <> COALESCE(cn.source_tool, n.source_type, '')
          AND {seen}
        LIMIT 200
        "#
        ),
        &params,
    )?;
    for r in rows {
        report.conflicts.push(Conflict {
            kind: "documented_supersession".into(),
            project_name: r.get(2),
            left_id: Some(r.get(0)),
            left_content: r.get(1),
            right_id: Some(r.get(3)),
            right_content: r.get(4),
            detail: "superseded across tools".into(),
        });
        report.checked += 1;
    }

    // 2. Contradiction-marker pairs in the same project/category.
    let (params, seen) = pair("a", "b");
    let rows = client.query(
        &format!(
            r#"
        SELECT a.id, a.content, a.project_name,
               b.id, b.content
        FROM memory_chunks a
        JOIN memory_chunks b
          ON a.project_name = b.project_name
         AND a.category = b.category
         AND a.id < b.id
        WHERE a.status = 'active' AND b.status = 'active'
          AND ($1::text IS NULL OR a.project_name = $1)
          AND (
            lower(b.content) LIKE '%no longer%'
            OR lower(b.content) LIKE '%instead%'
            OR lower(b.content) LIKE '%switched from%'
            OR lower(b.content) LIKE '%rolled back%'
            OR lower(b.content) LIKE '%deprecated%'
            OR lower(b.content) LIKE '%stattdessen%'
            OR lower(b.content) LIKE '%nicht mehr%'
          )
          AND {seen}
        LIMIT 100
        "#
        ),
        &params,
    )?;
    for r in rows {
        let b: String = r.get(4);
        let has_marker = CONTRADICTION_MARKERS
            .iter()
            .any(|m| b.to_lowercase().contains(m));
        if !has_marker {
            continue;
        }
        report.conflicts.push(Conflict {
            kind: "semantic_contradiction_marker".into(),
            project_name: r.get(2),
            left_id: Some(r.get(0)),
            left_content: r.get(1),
            right_id: Some(r.get(3)),
            right_content: b,
            detail: format!("contradiction marker; similarity threshold {min_similarity}"),
        });
        report.checked += 1;
    }

    // 3. Stale-tool drift: old unused chunk vs newer chunk same project/category.
    let (params, seen) = pair("o", "n");
    let rows = client.query(
        &format!(
            r#"
        SELECT o.id, o.content, o.project_name,
               n.id, n.content
        FROM memory_chunks o
        JOIN memory_chunks n
          ON n.project_name = o.project_name
         AND n.category = o.category
         AND n.id <> o.id
        WHERE o.status = 'active' AND n.status = 'active'
          AND o.created_at < now() - interval '90 days'
          AND n.created_at > o.created_at
          AND COALESCE(o.access_count, 0) = 0
          AND ($1::text IS NULL OR o.project_name = $1)
          AND {seen}
        LIMIT 100
        "#
        ),
        &params,
    )?;
    for r in rows {
        report.conflicts.push(Conflict {
            kind: "stale_tool_drift".into(),
            project_name: r.get(2),
            left_id: Some(r.get(0)),
            left_content: r.get(1),
            right_id: Some(r.get(3)),
            right_content: r.get(4),
            detail: "older unused chunk alongside a newer one".into(),
        });
        report.checked += 1;
    }

    Ok(report)
}

#[cfg(test)]
mod tests {
    #[test]
    fn contradiction_markers_are_listed() {
        let markers = super::CONTRADICTION_MARKERS;
        assert!(markers.contains(&"no longer"));
        assert!(markers.contains(&"stattdessen"));
    }

    #[test]
    fn conflicts_hide() {
        use super::*;
        use crate::access::team_test::{assert_hidden, note, two_accounts};
        let Some(mut client) = crate::config::DbConfig::from_env().connect().ok() else {
            eprintln!("skipping conflicts_hide: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let (a, b) = two_accounts(&mut tx);
        let project = format!("cf-{}", uuid::Uuid::new_v4().simple());
        let secret = format!("secret {project}");
        let p = Some(project.as_str());
        // A shared decision, A's private reversal of it (a marker pair) and
        // a documented replacement across tools by the private note.
        let shared = note(
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
            &format!("no longer diesel, instead sqlx {secret}"),
            "decision",
            p,
        );
        tx.execute(
            "UPDATE memory_chunks SET superseded_by = $2, source_type = 'ui_pin' WHERE id = $1",
            &[&shared, &private],
        )
        .expect("chain");
        let mine = find_conflicts(&mut tx, &a, p, 0.85).expect("conflicts");
        assert!(
            mine.conflicts.iter().any(|c| c.right_id == Some(private)),
            "A sees the pair"
        );
        let theirs = find_conflicts(&mut tx, &b, p, 0.85).expect("conflicts");
        assert_hidden(
            &serde_json::to_string(&theirs).unwrap(),
            &secret,
            "the conflicts",
        );
        assert!(theirs
            .conflicts
            .iter()
            .all(|c| c.left_id != Some(private) && c.right_id != Some(private)));
    }
}
