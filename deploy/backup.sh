#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${JEJUNO_DATA_DIR:-$BASE_DIR/data}"
DB="${JEJUNO_DATABASE:-$DATA_DIR/jejuno.db}"
BACKUP_DIR="${JEJUNO_BACKUP_DIR:-$DATA_DIR/backups}"
mkdir -p "$BACKUP_DIR"
if [ ! -f "$DB" ]; then
  echo "DB not found: $DB" >&2
  exit 1
fi
STAMP=$(date +%Y%m%d-%H%M%S)
cp "$DB" "$BACKUP_DIR/jejuno-$STAMP.db"
find "$BACKUP_DIR" -type f -name 'jejuno-*.db' -mtime +14 -delete
printf 'Backup created: %s\n' "$BACKUP_DIR/jejuno-$STAMP.db"
