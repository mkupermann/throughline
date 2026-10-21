//! Cost / token ledger (R3).

use anyhow::Result;
use postgres::GenericClient;
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct CostRow {
    pub source_tool: String,
    pub model: Option<String>,
    pub day: Option<String>,
    pub conversations: i64,
    pub token_in: i64,
    pub token_out: i64,
    pub cost_usd: f64,
}

#[derive(Debug, Serialize)]
pub struct CostReport {
    pub rows: Vec<CostRow>,
    pub total_token_in: i64,
    pub total_token_out: i64,
    pub total_cost_usd: f64,
    pub days: i32,
}

/// Aggregate tokens/cost per tool and model over the last `days` days.
pub fn cost_report<C: GenericClient>(client: &mut C, days: i32) -> Result<CostReport> {
    let rows = client.query(
        r#"
        SELECT COALESCE(source_tool, 'unknown') AS source_tool,
               model,
               COUNT(*) AS conversations,
               COALESCE(SUM(token_count_in), 0)::bigint AS token_in,
               COALESCE(SUM(token_count_out), 0)::bigint AS token_out,
               COALESCE(SUM(cost_usd), 0)::float8 AS cost_usd
        FROM conversations
        WHERE started_at > now() - ($1 || ' days')::interval
          AND generated_by IS NULL
        GROUP BY 1, 2
        ORDER BY COALESCE(SUM(token_count_in), 0) + COALESCE(SUM(token_count_out), 0) DESC
        LIMIT 100
        "#,
        &[&days.to_string()],
    )?;

    let mut out = Vec::new();
    let mut tin = 0i64;
    let mut tout = 0i64;
    let mut cost = 0f64;
    for r in rows {
        let row = CostRow {
            source_tool: r.get(0),
            model: r.get(1),
            day: None,
            conversations: r.get(2),
            token_in: r.get(3),
            token_out: r.get(4),
            cost_usd: r.get(5),
        };
        tin += row.token_in;
        tout += row.token_out;
        cost += row.cost_usd;
        out.push(row);
    }

    // Daily rollup for the chart/UI
    let days_rows = client.query(
        r#"
        SELECT date_trunc('day', started_at)::date::text AS day,
               COALESCE(SUM(token_count_in), 0)::bigint,
               COALESCE(SUM(token_count_out), 0)::bigint,
               COALESCE(SUM(cost_usd), 0)::float8
        FROM conversations
        WHERE started_at > now() - ($1 || ' days')::interval
          AND generated_by IS NULL
        GROUP BY 1
        ORDER BY 1 DESC
        LIMIT 60
        "#,
        &[&days.to_string()],
    )?;
    for r in days_rows {
        out.push(CostRow {
            source_tool: "(daily)".into(),
            model: None,
            day: r.get(0),
            conversations: 0,
            token_in: r.get(1),
            token_out: r.get(2),
            cost_usd: r.get(3),
        });
    }

    Ok(CostReport {
        rows: out,
        total_token_in: tin,
        total_token_out: tout,
        total_cost_usd: cost,
        days,
    })
}
