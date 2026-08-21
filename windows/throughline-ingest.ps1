# Scheduled: pull available local-tool sessions into Postgres. Mirrors
# systemd/throughline-ingest.service and the launchd ingest plist.
. (Join-Path $PSScriptRoot "_docker.ps1")

if (Test-DockerContainerRunning "throughline-web") {
    docker exec throughline-web throughline ingest --all
    exit $LASTEXITCODE
}

. (Join-Path $PSScriptRoot "_env.ps1")
throughline ingest --all
exit $LASTEXITCODE
