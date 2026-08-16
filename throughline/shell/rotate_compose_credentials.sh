#!/bin/sh
# Rotate a legacy Compose-volume role password before migration-gated startup.

set -eu

: "${THROUGHLINE_LEGACY_DB_PASSWORD:?set the previous volume password}"
: "${POSTGRES_PASSWORD:?set the new Compose password}"

PSQL_BIN="${PSQL_BIN:-psql}"
configured_user="${POSTGRES_USER:-throughline}"
configured_database="${POSTGRES_DB:-throughline}"
legacy_user="${THROUGHLINE_LEGACY_DB_USER:-throughline}"
legacy_database="${THROUGHLINE_LEGACY_DB_NAME:-throughline}"

# The official Postgres image only creates the configured role/database on an
# empty volume. Renaming either one is a different, data-bearing migration;
# this helper rotates only the password and refuses a misleading partial run.
invalid_names=0
if [ "$configured_user" != "$legacy_user" ]; then
    echo "POSTGRES_USER is immutable for an existing volume; keep $legacy_user" >&2
    invalid_names=1
fi
if [ "$configured_database" != "$legacy_database" ]; then
    echo "POSTGRES_DB is immutable for an existing volume; keep $legacy_database" >&2
    invalid_names=1
fi
[ "$invalid_names" -eq 0 ] || exit 1

export PGPASSWORD="$THROUGHLINE_LEGACY_DB_PASSWORD"
export PGUSER="$legacy_user"
export PGDATABASE="$legacy_database"

# The password is passed over stdin, never in psql's argv. SQL string literals
# escape single quotes by doubling them.
escaped_password=$(printf '%s' "$POSTGRES_PASSWORD" | sed "s/'/''/g")
printf "ALTER ROLE CURRENT_USER PASSWORD '%s';\n" "$escaped_password" |
    "$PSQL_BIN" -v ON_ERROR_STOP=1
