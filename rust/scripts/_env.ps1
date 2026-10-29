<#
.SYNOPSIS
    Load Throughline Rust task environment into the current session.
    Dot-source this file, then call $ThroughlineBin.
#>

$envFile = Join-Path $env:USERPROFILE ".throughline\rust.env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $pair = $line -split "=", 2
        if ($pair.Count -eq 2) {
            Set-Item -Path ("Env:" + $pair[0].Trim()) -Value $pair[1].Trim()
        }
    }
}

$script:ThroughlineBin = $env:THROUGHLINE_BIN
if (-not $script:ThroughlineBin) {
    $script:ThroughlineBin = Join-Path $PSScriptRoot "..\target\release\throughline.exe"
}
if (-not (Test-Path $script:ThroughlineBin)) {
    Write-Error "throughline.exe not found at $script:ThroughlineBin"
    exit 2
}

# Host pg_dump shim lives with the task env (Dockerized Postgres).
$shimDir = Join-Path $env:USERPROFILE ".throughline\bin"
if ((Test-Path $shimDir) -and ($env:PATH -notlike "*$shimDir*")) {
    $env:PATH = "$shimDir;$env:PATH"
}
