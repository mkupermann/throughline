---
name: db-migration
description: Write reversible SQL migrations with a rollback section.
---

# Database Migration

Every migration must be reversible.

Include a rollback section with the down migration.

Use explicit column types, never rely on defaults.

Add indexes in the same migration that adds the column.

Test the migration against a copy of production data.

Never drop a column in the same release that stops writing to it.

Write the migration file in the project migrations directory.

Name the file with a timestamp and a short description.
