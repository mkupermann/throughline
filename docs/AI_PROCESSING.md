# Complete processing and AI settings

**Process everything** runs a full pass across configured sources and pending records. The button is available in Projects, Timeline and Operate. Opening a page never starts a job.

The pass imports conversations, scans skills and prompts, suggests project names, generates conversation titles, extracts knowledge and entities, runs reflection, creates embeddings, audits extraction and checks the installation. Titles and extraction do not use the individual buttons' small batch limits. Reflection produces review suggestions rather than automatically merging or confirming knowledge. Export remains separate because it requires an output destination.

Progress shows each step as pending, running, finished, blocked or failed. A failed step does not prevent independent later steps from running, and the overall pass stays unsuccessful if any step fails or is blocked. The Operate extraction and entity jobs checkpoint successful source versions, including valid empty results. Invalid responses and failed transactions remain pending. Existing jobs finish before the full pass starts; new overlapping individual jobs are rejected. Processing requests, cancellation and finished step checkpoints are saved in PostgreSQL. One database lock permits one queue worker per database. Closing the browser or restarting the web process does not discard a pass. A replacement worker waits a short recovery grace period, then skips finished steps and retries an interrupted step. **At-least-once execution** means an interrupted remote request can be repeated and charged again. Stop terminates the local command tree; a remote request already accepted cannot be retracted. Each worker attempt has a 24-hour full-pass limit or a one-hour individual-job limit; recovery starts a new attempt. An active host CLI request has its own ten-minute limit.

The UI distinguishes queued work, recovery, failures and completion. Diagnostic output is a bounded tail: 500 lines of at most 8,192 characters each; unflushed output can be lost on a crash. It is not the processing checkpoint or a complete execution archive. Lifecycle transitions are audited separately. The UI exposes the latest 50 processing runs; older rows remain in PostgreSQL. There is no automatic retention policy or per-provider spending cap yet. Set provider-side limits before large hosted runs.

The web process checks the queue every five seconds. An operator can also run `python -m throughline.jobs.processing_worker` under a process supervisor using the same environment, source mounts and database. Multiple starters are safe because the database lock decides ownership. Workers exit when the queue is empty. Inspect server stderr and the queued run's error when work cannot start.

## Readable names with evidence

Current conversation groups derive from source working-folder names. Those identifiers remain source metadata. A display label names an existing group without changing its membership. Until a label is available, the interface uses a recorded conversation excerpt. If no readable excerpt exists, it says so.

Suggested display labels use excerpts from up to eight recent conversations in the existing folder group. They retain their source conversation IDs, are visibly marked as AI suggestions and do not assert that all conversations belong to one real project. User-saved names take precedence and are never overwritten. Editing a suggested name records it as a user label. Renaming does not merge folders or reassign conversations.

Conversation title generation requests a structured final title. Known context envelopes, tool wrappers and thinking-only output are excluded. Previously stored `Thinking Process:` titles are eligible for repair.

## Choose a service for each purpose

Open **AI settings** in the sidebar. Choose a provider and model separately for answers, conversation titles, project names, knowledge and entities, reflection, and search embeddings. Manage API endpoints, keys and model lists through the linked provider page.

Supported generation routes are Ollama, OpenAI, Anthropic, Mistral, Google Gemini, OpenRouter and OpenAI-compatible APIs, plus the installed Codex, Vibe and Claude Code CLIs through the host bridge. Models must support the required operation and structured response shape; incompatible models fail explicitly. No API subscription, model entitlement or CLI login is supplied by Throughline.

A saved purpose selection never silently falls back to a different provider. An unsaved purpose retains the installation's existing configuration. The destination is displayed in AI settings. Stop an active complete pass before changing purpose selections or provider configuration. Connection tests require an explicitly saved selection and send only a synthetic prompt, not project content. Actual processing sends the relevant source excerpts to the selected service. Hosted APIs and hosted services reached through CLIs may process that content remotely.

API keys use the existing local provider store and are never returned by the settings API. Team mode restricts provider configuration, processing, host-file exports and the SQL Console to administrators. Existing database keys remain plaintext, including in backups; this is not a secret vault. CLI credentials remain with the host CLI. For Vibe, the model field accepts an alias already configured in Vibe; the per-request override does not change its global default.

Embeddings require an Ollama, OpenAI, Mistral, OpenRouter or OpenAI-compatible embedding API, not a chat CLI. Anthropic and Gemini generation support does not imply an embedding adapter. Supported vector dimensions are 768 and 1536. Select a matching model and dimension; incorrect output dimensions are rejected. Ollama can use the installed `nomic-embed-text` model. Vectors are separated by model and endpoint. Changing either requires indexing pending content for that selection.

## Host CLI bridge

A container cannot automatically execute authenticated CLIs installed on its host. Run the optional packaged bridge on that host:

```bash
python -m throughline.jobs.cli_bridge
```

Set `THROUGHLINE_CLI_BRIDGE_TOKEN` to a locally generated secret. The bridge refuses to start without it. Its default bind address is `127.0.0.1` and its port is `11435`; `THROUGHLINE_CLI_BRIDGE_BIND` and `THROUGHLINE_CLI_BRIDGE_PORT` configure these. For a container, use a host interface reachable from that container and restrict access to the host/container network.

Set `THROUGHLINE_CLI_BRIDGE_URL` and the same `THROUGHLINE_CLI_BRIDGE_TOKEN` in the application environment. The bridge accepts only registered CLI names, prompts, models and optional schemas; it does not accept arbitrary commands or executable paths. Requests require the token. Each request runs in a temporary directory with tool execution disabled where the CLI supports it, a timeout and bounded concurrency. Installed does not mean authenticated: use **Test connection** to verify the selected CLI and model. Only one connection test runs at a time on a settings page. A busy host bridge, mismatched bridge token, unavailable host, expired CLI login, rejected model, quota and timeout have separate messages. Errors expose a fixed diagnostic vocabulary, never raw CLI stderr or provider response bodies. CLI behavior can change between releases; the included adapters target the command options documented in their tests and implementation.

The bridge's own prompts have an explicit Throughline marker so future imports do not mistake them for human conversations.

## Protocol references

Codex configuration follows the [official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference). Native Anthropic responses use its [structured-output API](https://platform.claude.com/docs/en/build-with-claude/structured-outputs); Gemini uses its [structured-output API](https://ai.google.dev/gemini-api/docs/generate-content/structured-output). Adapter unit tests check request shapes and error handling; live account access is checked by the settings connection test.

Automatic recovery is limited to three interruptions per queued run. A further interruption marks that run failed and requires the operator to investigate before starting another pass. This bounds automatic retries, but is not a provider spending cap or an exactly-once guarantee. Worker startup failures are written to server stderr using a fixed message and exception class, without raw exception values.

The host bridge forwards basic runtime/network settings and the local session-bus/runtime location needed by OS keyrings and only the selected CLI's credential/configuration environment family (`CODEX_`/`OPENAI_`, `VIBE_`/`MISTRAL_`, or `CLAUDE_`/`ANTHROPIC_`/`AWS_`/`GOOGLE_`). Database passwords, the bridge token and unrelated environment secrets are not forwarded. Existing credentials stay in their original files and environment. Installed CLIs are trusted software and still read their own host login/configuration files. The first-party processing worker inherits the application environment because it needs database access and the configured providers; that environment is not stored in the queue or sent as a model prompt.


## Incremental processing and speed

**Process recent conversations** runs memory extraction followed by entity extraction, with a per-stage limit and an optional exact project scope.
It uses the already imported corpus; ingest new sessions first.
A conversation fingerprint includes ordered user/assistant content, roles and project assignment.
A successful checkpoint and its output commit in the same database transaction.
If the input changes during extraction, the transaction rolls back and the new source version remains pending.
An interrupted remote request can still be repeated, because a remote provider cannot participate in the database transaction.

The first run checkpoints older records, including already enriched history.
For a changed memory source, previous active chunks are retained as stale when replacement chunks are produced; they are not deleted. A valid empty result keeps existing chunks and records the completed source version.
Entity extraction retains historical graph evidence and adds or updates current findings.
Changing the selected model alone does not invalidate completed source versions.
The explicit legacy force-reextract CLI remains available for deliberate reprocessing.

Select one to four workers for an API-backed extraction model.
A CLI-backed model uses one worker, regardless of the requested setting, because the shared host bridge can be busy.
Each worker owns its database connection.
The selected provider receives the same source material as ordinary extraction; no automatic destination change or fallback is introduced.
Per-model mean time includes database and model work and is not a quality measure.
The connection test in AI settings reports elapsed time for a synthetic structured-response check, not a representative extraction benchmark.

**Embeddings first** starts the existing embedding job separately.
It uses the selected embedding provider and its model-specific namespace, so historical vectors from another destination may not count toward current coverage.
The default full pass still runs all stages in sequence; use the bounded controls for a faster first useful result.

## Continuing a project

The project continuation brief contains up to 20 recent source-linked knowledge records and 12 recent conversation excerpts, subject to a character budget (default 12,000; supported range 2,000–30,000).
Machine-generated conversations and other projects are excluded.
The UI exposes truncation and the original source links; it never executes text from the brief.
The MCP tool `continue_project` honors the existing project-scope resolver and requires a single project.
No model call, code inspection, agent launch or export to another provider occurs when a brief is built.
Download it locally, review it, then choose where to use it.
