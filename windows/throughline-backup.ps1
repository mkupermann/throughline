<#
.SYNOPSIS
    Daily pg_dump of the Throughline database, with rotation.

.DESCRIPTION
    Under Docker Compose, `throughline backup` already works as-is — the
    web container is Linux and has bash, and `docker-compose.yml` gives it
    its own named volume (`backup_data`) to write into. This script just
    calls it there.

    A native Windows install has no such container to shell into, and the
    CLI's own `backup` subcommand (`throughline/cli.py:cmd_backup`) shells
    out to `bash scripts/backup.sh`, which a native install has no reason to
    have on PATH. For that case only, this script reimplements the same
    dump/verify/rotate sequence natively in PowerShell, using
    `pg_dump -Fc` (already compressed) instead of the `pg_dump | gzip`
    pipeline the shell version uses.
#>

. (Join-Path $PSScriptRoot "_docker.ps1")

if (Test-DockerContainerRunning "throughline-web") {
    docker exec throughline-web throughline backup
    exit $LASTEXITCODE
}

. (Join-Path $PSScriptRoot "_env.ps1")

$ErrorActionPreference = "Stop"

$BackupDir = if ($env:CLAUDE_MEMORY_BACKUP_DIR) {
    $env:CLAUDE_MEMORY_BACKUP_DIR
} else {
    Join-Path $env:LOCALAPPDATA "throughline\backups"
}
$RetentionDays = 30
$DbName = if ($env:PGDATABASE) { $env:PGDATABASE } else { "throughline" }
$DbUser = if ($env:PGUSER) { $env:PGUSER } else { $env:USERNAME }
# Minimum plausible size — a real dump of this database is several MB;
# anything smaller means the dump aborted, whatever the exit code claimed.
$MinBytes = if ($env:CLAUDE_MEMORY_BACKUP_MIN_BYTES) { [int]$env:CLAUDE_MEMORY_BACKUP_MIN_BYTES } else { 100000 }

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

$Timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$BackupFile = Join-Path $BackupDir "$DbName`_$Timestamp.dump"

function Fail([string]$Reason) {
    Write-Error "[$(Get-Date)] FEHLER: $Reason — unvollstaendige Datei wird entfernt"
    Remove-Item -Force -ErrorAction SilentlyContinue $BackupFile
    exit 1
}

Write-Host "[$(Get-Date)] Backup startet -> $BackupFile"

$pgDumpArgs = @("-U", $DbUser, "-d", $DbName, "-Fc", "-f", $BackupFile)
& pg_dump @pgDumpArgs
if ($LASTEXITCODE -ne 0) { Fail "pg_dump fehlgeschlagen (exit $LASTEXITCODE)" }

# Three independent checks, because a backup you cannot restore is not a backup.
if (-not (Test-Path $BackupFile)) { Fail "keine Datei geschrieben" }
$bytes = (Get-Item $BackupFile).Length
if ($bytes -lt $MinBytes) { Fail "Backup ist nur $bytes Bytes (< $MinBytes)" }

$listing = & pg_restore --list $BackupFile 2>$null
if (-not $listing -or $listing.Count -eq 0) { Fail "Backup enthaelt keine Eintraege (pg_restore --list ist leer)" }

Write-Host "[$(Get-Date)] Backup fertig: $bytes Bytes, $($listing.Count) Eintraege, verifiziert"

# Rotation: delete anything older than RetentionDays, but never the last one.
$all = Get-ChildItem -Path $BackupDir -Filter "$DbName`_*.dump"
if ($all.Count -gt 1) {
    $cutoff = (Get-Date).AddDays(-$RetentionDays)
    $all | Where-Object { $_.LastWriteTime -lt $cutoff } | Remove-Item -Force
}

Write-Host "[$(Get-Date)] Verfuegbare Backups:"
Get-ChildItem -Path $BackupDir -Filter "$DbName`_*.dump" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 5 Name, Length, LastWriteTime |
    Format-Table -AutoSize | Out-String | Write-Host
