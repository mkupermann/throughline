<#
.SYNOPSIS
    Register Throughline (Rust) scheduled tasks in Windows Task Scheduler.

.DESCRIPTION
    Per-user tasks, no elevation required. Same cadence as the Python
    windows/ pack and the systemd units: hourly ingest, daily extract/embed/
    backup.

.EXAMPLE
    cd rust\scripts
    .\register-tasks.ps1
#>

[CmdletBinding()]
param(
    # Override if throughline.exe lives somewhere other than rust\target\release.
    [string]$Binary = "",
    # PostgreSQL connection for the tasks. Defaults to the compose port.
    [string]$PgHost = "127.0.0.1",
    [string]$PgPort = "5433",
    [string]$PgDatabase = "throughline",
    [string]$PgUser = ""
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot

if (-not $Binary) {
    $candidates = @(
        (Join-Path $here "..\target\release\throughline.exe"),
        (Join-Path $here "..\target\debug\throughline.exe"),
        (Join-Path $env:LOCALAPPDATA "throughline\throughline.exe")
    )
    $Binary = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $Binary -or -not (Test-Path $Binary)) {
    throw "throughline.exe not found. Build it first: cargo build --release (from rust\), or pass -Binary."
}
$Binary = (Resolve-Path $Binary).Path

if (-not $PgUser) {
    $PgUser = $env:PGUSER
    if (-not $PgUser) { $PgUser = "throughline" }
}

$envDir = Join-Path $env:USERPROFILE ".throughline"
$envFile = Join-Path $envDir "rust.env"
if (-not (Test-Path $envFile)) {
    New-Item -ItemType Directory -Force -Path $envDir | Out-Null
    $backupDir = Join-Path $env:LOCALAPPDATA "throughline\backups"
    $lines = @(
        "# Throughline Rust scheduled-task environment",
        "THROUGHLINE_BIN=$Binary",
        "PGHOST=$PgHost",
        "PGPORT=$PgPort",
        "PGDATABASE=$PgDatabase",
        "PGUSER=$PgUser",
        "# PGPASSWORD=replace-me",
        "# THROUGHLINE_BACKUP_DIR=$backupDir"
    )
    Set-Content -Path $envFile -Value $lines -Encoding UTF8
    Write-Host "Wrote $envFile - set PGPASSWORD there if your database needs it."
} else {
    Write-Host "Using existing $envFile"
}

if (-not (Get-Command pg_dump -ErrorAction SilentlyContinue)) {
    $shim = Join-Path $env:USERPROFILE ".throughline\bin\pg_dump.cmd"
    if (Test-Path $shim) {
        Write-Host "Using pg_dump shim at $shim (Dockerized Postgres)."
    } else {
        Write-Warning "pg_dump is not on PATH. The backup task needs it (PostgreSQL installer bin folder or ~/.throughline/bin/pg_dump.cmd)."
    }
}

$pwsh = (Get-Process -Id $PID).Path

function Register-OneTask {
    param(
        [string]$TaskName,
        [string]$ScriptPath,
        [Microsoft.Management.Infrastructure.CimInstance]$Trigger,
        [string]$Description
    )
    $action = New-ScheduledTaskAction -Execute $pwsh `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -DontStopOnIdleEnd `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $Trigger `
        -Settings $settings -Description $Description -Force | Out-Null
    Write-Host "Registered task: $TaskName"
}

$ingestTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-OneTask -TaskName "Throughline Rust Ingest" `
    -ScriptPath (Join-Path $here "throughline-ingest.ps1") `
    -Trigger $ingestTrigger `
    -Description "Throughline (Rust): import new AI coding sessions. Hourly."

$dailyAt = (Get-Date).Date.AddHours(2).AddMinutes(30)
if ($dailyAt -lt (Get-Date)) { $dailyAt = $dailyAt.AddDays(1) }
$extractTrigger = New-ScheduledTaskTrigger -Daily -At $dailyAt
Register-OneTask -TaskName "Throughline Rust Nightly" `
    -ScriptPath (Join-Path $here "throughline-nightly.ps1") `
    -Trigger $extractTrigger `
    -Description "Throughline (Rust): extract memory, embed, and backup. Daily."

Write-Host ""
Write-Host "Done. Inspect with: Get-ScheduledTask -TaskName 'Throughline Rust*'"
Write-Host "Run now via:        Start-ScheduledTask -TaskName 'Throughline Rust Ingest'"
