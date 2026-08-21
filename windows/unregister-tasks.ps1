# Removes the three tasks register-tasks.ps1 creates. Leaves
# %USERPROFILE%\.throughline\throughline.env in place — it holds your DB
# config, not scheduler state.
$ErrorActionPreference = "SilentlyContinue"

foreach ($name in @("Throughline Ingest", "Throughline Extract", "Throughline Backup")) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host "Removed task: $name"
}
