# Scheduled: pull AI coding sessions into Postgres.
. (Join-Path $PSScriptRoot "_env.ps1")
& $ThroughlineBin ingest
exit $LASTEXITCODE
