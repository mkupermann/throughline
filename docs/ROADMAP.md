# What is still missing

These are proposed extensions based on the current implementation, not shipped features or claims of market novelty. The priority is whether they help someone explain a project after four weeks.

| Priority | Capability | Concrete acceptance criterion |
|---|---|---|
| 1 | Guided execution readiness | After creating a project from a template, a first-time user assigns real resources, sees missing executor dependencies and reaches a verifiable first run without confusing saved configuration with execution. |
| 1 | Artifact lineage | A recorded file version links to its producing message, tool result and commit or content hash; later changes do not silently replace the historical artifact. |
| 2 | Provider budgets and retention | Set spending limits before remote processing; review estimates, repeated attempts and retention of diagnostics and audit history. |
| 2 | Evidence-change alerts | When a source is edited, removed or contradicted, affected claims are flagged with the exact changed evidence; the system never silently rewrites accepted project state. |
| 2 | Project-state comparison | Select two dates and see which decisions, blockers and results changed, with sources on both sides. |
| 2 | Explicit parallel-agent graph | Display parent, fork and merge relationships only when source IDs or a user-confirmed link establish them; unresolved imports remain visible. |
| 2 | Reproducible experiment records | Each result stores its hypothesis, dataset version, configuration, environment, run command and observed output. An independent rerun can compare results. |
| 3 | Context handoff with coverage checks | Before export, show which goal, constraints, open questions and evidence are included or omitted; record what the receiving session actually used when that tool exposes it. |
| 3 | Enterprise identity and confidential projects | Integrate SSO/MFA and project ACLs; validate tenant boundaries and recovery with an independent security review and the adopting organization. |

## The strongest opportunities

**Evidence-change alerts** would make old knowledge accountable: a conclusion would carry its dependence on specific evidence, and a changed source would create an explicit review task.

**Project-state comparison** would answer “What changed while I was away?” using source-backed differences rather than another free-form summary.

**Artifact lineage plus reproducible runs** would connect the conversation to the thing that was actually produced and tested. The current UI exposes explicit file references; it does not yet establish versioned artifact identity or successful execution.

## Already available foundations

The Operations library includes 103 versioned templates (43 projects, 15 teams and
45 roles), with category filters, previews and editable project/team/role creation.
Instances retain their original template snapshots. See [the catalog](TEMPLATES.md).
These plans do not create enforced execution stages, grant tools or replace the
separate agent executor. An end-to-end guided launch remains open.

Project history, source-linked state notes, prior state notes, explicit source relationships for supported imports, per-conversation memories, full transcripts, labelled output references and selected Markdown handoffs exist today. A complete processing pass, source-derived name suggestions and purpose-specific API/CLI selection are also available. Explicit project assignment, PostgreSQL-backed processing recovery and optional viewer/editor/admin accounts with audit history are now implemented for one shared corpus. They are foundations, not proof of tenant isolation or organizational acceptance.

Use the [implementation and data-model audit](PROJECT_STORY.md) for current limits. Acceptance should include a first-time-user task and a four-week resumption task; passing automated tests alone does not establish usability.
