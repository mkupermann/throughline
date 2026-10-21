# Scheduled jobs for the Rust build

Register and manage the Throughline (Rust) Windows scheduled tasks.

```powershell
.\register-tasks.ps1
.\throughline-ingest.ps1    # run one job now, the same entry point Task Scheduler calls
.\unregister-tasks.ps1
```

On Linux the equivalents are the systemd units beside these scripts:
`throughline-ingest.service` with `throughline-ingest.timer`, and
`throughline-nightly.service` with `throughline-nightly.timer`.
