//! Notes of a document across its versions.
//!
//! A Markdown file or a web page is one conversation whose messages are its
//! sections, written without uuids. A changed file therefore replaces all of
//! them (`writer::replace_messages`), and the next extraction reads the whole
//! new version. Without the steps below, facts from deleted text would stay
//! current, and a note the user forgot would come back as a new one.
//!
//! The rules are the ones agent memory files follow (`agent_memory.rs`):
//! the old version's notes are closed, not deleted, and a forgotten note
//! stays forgotten. Import revives nothing. The extraction job runs these
//! steps without an account, so local and team mode behave the same.

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use postgres::types::ToSql;
use postgres::GenericClient;

/// When the messages of `conversation_id` were replaced after its last
/// extraction: the start of the new version (its earliest message).
///
/// The signal is the extraction watermark, so no column is added. An
/// extraction leaves `memory_extracted_until` at the last message it read.
/// Appending (sessions, git commits) keeps those messages. Replacing deletes
/// them and writes the new version with later timestamps (the new mtime or
/// fetch time). A watermark with no message at or before it therefore means
/// a new version. A new version stamped no later than the watermark (a file
/// restored with an older mtime) is not even pending, so it is not seen.
pub fn replaced_since_extraction<C: GenericClient>(
    client: &mut C,
    conversation_id: i64,
) -> Result<Option<DateTime<Utc>>> {
    let row = client
        .query_one(
            "SELECT min(m.created_at) \
             FROM conversations c JOIN messages m ON m.conversation_id = c.id \
             WHERE c.id = $1 AND c.memory_extracted_until IS NOT NULL \
               AND NOT EXISTS (SELECT 1 FROM messages o \
                               WHERE o.conversation_id = c.id \
                                 AND o.created_at <= c.memory_extracted_until)",
            &[&conversation_id],
        )
        .context("look for a new document version")?;
    Ok(row.get(0))
}

/// Close the notes the old version of `conversation_id` produced: every
/// current note extracted from it with a source time before
/// `new_version_at` becomes `superseded`, without a successor, and its
/// validity ends at `new_version_at` (never before it began). Forgotten
/// notes stay forgotten. Notes written by hand or over MCP have no
/// conversation as their source and stay as they are. Returns how many
/// notes were closed.
pub fn reconcile<C: GenericClient>(
    client: &mut C,
    conversation_id: i64,
    new_version_at: DateTime<Utc>,
) -> Result<u64> {
    client
        .execute(
            "UPDATE memory_chunks \
                SET status = 'superseded', superseded_at = now(), updated_at = now(), \
                    valid_to = LEAST(valid_to, \
                                     GREATEST(COALESCE(valid_from, source_at, created_at), $2)) \
              WHERE source_type = 'conversation' AND source_id = $1 \
                AND COALESCE(status, 'active') = 'active' AND superseded_by IS NULL \
                AND COALESCE(source_at, valid_from, created_at) < $2",
            &[&conversation_id, &new_version_at],
        )
        .context("close the notes of the old document version")
}

/// Delete those of `note_ids`, just written for `conversation_id`, that say
/// again what a forgotten note of the same conversation said: the same text
/// (ignoring surrounding whitespace), or a vector of `model` at or above
/// `min_similarity` cosine to the forgotten note's vector. Consolidation
/// leaves forgotten notes out, so nothing else would catch the repeat.
/// Returns how many notes were deleted.
pub fn drop_forgotten_repeats<C: GenericClient>(
    client: &mut C,
    conversation_id: i64,
    note_ids: &[i64],
    model: &str,
    min_similarity: f64,
) -> Result<u64> {
    if note_ids.is_empty() {
        return Ok(0);
    }
    // A core-only schema has no embeddings table; a failed statement would
    // abort the caller's transaction, so probe first (as writer.rs does).
    let has_embeddings: bool = client
        .query_one("SELECT to_regclass('public.embeddings') IS NOT NULL", &[])?
        .get(0);
    let mut params: Vec<&(dyn ToSql + Sync)> = vec![&note_ids, &conversation_id];
    let by_vector = if has_embeddings {
        params.extend([&model as &(dyn ToSql + Sync), &min_similarity]);
        "OR EXISTS (SELECT 1 FROM embeddings en JOIN embeddings ef \
                      ON ef.model = en.model AND ef.source_type = 'memory_chunk' \
                     AND ef.source_id = f.id \
                   WHERE en.source_type = 'memory_chunk' AND en.source_id = n.id \
                     AND en.model = $3 \
                     AND GREATEST(1 - (en.embedding_768 <=> ef.embedding_768), \
                                  1 - (en.embedding_1536 <=> ef.embedding_1536)) >= $4)"
    } else {
        ""
    };
    let repeats: Vec<i64> = client
        .query(
            &format!(
                "SELECT n.id FROM memory_chunks n \
                 WHERE n.id = ANY($1) \
                   AND EXISTS (SELECT 1 FROM memory_chunks f \
                               WHERE f.source_type = 'conversation' AND f.source_id = $2 \
                                 AND f.status = 'forgotten' AND f.id <> n.id \
                                 AND (btrim(f.content) = btrim(n.content) {by_vector}))"
            ),
            &params,
        )
        .context("find repeats of forgotten notes")?
        .iter()
        .map(|r| r.get(0))
        .collect();
    if repeats.is_empty() {
        return Ok(0);
    }
    if has_embeddings {
        client.execute(
            "DELETE FROM embeddings WHERE source_type = 'memory_chunk' AND source_id = ANY($1)",
            &[&repeats],
        )?;
    }
    client
        .execute("DELETE FROM memory_chunks WHERE id = ANY($1)", &[&repeats])
        .context("delete repeats of forgotten notes")
}

/// The embedding model notes get their vector from when they are written,
/// and its consolidation threshold. No request is made.
pub fn repeat_check_model() -> (String, f64) {
    let model = crate::embedding::configured_model();
    let min_similarity = crate::embedding::model_profile(&model)
        .map_or(crate::lifecycle::MIN_SIMILARITY, |p| {
            p.consolidation_min_similarity
        });
    (model, min_similarity)
}
