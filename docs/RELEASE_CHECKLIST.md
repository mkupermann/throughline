# Release validation checklist

Use this checklist for a proposed release commit. Record evidence in its pull
request or release notes: commit SHA, environment, commands, outcomes and remaining
limitations. A checked box requires an observed result. This document adds manual
review steps; it does not claim that GitHub enforces them.

## Before a release tag

- [ ] Complete CI is green for the exact proposed commit. Feature branches need a
  pull request targeting `main` to trigger the current CI workflow.
- [ ] Version metadata and changelog match the proposed version; compatibility
  changes and operator actions are explicit.
- [ ] Production frontend assets match their source. The installed wheel contains
  the assets and migrations required outside the checkout.
- [ ] Fresh installation succeeds using the documented Docker path, with initialized
  credentials, migrations and loopback bindings. Review rendered Compose output
  without publishing credentials.
- [ ] Upgrade a disposable copy of the prior supported schema, apply migrations
  again to check idempotence, and verify existing data and template snapshots.
- [ ] Restore a backup into a disposable database and verify representative records.
  Do not assume that downgrading the application reverses database migrations.
- [ ] Exercise one core recovery journey: import, project context, source link,
  selection-preserving handoff and downloaded content.
- [ ] Exercise template search, preview, creation and reload. Confirm the linked
  resources persist, and an unavailable executor does not appear as a completed run.
- [ ] Review desktop/mobile layouts, both themes, keyboard access, loading and error
  states. Automated accessibility scans supplement these checks.
- [ ] Check local-mode and team-mode access boundaries, including non-admin denial
  on administrative APIs, expired/revoked sessions and shared-corpus visibility.
- [ ] Review public claims through a second vendor's model using the actual files,
  as required by [Contributing](../CONTRIBUTING.md#reviewing-user-facing-claims).
- [ ] Inspect screenshots, video frames, captions and logs for private content.
  All public demonstration assets must use fictional or publication-approved data.
- [ ] Check documentation links, quickstart commands, provider destinations and
  disclosed limitations against the release candidate.

## Publish and verify

- [ ] Confirm the intended commit is on reviewed `main`, then create and push only
  its annotated release tag. This triggers external publication.
- [ ] Verify the [Release workflow](../.github/workflows/release.yml) succeeds and
  GHCR contains the expected immutable image digest and both target architectures.
- [ ] Smoke-test the published image by digest with a disposable database. Source
  checkout tests do not prove that the published image works.
- [ ] Review generated registry tags against the resolved version. The workflow
  refuses commits without an exact version tag and assigns `latest` only to stable
  versions. Verify prereleases do not replace the stable `latest` tag.
- [ ] If publishing a GitHub Release, create it separately with install/upgrade
  notes, the verified digest, known limitations and links to validation evidence.
  The current workflow does not create GitHub Releases or publish a PyPI package.

## If validation fails

Stop publication and retain the failing evidence. Fix the candidate through review
and repeat the affected checks. Do not move an already published version tag to
hide a defective build. Document the affected version and publish a corrected
version when verified. Recovery of a real installation follows the operator's
verified backup and upgrade plan, not an assumed reverse migration.
