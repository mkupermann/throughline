# Upgrading an existing installation

Keep your existing database and source mounts. Do not run the demo seeder against a normal installation.

1. Make a PostgreSQL custom-format logical backup with `pg_dump -Fc`, using the database service and credentials from your existing configuration. Store it privately: it contains your conversations and may contain credentials.
2. Restore that backup into a separate PostgreSQL instance with the same major version and pgvector installed. Check the restore exit status and compare table counts. This checks recoverability without touching the live database.
3. Build the application image from the desired Git commit. The repository includes the built frontend; a Python-only image build uses those committed assets. Preserve your existing user IDs, environment, ports, source mounts and backup mounts.
4. Rehearse `throughline migrate` on the restored database using the new application version. Inspect `throughline migrate --status` and any migration-specific notes before changing the live installation.
5. Apply `throughline migrate` to the live database with the new application image, then recreate only the application service. Keep the database service and its volume in place. For a Compose deployment, use `docker compose up -d --no-deps <application-service>` with your actual service name.
6. Check `/api/health`, open Projects, open an existing conversation, and verify original message counts. Reload the browser so it loads the new asset bundle.
7. Update any launcher, pinned image ID or offline image archive to the same version. Otherwise the next restart may load the previous application again. Keep the old image and configuration for rollback.

The project-history release adds migration `009_project_story.sql`. It creates the project-checkpoint table and indexes; it does not rewrite existing conversations. Reverting the application image does not revert migrations. If a future migration changes existing data, follow that migration’s recovery instructions and use the verified backup when necessary.

The global language selector retains an existing `pm-lang` browser preference. A fresh browser defaults to English. Use **EN / DE** in the sidebar to change it.
