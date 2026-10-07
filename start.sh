#!/bin/sh
set -eu

mkdir -p /data

# Railway Volume이 처음 생성된 경우에만 초기 SQLite DB를 복사합니다.
# seed/initial_jejuno.db는 Git에 포함되는 '최초 배포용 복사본'입니다.
# 이후 재배포에서는 /data/jejuno.db가 이미 존재하므로 절대 덮어쓰지 않습니다.
if [ ! -f /data/jejuno.db ] && [ -f /app/seed/initial_jejuno.db ]; then
  echo "[JEJUNO] Initializing Railway volume with bundled SQLite database..."
  cp /app/seed/initial_jejuno.db /data/jejuno.db
fi

exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}"
