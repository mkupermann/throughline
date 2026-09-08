# Complete processing and AI settings

**Process everything** runs a full pass across configured sources and pending records. The button is available in Projects, Timeline and Operate. Opening a page never starts a job.

The pass imports conversations, scans skills and prompts, suggests project names, generates conversation titles, extracts knowledge and entities, runs reflection, creates embeddings, audits extraction and checks the installation. Titles and extraction do not use the individual buttons' small batch limits. Reflection produces review suggestions rather than automatically merging or confirming knowledge. Export remains separate because it requires an output destination.

Progress shows each step as pending, running, finished, blocked or failed. A failed step does not prevent independent later steps from running, and the overall pass stays unsuccessful if any step fails or is blocked. Valid empty extractions remain pending and are identified in the output. Existing jobs finish before the full pass starts; new overlapping individual jobs are rejected. Closing the browser does not stop processing. Stopping the pass stops its local subprocess group, with a forced stop after five seconds if needed. An already submitted remote request may continue at the provider; an active host CLI request expires within ten minutes. Server restarts and the 24-hour execution limit stop the pass; already committed database work remains. Start another pass to attempt remaining work. This is not a durable scheduler.

## Readable names with evidence

Imported folder identifiers are preserved as source metadata, not presented as meaningful project names. Until a name is available, the interface uses a recorded conversation excerpt. If no readable excerpt exists, it says so.

Suggested project labels use excerpts from up to eight recent conversations in the existing folder group. They retain their source conversation IDs, are visibly marked as AI suggestions and do not assert that all conversations belong to one real project. User-saved names take precedence and are never overwritten. Editing a suggested name records it as a user label. Renaming does not merge folders or reassign conversations.

Conversation title generation requests a structured final title. Known context envelopes, tool wrappers and thinking-only output are excluded. Previously stored `Thinking Process:` titles are eligible for repair.

## Choose a service for each purpose

Open **AI settings** in the sidebar. Choose a provider and model separately for answers, conversation titles, project names, knowledge and entities, reflection, and search embeddings. Manage API endpoints, keys and model lists through the linked provider page.

Supported generation routes are Ollama, OpenAI, Anthropic, Mistral, Google Gemini, OpenRouter and OpenAI-compatible APIs, plus the installed Codex, Vibe and Claude Code CLIs through the host bridge. Models must support the required operation and structured response shape; incompatible models fail explicitly. No API subscription, model entitlement or CLI login is supplied by Throughline.

A saved purpose selection never silently falls back to a different provider. An unsaved purpose retains the installation's existing configuration. The destination is displayed in AI settings. Stop an active complete pass before changing purpose selections or provider configuration. Connection tests require an explicitly saved selection and send only a synthetic prompt, not project content. Actual processing sends the relevant source excerpts to the selected service. Hosted APIs and hosted services reached through CLIs may process that content remotely.

API keys use the existing local provider store and are never returned by the settings API. This remains a local, single-user installation, not a multi-tenant secret vault. CLI credentials remain with the host CLI. For Vibe, the model field accepts an alias already configured in Vibe; the per-request override does not change its global default.

Embeddings require an Ollama, OpenAI, Mistral, OpenRouter or OpenAI-compatible embedding API, not a chat CLI. Anthropic and Gemini generation support does not imply an embedding adapter. Supported vector dimensions are 768 and 1536. Select a matching model and dimension; incorrect output dimensions are rejected. Ollama can use the installed `nomic-embed-text` model. Vectors are separated by model and endpoint. Changing either requires indexing pending content for that selection.

## Host CLI bridge

A container cannot automatically execute authenticated CLIs installed on its host. Run the optional packaged bridge on that host:

```bash
python -m throughline.jobs.cli_bridge
```

Set `THROUGHLINE_CLI_BRIDGE_TOKEN` to a locally generated secret. The bridge refuses to start without it. Its default bind address is `127.0.0.1` and its port is `11435`; `THROUGHLINE_CLI_BRIDGE_BIND` and `THROUGHLINE_CLI_BRIDGE_PORT` configure these. For a container, use a host interface reachable from that container and restrict access to the host/container network.

Set `THROUGHLINE_CLI_BRIDGE_URL` and the same `THROUGHLINE_CLI_BRIDGE_TOKEN` in the application environment. The bridge accepts only registered CLI names, prompts, models and optional schemas; it does not accept arbitrary commands or executable paths. Requests require the token. Each request runs in a temporary directory with tool execution disabled where the CLI supports it, a timeout and bounded concurrency. Installed does not mean authenticated: use **Test connection** to verify the selected CLI and model. CLI behavior can change between releases; the included adapters target the command options documented in their tests and implementation.

The bridge's own prompts have an explicit Throughline marker so future imports do not mistake them for human conversations.

## Protocol references

Codex configuration follows the [official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference). Native Anthropic responses use its [structured-output API](https://platform.claude.com/docs/en/build-with-claude/structured-outputs); Gemini uses its [structured-output API](https://ai.google.dev/gemini-api/docs/generate-content/structured-output). Adapter unit tests check request shapes and error handling; live account access is checked by the settings connection test.
