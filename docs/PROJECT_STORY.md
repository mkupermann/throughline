# Project history with traceable evidence

Throughline now opens on a project library covering all imported history, including inactive projects.
Opening a project shows its recorded goal, current position, blocker and next step above a chronological session history.
This workflow uses the local database and requires no AI connection.

## What is included

- All-history project library, sorted by last session, with project-name search and tool filtering.
- Session history with newest/oldest ordering, folder and tool scope, automation visibility, bounded pagination and full-message text search.
- On-demand transcripts and extracted notes inside their originating sessions.
- Explicitly unreviewed labels on extracted notes; confidence never becomes a confirmation action.
- User-authored project-state entries attached to a source session or a specific message. Earlier entries remain available; the latest entry for each state field survives even when its date precedes the recent-history page.
- Original source excerpts retained with entries. The interface and handoff expose up to 4,000 characters per excerpt, identify source changes and report unavailable originals.
- Explicit Codex fork and Vibe parent-session references resolved only when a unique imported source matches. Missing and ambiguous targets are shown without guessing. Chronological adjacency never becomes a relationship.
- A selected-session Markdown handoff with exact preview, user-note history and saved evidence excerpts. It downloads locally and does not call an external model.
- Message database IDs preserved on refresh when the source UUID survives. Removed source messages are removed, and checkpoint references degrade to their saved excerpt and surviving session.
- Codex message models follow successive turn-context records; selected explicit source metadata is preserved on import.

## Data migration

Apply the packaged migrations before serving the new project history:

```bash
throughline migrate
throughline serve
```

The existing deployment's backup and rollout procedure still applies.
Migration `009_project_story.sql` adds `project_checkpoints`; it does not rewrite existing conversations or claim that extracted notes are verified.
The shared writer requires the partial unique message index established by migration 001, also represented in the current schema snapshot.

The checkpoint API appends entries. Its `recorded_by` value describes the existing local-user deployment; it is not authenticated team identity.
Deleting a source does not erase the checkpoint's saved excerpt. Retention and deletion workflows must account for these independently stored notes.

## Meaning of scope and provenance

Projects retain the existing folder-name grouping. A folder selector allows separation of same-name folders without changing the source path. This is a scope filter, not a permanent project reassignment or a new canonical project registry.
Project-state entries belong to the selected project/folder scope. Tool and search filters affect the displayed sessions; the recorded project state remains project-wide within that folder scope.

A session-level citation is weaker than a message-level citation and is labelled accordingly.
The state entries are user interpretations, not independent scientific verification.
Knowledge extraction time is labelled as extraction time rather than the original time of discovery.

Source UUID stability is an adapter contract. Messages without UUIDs are still replaced. Codex uses deterministic event-position UUIDs, so append-only refresh preserves identity; arbitrary insertion or reordering in an old rollout can change it. Older already-broken links cannot be reconstructed by this change.

## Acceptance scenario (fictional)

The Atlas demo studies document retrieval over 120 fictional documents:

1. A first session records the evaluation goal and fixed queries.
2. Initial reranking results look promising, explicitly pending a long-document check.
3. The long-document experiment overturns the initial conclusion because chunk boundaries cut off the evidence.
4. After four weeks, the project shows the retained keyword baseline, unresolved overlap question and next experiment, each linked to an original message.
5. The user opens an earlier state, compares the source, chooses sessions and inspects a local handoff before downloading it.

The demonstration contains synthetic conversations and example model names. It does not represent a benchmark result, endorsement or internal requirements of any AI provider.

## Verification

Regression coverage includes project/folder isolation, API source validation, SQL input handling, old-project discovery, tool filters, search without a term, pagination, source changes/deletion, stable message IDs across repeated import, preserved old goals, explicit relationship resolution and changing Codex models.
Frontend tests cover expansion, unreviewed notes, explicit source-backed authoring, selected-source export and keyboard-accessible navigation.
Browser checks cover the populated project, creating a state entry, source navigation, handoff preview and a narrow viewport.

## Remaining product work

This is a local project-history implementation, not a team deployment certification.
Authenticated authorship, access control and collaboration require a separate design.
Permanent project reassignment, a full agent graph, complete subagent import, artifact versioning and structured experiment records are not implemented here.
Existing import exclusions and source-time fallbacks remain visible limitations.
The handoff includes selected context, not a full transcript or artifact bundle; its local URLs require access to the originating instance.

The state-history disclosure currently shows the latest 100 entries; the latest value of each field is fetched independently. Older entries remain in the database, but a dedicated older-state browser is future work.

Memory is conversation context, not an independent timeline event. Timeline aggregates and day details exclude memory extraction. Project notes and their replacements link to source conversations instead of standalone memory pages.

## Conversation navigation and recorded outputs

Conversations has its own sidebar entry, grouped by project. Narrow panels retain navigation labels. Messages and memories are excluded from independent timeline events; source links still open individual messages within their conversations.

A collapsed conversation exposes labelled excerpts of its first prompt, last recorded answer and latest tool output or explicit file-reference count. These are not a synthetic prompt/answer pair. Expanded transcripts show original content, structured file references and source timestamps with seconds and timezone. Missing timestamps or outputs remain explicitly missing; file existence and successful creation are not inferred from a reference.

AI-team operations lives in the System group and links its associated project names back to conversation history. The two project record types remain distinct in storage. The interface explains this relationship; it does not implement persistent reassignment or a guided team-setup wizard.

## Conversation reading workspace

`/conversations` is a dedicated reader. Select a project, scan its compact conversation list, and read one selected conversation alongside it. Project state cards remain in Project history. The URL records project, conversation and filter scope; changing projects or filters resets the selected conversation.

The selected reader distinguishes first-prompt and last-answer excerpts from the original chronological transcript. Recorded output and file references remain inside that conversation. Tool output starts collapsed in previews and original messages. Expand it to inspect the recorded content; large output scrolls within a bounded area. Extracted notes are disclosed in the same reader; search matches link to their original message context. Timestamps include seconds and timezone, with explicit fallback text for absent or invalid values.

Verification covers project and conversation switching without stale transcript content, provider scope in deep links, exact timestamp markup and malformed source times. The existing story APIs and database schema are unchanged.
