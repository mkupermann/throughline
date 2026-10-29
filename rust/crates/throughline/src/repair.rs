//! One-time repair of message timestamps that were set to import time.

use anyhow::Result;

/// One row per conversation that has messages dated after `ended_at`.
pub const FIND_SQL: &str = "SELECT m.conversation_id, count(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id WHERE m.created_at > c.ended_at GROUP BY m.conversation_id";

/// Clamp message timestamps to the conversation's `ended_at`.
pub const FIX_MESSAGES_SQL: &str = "UPDATE messages m SET created_at = c.ended_at FROM conversations c WHERE c.id = m.conversation_id AND m.created_at > c.ended_at";

/// Extend `ended_at` to the latest message timestamp where it lags.
pub const FIX_ENDED_SQL: &str = "UPDATE conversations c SET ended_at = x.max_created FROM (SELECT conversation_id, max(created_at) AS max_created FROM messages GROUP BY conversation_id) x WHERE x.conversation_id = c.id AND c.ended_at < x.max_created";

/// Summary of a repair run.
pub struct RepairSummary {
    pub conversations: usize,
    pub messages: usize,
}

/// Find (and optionally fix) messages dated after their conversation's `ended_at`.
pub fn repair_timestamps(client: &mut postgres::Client, apply: bool) -> Result<RepairSummary> {
    let rows = client.query(FIND_SQL, &[])?;
    let conversations = rows.len();
    let messages: usize = rows.iter().map(|r| r.get::<_, i64>(1) as usize).sum();

    if apply {
        let mut tx = client.transaction()?;
        tx.execute(FIX_MESSAGES_SQL, &[])?;
        tx.execute(FIX_ENDED_SQL, &[])?;
        tx.commit()?;
    }

    Ok(RepairSummary {
        conversations,
        messages,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn find_sql_targets_messages_after_ended() {
        assert!(FIND_SQL.contains("created_at > c.ended_at"));
    }

    #[test]
    fn fix_ended_sql_uses_max_created() {
        assert!(FIX_ENDED_SQL.contains("max(created_at)"));
    }
}
