<#
.SYNOPSIS
    Remove the Throughline (Rust) scheduled tasks.
#>
$ErrorActionPreference = "Stop"
foreach ($name in @("Throughline Rust Ingest", "Throughline Rust Nightly")) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed task: $name"
    } else {
        Write-Host "Task not present: $name"
    }
}
