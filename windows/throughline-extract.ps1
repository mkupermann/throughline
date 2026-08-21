# Scheduled: distil memory chunks from recent conversations. Mirrors
# systemd/throughline-extract.service and the launchd extract plist.
. (Join-Path $PSScriptRoot "_docker.ps1")

if (Test-DockerContainerRunning "throughline-web") {
    docker exec throughline-web throughline extract-memory
    exit $LASTEXITCODE
}

. (Join-Path $PSScriptRoot "_env.ps1")
throughline extract-memory
exit $LASTEXITCODE
