# Throughline design blueprint

## Product direction

Throughline is a workspace for recovering project context and coordinating AI teams. Its first screen should answer: what needs my attention, what is running, and what should happen next?

Implementation status: the Operations overview, persistent template library, versioned references, resource creation and editable project briefs are implemented. Runtime assignments use the existing resource editors. Template workflow/review prose is configuration guidance, not enforced execution; the existing launch action requires a separately installed compatible executor; the guided launch and enforced review flow below remain future work.

## Research basis

Reviewed 2026-09-12. No defensible universal ranking of the best GUI was found. Selection combines verified design recognition, documented product design decisions and fit for Throughline. Awards and vendor descriptions do not establish user-task performance here.

| Reference | Evidence | Pattern adopted for Throughline |
|---|---|---|
| [Things](https://culturedcode.com/things/blog/2017/06/back-from-wwdc/) | Apple Design Award recognition, 2017; [current product presentation](https://culturedcode.com/things/) | Clear task grouping and a focused next-action area. Adaptation: decisions requiring attention precede general activity. |
| [Linear design refresh](https://linear.app/now/behind-the-latest-design-refresh) | First-party account of consistent headers, quieter navigation and reduced visual competition | Stable page headers, restrained sidebar, predictable actions and compact operational rows. |
| [Linear UI redesign](https://linear.app/now/how-we-redesigned-the-linear-ui) | First-party design process for hierarchy and application navigation | One coherent shell across project, team and run views; consistent location and selection cues. |
| [Notion template marketplace](https://www.notion.com/templates/category/projects) | First-party project template library and composable project artifacts | Searchable templates, preview before use, customization of the resulting project. |
| [NN/g progressive disclosure](https://www.nngroup.com/articles/progressive-disclosure/) | Usability guidance on deferring secondary options | Show purpose, deliverables and readiness first; reveal model parameters, raw prompts and logs on demand. |

These are adapted interaction principles. Do not copy proprietary assets, branding or screen layouts verbatim. Newest award winners were considered through [Apple's awards directory](https://developer.apple.com/design/awards/); award recency alone is insufficient reason to borrow a pattern from an unrelated app category.

## Information architecture

Primary navigation: Projects, AI Team Operations, Search. Settings remains a utility destination. Preserve existing URLs or provide explicit redirects when navigation is reorganized.

AI Team Operations has three destinations:

1. Overview: needs attention, active runs, recent results. Every count derives from actual data. An empty system explains the next action rather than displaying fictional success metrics.
2. Templates: Project templates, Team templates, Role templates. These are mutually exclusive library filters with distinct counts and descriptions.
3. Resources: configured teams, members, models and connections. Existing catalogs remain the source of configured resources; templates do not silently replace them.

Project workspace: Overview, Team, Runs, Evidence. Keep recovery and exact source navigation connected to operations. The Overview shows objective, next action and current stage above supporting history.

## Template library anatomy

Header: breadcrumb, Templates heading, one-line explanation, primary Create template action. Below: type selector, search and optional category filter. Desktop uses a restrained two- or three-column grid; mobile uses one column. Search state survives preview and back navigation.

Each card contains name, intended outcome, type, version and two useful facts. Project facts: deliverables and stages. Team facts: role count and collaboration order. Role facts: responsibility and expected output. Avoid oversized illustrations, invented popularity ratings and decorative performance scores.

A preview shows what will be created, required inputs, linked template versions and editable defaults. Desktop may use a detail pane; mobile uses a dedicated page. Preview is read-only. The primary action explicitly names its result: Create project from template, Create team from template, or Add role from template.

## Domain rules

Project template: objective scaffold, required inputs, stages, deliverables, acceptance criteria, review gates and a team-template reference.

Team template: role-template references, execution order/dependencies, handoff contracts, independent review requirements, and optional execution limits.

Role template: responsibility, instructions, allowed tools, input contract, output contract and evaluation criteria. Role identity is independent of a model/provider. Runtime assignments select Codex, Claude, Vibe or an available local model separately.

Applying a template creates an editable instance with a recorded template ID and version. Editing a template must not mutate existing projects or runs. Explicit upgrades show a diff and preserve user overrides. Referenced templates cannot disappear silently: archive them or retain the referenced version.

## Start-project flow

Choose project template → enter brief → review team and assignments → check readiness → create project. Launch execution is a separate explicit action once a working executor is available.

Each stage preserves input when navigating back. A review summary shows deliverables, selected providers, data destinations, limits and review gates. Missing providers or pipeline dependencies produce actionable readiness messages. Do not show a successful run when only configuration has been saved.

For execution: distinguish queued, running, awaiting review, succeeded, failed and cancelled. State must come from runtime evidence. Failed runs expose the failed stage, retained output and a defined retry action. Cancellation acknowledgement differs from cancellation requested. Unknown cost stays unknown rather than displaying zero.

## Visual system

Canonical color, spacing, type, motion and elevation values remain in `web/src/styles/tokens.css`. Components reference tokens; no new hardcoded palette or external fonts. Any new token must be documented here and defined there before use.

Use the existing 28px title, 20px section title and 14–16px body scale. Reserve small text for secondary metadata, never core instructions. Use whitespace and typography for grouping; borders separate actual sections. Reserve the accent for primary actions, selection and focus. Use status colors only alongside readable status labels.

Navigation visually recedes behind the active task. Keep one principal action per page or dialog. Prefer compact rows for live runs and cards for browsing templates. Keep icons consistent and accompany unfamiliar actions with text. Motion follows existing duration tokens and respects reduced-motion preferences.

Mobile: preserve the existing full-width drawer shell, stack panes, provide comfortable touch targets and avoid horizontal task pipelines that require scrolling to discover required steps. Light and dark themes must have equivalent hierarchy and readable states.

## Acceptance gates for implementation

- A new user can distinguish a project, a template and an active run from labels and page context.
- Templates can be searched, previewed, instantiated and edited; saved instances survive reload. Empty, loading, permission and error states are explicit.
- Updating a template leaves existing instances unchanged; missing or archived references remain understandable.
- Role-to-provider assignments validate against actual available resources. A missing executor blocks launch with a recovery path.
- Keyboard users can traverse the entire flow, dismiss previews and retain focus. Search updates are announced without moving focus.
- Verify desktop and 390px mobile, both themes, long names, German labels, empty libraries and failed requests. No overflow or clipped primary actions.
- Existing evidence navigation and selection-preserving handoff regressions continue to pass.
- Browser tests verify persisted outcomes, not only clickability. Automated accessibility scans supplement keyboard and human review.
- Human usability evaluation measures finding a blocked run, selecting an appropriate template and creating a configured project. Record completion, errors and hesitation; agent ratings do not establish willingness to pay.

## Implementation sequence

1. Consolidate operations navigation around existing resources and add the template library with persistent versioned data.
2. Implement preview and instance creation, including input validation and reference integrity.
3. Connect project team assignments and executor readiness to the existing cockpit.
4. Implement and verify actual run lifecycle, review gates and recovery.
5. Refine visual density against screenshots and human task completion, retaining the current regression suite.

## Brand identity

The Throughline mark is one continuous, turning path: work retains its context across sessions and tools. The app component, favicon and SVG wordmarks share the same geometry. Use the existing `--accent` token (#0550ae light / #58a6ff dark), with `--text-primary` for lettering. No new brand palette is introduced. The mark has a 56-unit square viewBox, a 6-unit stroke and rounded terminals. Allow 8 units clear space; render at 16px or larger.

Assets and use guidelines: [brand identity](docs/brand/README.md).

## Continuation and processing controls

Project workspaces expose an on-demand continuation panel with a plain-text, source-linked preview and Markdown download.
Operate places bounded project processing before the full pipeline and groups data reconciliation and measured model timings in expandable sections.
Use existing surface, border, text and accent tokens, responsive wrapping and named form controls.
Neither elapsed-time indicators nor successful connection tests imply model quality or an assured speedup.
