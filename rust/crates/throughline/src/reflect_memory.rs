//! Self-reflecting memory maintenance (dedup / contradictions / stale).

use crate::llm::{self, GenerationBackend};
use anyhow::Result;
use postgres::GenericClient;

#[derive(Debug, Default)]
pub struct ReflectSummary {
    pub mode: String,
    pub examined: usize,
    pub acted: usize,
    pub dry_run: bool,
}

/// Mark chunks expired / stale when their expiry has passed.
pub fn expire_stale<C: GenericClient>(client: &mut C, dry_run: bool) -> Result<ReflectSummary> {
    let mut summary = ReflectSummary {
        mode: "stale".into(),
        dry_run,
        ..Default::default()
    };
    if dry_run {
        let row = client.query_one(
            "SELECT COUNT(*) FROM memory_chunks WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at < now()",
            &[],
        )?;
        summary.examined = row.get::<_, i64>(0) as usize;
        return Ok(summary);
    }
    let n = client.execute(
        r#"
        UPDATE memory_chunks
        SET status = 'stale', updated_at = now()
        WHERE status = 'active'
          AND expires_at IS NOT NULL
          AND expires_at < now()
        "#,
        &[],
    )?;
    summary.examined = n as usize;
    summary.acted = n as usize;
    Ok(summary)
}

/// Collapse near-duplicate (category, project, content-prefix) groups.
pub fn dedup<C: GenericClient>(client: &mut C, dry_run: bool) -> Result<ReflectSummary> {
    let mut summary = ReflectSummary {
        mode: "dedup".into(),
        dry_run,
        ..Default::default()
    };
    let rows = client.query(
        r#"
        SELECT project_name, category, left(content, 80) AS head,
               array_agg(id ORDER BY id), array_agg(content ORDER BY id)
        FROM memory_chunks
        WHERE status = 'active'
        GROUP BY project_name, category, left(content, 80)
        HAVING COUNT(*) > 1
        LIMIT 100
        "#,
        &[],
    )?;
    for row in rows {
        let ids: Vec<i64> = row.get(3);
        let contents: Vec<String> = row.get(4);
        summary.examined += ids.len();
        if ids.len() < 2 {
            continue;
        }
        if dry_run {
            summary.acted += 1;
            continue;
        }
        let keep = ids[0];
        let merged = contents.join("\n---\n");
        client.execute(
            "UPDATE memory_chunks SET content = $2, merged_from = $3 WHERE id = $1",
            &[&keep, &merged, &ids],
        )?;
        if ids.len() > 1 {
            client.execute(
                "UPDATE memory_chunks SET status = 'superseded', superseded_by = $2, superseded_at = now() WHERE id = ANY($1) AND id <> $2",
                &[&ids, &keep],
            )?;
        }
        summary.acted += 1;
    }
    Ok(summary)
}

/// Prompt for the contradiction check — answer YES/NO only.
pub const CONTRADICTION_PROMPT: &str =
    "Does this memory reverse or contradict an earlier decision? \
Consider if it says the opposite of what was previously chosen. Answer YES or NO only.";

/// Flag possible contradictions via the generation backend (opt-in, limited).
pub fn contradictions<C: GenericClient>(
    client: &mut C,
    backend: &GenerationBackend,
    dry_run: bool,
    limit: usize,
) -> Result<ReflectSummary> {
    let mut summary = ReflectSummary {
        mode: "contradictions".into(),
        dry_run,
        ..Default::default()
    };
    let rows = client.query(
        r#"
        SELECT id, content, category FROM memory_chunks
        WHERE status = 'active' AND category IN ('decision','pattern','preference')
        ORDER BY created_at DESC
        LIMIT $1::bigint
        "#,
        &[&(limit as i64)],
    )?;
    summary.examined = rows.len();
    for row in rows {
        let id: i64 = row.get(0);
        let content: String = row.get(1);
        let prompt = format!("{CONTRADICTION_PROMPT}\n\n{content}");
        if let Ok(answer) = llm::complete(backend, None, &prompt) {
            if is_contradiction(&answer) {
                summary.acted += 1;
                if !dry_run {
                    client.execute(
                        "UPDATE memory_chunks SET confidence = GREATEST(confidence - 0.15, 0) WHERE id = $1",
                        &[&id],
                    )?;
                }
            }
        }
    }
    Ok(summary)
}

/// Check if a YES/NO answer indicates a contradiction.
pub fn is_contradiction(answer: &str) -> bool {
    let upper = answer.trim().to_uppercase();
    upper.starts_with("YES") || upper.starts_with("Y") && upper.len() <= 5
}

/// Run all maintenance modes.
pub fn run_all<C: GenericClient>(
    client: &mut C,
    backend: Option<&GenerationBackend>,
    dry_run: bool,
    limit: usize,
) -> Result<Vec<ReflectSummary>> {
    let mut out = vec![dedup(client, dry_run)?, expire_stale(client, dry_run)?];
    if let Some(b) = backend {
        out.push(contradictions(client, b, dry_run, limit)?);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn is_contradiction_detects_yes() {
        assert!(is_contradiction("YES"));
        assert!(is_contradiction("yes"));
        assert!(is_contradiction("Yes, it reverses the decision."));
        assert!(is_contradiction("  YES  "));
    }

    #[test]
    fn is_contradiction_rejects_no() {
        assert!(!is_contradiction("NO"));
        assert!(!is_contradiction("no"));
        assert!(!is_contradiction("No, this is consistent."));
        assert!(!is_contradiction(""));
    }

    #[test]
    fn is_contradiction_handles_short_y() {
        assert!(is_contradiction("Y"));
    }
}
