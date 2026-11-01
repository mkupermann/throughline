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

use crate::embedding::{self, EmbedBreaker};
use crate::generate_embeddings;
use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use postgres::types::ToSql;
use postgres::GenericClient;

/// SQL expression: the start of a new version of conversation `$1` (its
/// earliest message) when its messages were replaced after its last
/// extraction, else NULL. One expression, so the extraction can read it in
/// the same statement, and thus the same snapshot, as the lines it sends.
///
/// The signal is the extraction watermark, so no column is added. An
/// extraction leaves `memory_extracted_until` at the last message it read.
/// Appending (sessions, git commits) keeps those messages. Replacing deletes
/// them and writes the new version with later timestamps (the new mtime or
/// fetch time). A watermark with no message at or before it therefore means
/// a new version. A new version stamped no later than the watermark (a file
/// restored with an older mtime) is not even pending, so it is not seen.
pub(crate) const NEW_VERSION_AT: &str = "(SELECT min(nm.created_at) \
     FROM conversations nc JOIN messages nm ON nm.conversation_id = nc.id \
     WHERE nc.id = $1 AND nc.memory_extracted_until IS NOT NULL \
       AND NOT EXISTS (SELECT 1 FROM messages om \
                       WHERE om.conversation_id = nc.id \
                         AND om.created_at <= nc.memory_extracted_until))";

/// When the messages of `conversation_id` were replaced after its last
/// extraction: the start of the new version. See [`NEW_VERSION_AT`].
pub fn replaced_since_extraction<C: GenericClient>(
    client: &mut C,
    conversation_id: i64,
) -> Result<Option<DateTime<Utc>>> {
    let row = client
        .query_one(&format!("SELECT {NEW_VERSION_AT}"), &[&conversation_id])
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

/// How repeats of forgotten notes are recognised.
pub struct RepeatCheck<'a> {
    /// The embedding model whose vectors are compared.
    pub model: String,
    /// Cosine at or above which two notes say the same thing.
    pub min_similarity: f64,
    /// Guards the on-demand embedding of a missing vector, as at write time.
    pub breaker: &'a EmbedBreaker,
}

impl RepeatCheck<'static> {
    /// The active embedding model, its consolidation threshold and the
    /// process breaker. No request is made.
    pub fn active() -> Self {
        let model = embedding::configured_model();
        Self {
            min_similarity: consolidation_threshold(&model),
            model,
            breaker: &embedding::BREAKER,
        }
    }
}

/// The consolidation threshold of `model`, the same one `consolidate` uses;
/// the baseline for a model without a profile.
pub fn consolidation_threshold(model: &str) -> f64 {
    embedding::model_profile(model).map_or(crate::lifecycle::MIN_SIMILARITY, |p| {
        p.consolidation_min_similarity
    })
}

/// What [`drop_forgotten_repeats`] did with one batch of new notes.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct RepeatOutcome {
    /// New notes deleted as repeats of a forgotten note.
    pub dropped: u64,
    /// New notes kept after a check that could only compare text, because a
    /// vector was missing and could not be made.
    pub text_only: u64,
}

/// Delete those of `note_ids`, just written for `conversation_id`, that say
/// again what a forgotten note of the same conversation said: the same text
/// (ignoring surrounding whitespace), or a vector of `check.model` at or
/// above `check.min_similarity` cosine to the forgotten note's vector.
/// Consolidation leaves forgotten notes out, so nothing else would catch
/// the repeat.
///
/// A missing vector, on either side, is embedded on demand under the
/// breaker and the quick timeout, and stored. The `embed` job skips
/// forgotten notes, so after a model switch the forgotten ones have none.
/// When no vector can be made, a note is checked by text only and counted.
pub fn drop_forgotten_repeats<C: GenericClient>(
    client: &mut C,
    conversation_id: i64,
    note_ids: &[i64],
    check: &RepeatCheck<'_>,
) -> Result<RepeatOutcome> {
    if note_ids.is_empty() {
        return Ok(RepeatOutcome::default());
    }
    let forgotten: Vec<i64> = client
        .query(
            "SELECT id FROM memory_chunks \
             WHERE source_type = 'conversation' AND source_id = $1 \
               AND status = 'forgotten' AND id <> ALL($2) ORDER BY id",
            &[&conversation_id, &note_ids],
        )?
        .iter()
        .map(|r| r.get(0))
        .collect();
    if forgotten.is_empty() {
        return Ok(RepeatOutcome::default());
    }
    let mut repeats: Vec<i64> = client
        .query(
            "SELECT n.id FROM memory_chunks n \
             WHERE n.id = ANY($1) \
               AND EXISTS (SELECT 1 FROM memory_chunks f \
                           WHERE f.id = ANY($2) AND btrim(f.content) = btrim(n.content))",
            &[&note_ids, &forgotten],
        )
        .context("find repeats of forgotten notes by text")?
        .iter()
        .map(|r| r.get(0))
        .collect();
    let rest: Vec<i64> = note_ids
        .iter()
        .copied()
        .filter(|id| !repeats.contains(id))
        .collect();

    let mut text_only = 0;
    if !rest.is_empty() {
        // A core-only schema has no embeddings table; a failed statement
        // would abort the caller's transaction, so probe first (as writer.rs
        // does).
        let has_embeddings: bool = client
            .query_one("SELECT to_regclass('public.embeddings') IS NOT NULL", &[])?
            .get(0);
        if has_embeddings {
            let mut all_forgotten = true;
            for id in &forgotten {
                all_forgotten &= ensure_vector(client, *id, check)?;
            }
            let mut with_vector = Vec::new();
            for id in &rest {
                if ensure_vector(client, *id, check)? {
                    with_vector.push(*id);
                } else {
                    text_only += 1;
                }
            }
            let similar = similar_to_forgotten(client, &with_vector, &forgotten, check)?;
            if !all_forgotten {
                text_only += with_vector
                    .iter()
                    .filter(|id| !similar.contains(id))
                    .count() as u64;
            }
            repeats.extend(similar);
        } else {
            text_only = rest.len() as u64;
        }
    }

    let mut dropped = 0;
    if !repeats.is_empty() {
        client.execute(
            "DELETE FROM embeddings WHERE source_type = 'memory_chunk' AND source_id = ANY($1)",
            &[&repeats],
        )?;
        dropped = client
            .execute("DELETE FROM memory_chunks WHERE id = ANY($1)", &[&repeats])
            .context("delete repeats of forgotten notes")?;
    }
    Ok(RepeatOutcome { dropped, text_only })
}

/// Those of `ids` whose vector of `check.model` lies at or above the
/// threshold to the vector of one of `forgotten`.
fn similar_to_forgotten<C: GenericClient>(
    client: &mut C,
    ids: &[i64],
    forgotten: &[i64],
    check: &RepeatCheck<'_>,
) -> Result<Vec<i64>> {
    if ids.is_empty() {
        return Ok(Vec::new());
    }
    let params: [&(dyn ToSql + Sync); 4] = [&ids, &forgotten, &check.model, &check.min_similarity];
    Ok(client
        .query(
            "SELECT DISTINCT en.source_id FROM embeddings en JOIN embeddings ef \
               ON ef.model = en.model AND ef.source_type = 'memory_chunk' \
              AND ef.source_id = ANY($2) \
             WHERE en.source_type = 'memory_chunk' AND en.source_id = ANY($1) \
               AND en.model = $3 \
               AND GREATEST(1 - (en.embedding_768 <=> ef.embedding_768), \
                            1 - (en.embedding_1536 <=> ef.embedding_1536)) >= $4",
            &params,
        )
        .context("find repeats of forgotten notes by vector")?
        .iter()
        .map(|r| r.get(0))
        .collect())
}

/// Whether note `id` has a vector of `check.model`, making and storing one
/// when it has none. Embedding runs under `check.breaker` and the quick
/// timeout; a refused insert rolls back only its own savepoint.
fn ensure_vector<C: GenericClient>(
    client: &mut C,
    id: i64,
    check: &RepeatCheck<'_>,
) -> Result<bool> {
    let row = client.query_one(
        "SELECT left(COALESCE(mc.content, ''), 4000), EXISTS (\
             SELECT 1 FROM embeddings e \
             WHERE e.source_type = 'memory_chunk' AND e.source_id = mc.id AND e.model = $2 \
               AND (e.embedding_768 IS NOT NULL OR e.embedding_1536 IS NOT NULL)) \
         FROM memory_chunks mc WHERE mc.id = $1",
        &[&id, &check.model],
    )?;
    if row.get::<_, bool>(1) {
        return Ok(true);
    }
    let text: String = row.get(0);
    if text.trim().is_empty() {
        return Ok(false);
    }
    let Some(v) = generate_embeddings::note_vector(&text, check.breaker) else {
        return Ok(false);
    };
    if v.model != check.model {
        return Ok(false);
    }
    let mut savepoint = client.transaction()?;
    let stored = savepoint.execute(
        &format!(
            "INSERT INTO embeddings (source_type, source_id, model, {column}) \
             VALUES ('memory_chunk', $1, $2, $3::text::vector) \
             ON CONFLICT (source_type, source_id, model) DO UPDATE SET {column} = EXCLUDED.{column}",
            column = v.column
        ),
        &[&id, &v.model, &generate_embeddings::vector_literal(&v.vector)],
    );
    match stored {
        Ok(_) => {
            savepoint.commit()?;
            Ok(true)
        }
        Err(e) => {
            eprintln!("forgotten check: note {id}: {e}");
            Ok(false)
        }
    }
}
