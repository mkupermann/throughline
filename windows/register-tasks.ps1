<#
.SYNOPSIS
    Register the three Throughline scheduled tasks (ingest, extract, backup)
    in Windows Task Scheduler. Windows equivalent of systemd/*.timer and the
    launchd/*.plist files — no scheduler integration shipped for Windows
    before this.

.DESCRIPTION
    Run once, interactively, from an ordinary (non-admin) PowerShell prompt —
    these are per-user tasks, matching the systemd --user / launchd
    per-user-agent model, so no elevation is needed.

.EXAMPLE
    cd windows
    .\register-tasks.ps1
#>

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
. (Join-Path $here "_docker.ps1")

$usingDocker = Test-DockerContainerRunning "throughline-web"

if ($usingDocker) {
    Write-Host "Detected a running 'throughline-web' container — the three tasks will use 'docker exec' and need no local PostgreSQL client or Python install."
} else {
    # 1) Shared env file, only if the person has not already made one. Only
    #    relevant to the native path — a Docker Compose install keeps its own
    #    environment inside the container.
    $envDir = Join-Path $env:USERPROFILE ".throughline"
    $envFile = Join-Path $envDir "throughline.env"
    if (-not (Test-Path $envFile)) {
        New-Item -ItemType Directory -Force -Path $envDir | Out-Null
        Copy-Item (Join-Path $here "throughline.env.example") $envFile
        Write-Host "Wrote a default config to $envFile — edit it if your database is not on localhost:5432."
    } else {
        Write-Host "Using existing config at $envFile"
    }

    # 2) Confirm `throughline` resolves before scheduling three tasks that
    #    would otherwise fail silently in the background every time they fire.
    if (-not (Get-Command throughline -ErrorAction SilentlyContinue)) {
        Write-Warning "'throughline' is not on PATH in this shell, and no 'throughline-web' Docker container is running. The scheduled tasks run under your normal user profile, so if this is a native install, fix PATH first (activate the venv it's installed in, or add it to your user PATH) — or start the Docker Compose stack, which these tasks will then use automatically."
    }
    if (-not (Get-Command pg_dump -ErrorAction SilentlyContinue)) {
        Write-Warning "'pg_dump' is not on PATH — the backup task needs it for a native install (it ships with the PostgreSQL installer, usually under ...\PostgreSQL\<version>\bin)."
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

# Hourly ingest — same cadence as the systemd timer and the launchd
# StartInterval=3600.
$ingestTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration ([TimeSpan]::MaxValue)
Register-OneTask -TaskName "Throughline Ingest" `
    -ScriptPath (Join-Path $here "throughline-ingest.ps1") `
    -Trigger $ingestTrigger `
    -Description "Throughline: import new sessions from every configured AI coding tool. Hourly."

# Daily extract at 02:00 — same time as the systemd timer.
$extractTrigger = New-ScheduledTaskTrigger -Daily -At "02:00"
Register-OneTask -TaskName "Throughline Extract" `
    -ScriptPath (Join-Path $here "throughline-extract.ps1") `
    -Trigger $extractTrigger `
    -Description "Throughline: extract memory chunks from recent conversations. Daily at 02:00."

# Daily backup at 03:00 — same time as the systemd timer.
$backupTrigger = New-ScheduledTaskTrigger -Daily -At "03:00"
Register-OneTask -TaskName "Throughline Backup" `
    -ScriptPath (Join-Path $here "throughline-backup.ps1") `
    -Trigger $backupTrigger `
    -Description "Throughline: pg_dump the database, with 30-day rotation. Daily at 03:00."

Write-Host ""
Write-Host "Done. Verify with:  Get-ScheduledTask -TaskName 'Throughline *' | Get-ScheduledTaskInfo"
Write-Host "Run one now with:   Start-ScheduledTask -TaskName 'Throughline Ingest'"
Write-Host "Remove all with:    .\unregister-tasks.ps1"
