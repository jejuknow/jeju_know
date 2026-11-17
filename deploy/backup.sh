#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${JEJUNO_DATA_DIR:-$BASE_DIR/data}"
DB="${JEJUNO_DATABASE:-$DATA_DIR/jejuno.db}"
exec "${PYTHON:-python3}" "$BASE_DIR/sqlite_backups.py" --database "$DB" "${1:-auto}"
