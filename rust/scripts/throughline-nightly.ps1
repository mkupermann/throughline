# Scheduled: extract memory, embed messages, and backup.
# embed limit kept modest — OpenAI fallback 429s when Ollama is absent.
. (Join-Path $PSScriptRoot "_env.ps1")
& $ThroughlineBin extract-memory --limit 40
& $ThroughlineBin embed --limit 40
& $ThroughlineBin backup
exit 0
