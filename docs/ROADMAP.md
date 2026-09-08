# What is still missing

These are proposed extensions based on the current implementation, not shipped features or claims of market novelty. The priority is whether they help someone explain a project after four weeks.

| Priority | Capability | Concrete acceptance criterion |
|---|---|---|
| 1 | Persistent project identity and corrected grouping | A user moves a wrongly grouped conversation; the correction survives re-import, and its source folder remains traceable. |
| 1 | Guided project setup | A first-time user starts with a goal, chooses whether agents are needed, and reaches the first useful action without understanding roles or token budgets. |
| 1 | Artifact lineage | A recorded file version links to its producing message, tool result and commit or content hash; later changes do not silently replace the historical artifact. |
| 2 | Durable processing and budget controls | An interrupted pass resumes from persisted step state after restart; a user can set a per-provider spending limit before remote processing. |
| 2 | Evidence-change alerts | When a source is edited, removed or contradicted, affected claims are flagged with the exact changed evidence; the system never silently rewrites accepted project state. |
| 2 | Project-state comparison | Select two dates and see which decisions, blockers and results changed, with sources on both sides. |
| 2 | Explicit parallel-agent graph | Display parent, fork and merge relationships only when source IDs or a user-confirmed link establish them; unresolved imports remain visible. |
| 2 | Reproducible experiment records | Each result stores its hypothesis, dataset version, configuration, environment, run command and observed output. An independent rerun can compare results. |
| 3 | Context handoff with coverage checks | Before export, show which goal, constraints, open questions and evidence are included or omitted; record what the receiving session actually used when that tool exposes it. |
| 3 | Multi-user workspaces | Authenticated access, scoped permissions, shared project identities and an audit trail are validated with multiple users before any team-ready claim. |

## The strongest opportunities

**Evidence-change alerts** would make old knowledge accountable: a conclusion would carry its dependence on specific evidence, and a changed source would create an explicit review task.

**Project-state comparison** would answer “What changed while I was away?” using source-backed differences rather than another free-form summary.

**Artifact lineage plus reproducible runs** would connect the conversation to the thing that was actually produced and tested. The current UI exposes explicit file references; it does not yet establish versioned artifact identity or successful execution.

## Already available foundations

Project history, source-linked state notes, prior state notes, explicit source relationships for supported imports, per-conversation memories, full transcripts, labelled output references and selected Markdown handoffs exist today. A complete processing pass, source-derived name suggestions and purpose-specific API/CLI selection are also available. They are the foundation for the proposals above, not proof that those proposals are complete.

Use the [implementation and data-model audit](PROJECT_STORY.md) for current limits. Acceptance should include a first-time-user task and a four-week resumption task; passing automated tests alone does not establish usability.
