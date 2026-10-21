#!/usr/bin/env bash
# Nightly extract + embed + backup for the Rust Throughline CLI.
set -euo pipefail
ROOT="${THROUGHLINE_ROOT:-$HOME/src/throughline}"
BIN="${THROUGHLINE_BIN:-$ROOT/rust/target/release/throughline}"
export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-5433}"
export PGDATABASE="${PGDATABASE:-throughline}"
"$BIN" extract-memory --limit 40 || true
"$BIN" embed --limit 500 || true
"$BIN" backup || true
