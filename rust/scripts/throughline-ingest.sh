#!/usr/bin/env bash
# Hourly ingest for the Rust Throughline CLI.
set -euo pipefail
ROOT="${THROUGHLINE_ROOT:-$HOME/src/throughline}"
BIN="${THROUGHLINE_BIN:-$ROOT/rust/target/release/throughline}"
export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-5433}"
export PGDATABASE="${PGDATABASE:-throughline}"
# PGUSER / PGPASSWORD come from the environment or ~/.pgpass
"$BIN" ingest
