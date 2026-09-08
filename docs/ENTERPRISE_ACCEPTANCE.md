# Shared-workspace acceptance plan

Target: a controlled internal deployment for one organization and one shared corpus, preserving the existing provider credentials. This is not a multi-tenant SaaS service or a claim of external certification.

| Area | Required evidence | Status |
| --- | --- | --- |
| Personal accounts and roles | Anonymous denial, viewer/editor/admin authorization, revocation, CSRF and secret-preservation tests with PostgreSQL | Passed PostgreSQL role, expiry, CSRF, revocation, secret-preservation and real CLI startup tests |
| Audit | Actor-attributed database mutations, no credential values, append-only behavior, bounded history UI | Actor tests passed, including password changes after transaction commit and processing stop by a different administrator; append-only audit enforced |
| Durable processing | Queue and completed steps survive web/worker termination; no concurrent worker duplicates; cancellation persists | Passed real worker recovery, ownership, cancellation and rotated-output tests |
| Project identity | Manual conversation assignment survives re-import; source path remains intact; navigation/search agree | Passed real import-writer correction/clear/conflict tests and browser correction workflow |
| Recovery | Migration from current installation, separate-database restore, retained conversations and secrets | Restored a private snapshot of the regular installation and applied migrations 013–015: original conversation/message counts, original message contents and provider-key values preserved; live database unchanged |
| User workflow | English/German login, account management, project correction and processing demonstrated in actual browser | Editor creation/assignment, administrator denial and account navigation verified in the browser; English/German and light/dark access screens checked; 16 fictional walkthroughs recorded and visually checked |
| Publication | English README and fictional walkthroughs, separate-model Roundtable and exact-commit CI | Published; [all nine CI jobs passed for the implementation commit](https://github.com/mkupermann/throughline/actions/runs/34227568399); 16 video previews verified on GitHub |
| Regular installation | Upgrade without changing source mounts, messages, provider secrets or purpose bindings | Candidate image passed real login/role/readiness checks; operator rollout awaits OS administrator authorization |

The [Roundtable record](ENTERPRISE_REVIEW.md) preserves verdicts, corrected defects and evidence-based disagreements. Do not mark this complete because a model says ENTHUSIASTIC. Actual organizational acceptance, identity-provider enrollment and an independent penetration test cannot be fabricated by the implementation agent.
