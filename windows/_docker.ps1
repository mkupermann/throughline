# Dot-sourced by the job scripts to decide whether Throughline is running
# under Docker Compose (docker-compose.yml — services throughline-web /
# throughline-postgres) or installed natively. The two need different
# commands: a Compose install already has its own environment and a Linux
# container's bash, so it runs `docker exec throughline-web throughline …`
# directly; a native install needs the shared env file and, for backup,
# a bash-free reimplementation (see throughline-backup.ps1).

function Test-DockerContainerRunning {
    param([Parameter(Mandatory)][string]$Name)
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $false }
    $running = docker ps --filter "name=^/$Name$" --filter "status=running" --format "{{.Names}}" 2>$null
    return [bool]$running
}
