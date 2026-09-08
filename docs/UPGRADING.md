# Upgrading an existing installation

Keep your existing database and source mounts. Do not run the demo seeder against a normal installation.

1. Make a PostgreSQL custom-format logical backup with `pg_dump -Fc`, using the database service and credentials from your existing configuration. Store it privately: it contains your conversations and may contain credentials.
2. Restore that backup into a separate PostgreSQL instance with the same major version and pgvector installed. Check the restore exit status and compare table counts. This checks recoverability without touching the live database.
3. Build the application image from the desired Git commit. The repository includes the built frontend; a Python-only image build uses those committed assets. Preserve your existing user IDs, environment, ports, source mounts and backup mounts.
4. Rehearse `throughline migrate` on the restored database using the new application version. Inspect `throughline migrate --status` and any migration-specific notes before changing the live installation.
5. Apply `throughline migrate` to the live database with the new application image, then recreate only the application service. Keep the database service and its volume in place. For a Compose deployment, use `docker compose up -d --no-deps <application-service>` with your actual service name.
6. Check `/api/health`, open Projects, open an existing conversation, and verify original message counts. Reload the browser so it loads the new asset bundle.
7. Update any launcher, pinned image ID or offline image archive to the same version. Otherwise the next restart may load the previous application again. Keep the old image and configuration for rollback.

Earlier releases include migrations `009_project_story.sql` (project checkpoints), `010_project_names.sql` (persistent labels), `011_generated_project_names.sql` (label origin and source IDs) and `012_ai_purposes.sql` (purpose-specific AI bindings). They do not rewrite original conversation messages. Reverting the application image does not revert migrations. If a future migration changes existing data, follow that migration’s recovery instructions and use the verified backup when necessary.

The global language selector retains an existing `pm-lang` browser preference. A fresh browser defaults to English. Use **EN / DE** in the sidebar to change it.

After upgrading, open **AI settings**, save the desired provider/model for every purpose and run the synthetic connection tests. Existing installations retain their older configuration for any purpose not yet saved. A saved binding takes precedence over those defaults. Containers using host CLIs also need a reachable host bridge and matching token; preserve that environment when updating a launcher or image.

## Shared-workspace migrations

Migrations 013–015 add accounts/audit, durable processing and explicit project assignments. Migration 015 converts the existing generated `conversations.project_name` column into a trigger-maintained effective key; original `project_path` and `source_project_name` remain available. The PostgreSQL 16 migration preserves the column, indexes and views. It does not rewrite original messages or provider key values. A manual assignment moves associated conversation memories to the effective project and retains earlier project notes with a moved-source warning.

Stop an active pass when upgrading from a pre-durable version: that old process cannot transfer its in-memory progress into the new queue. Committed records remain. After migration, new queued jobs and finished step checkpoints survive restarts. Before rolling back to an older image, stop new processing and restore the verified pre-upgrade backup if that image depends on the previous generated-column behavior. An image rollback alone does not undo assignments or schema changes.

Accounts are opt-in. Local mode remains the default after upgrading. Preserve existing `.env`, provider records, CLI bridge environment and source mounts. To enable team mode, create the first administrator and follow [TEAM_DEPLOYMENT.md](TEAM_DEPLOYMENT.md). Configure TLS before inviting colleagues.
