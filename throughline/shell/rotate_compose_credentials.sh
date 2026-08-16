#!/bin/sh
# Rotate a legacy Compose-volume role password before migration-gated startup.

set -eu

: "${THROUGHLINE_LEGACY_DB_PASSWORD:?set the previous volume password}"
: "${POSTGRES_PASSWORD:?set the new Compose password}"

PSQL_BIN="${PSQL_BIN:-psql}"
export PGPASSWORD="$THROUGHLINE_LEGACY_DB_PASSWORD"
export PGUSER="${THROUGHLINE_LEGACY_DB_USER:-${PGUSER:-throughline}}"

# The password is passed over stdin, never in psql's argv. SQL string literals
# escape single quotes by doubling them.
escaped_password=$(printf '%s' "$POSTGRES_PASSWORD" | sed "s/'/''/g")
printf "ALTER ROLE CURRENT_USER PASSWORD '%s';\n" "$escaped_password" |
    "$PSQL_BIN" -v ON_ERROR_STOP=1
