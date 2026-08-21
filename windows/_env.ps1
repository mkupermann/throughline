# Dot-sourced by every other script in this folder — never run directly.
#
# Task Scheduler has no equivalent of systemd's EnvironmentFile=, so each job
# script loads the same shared KEY=VALUE file itself before calling into
# Throughline. Mirrors systemd/throughline.env and the launchd plists' inline
# EnvironmentVariables dict — one shared file, same three keys.

$ErrorActionPreference = "Stop"

$EnvFile = Join-Path $env:USERPROFILE ".throughline\throughline.env"

if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        $parts = $line.Split("=", 2)
        if ($parts.Count -eq 2) {
            [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
        }
    }
} else {
    Write-Warning "No env file at $EnvFile — using PGDATABASE/PGHOST/PGPORT defaults (throughline/localhost/5432)."
}
