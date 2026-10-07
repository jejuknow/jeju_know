# JEJUNO Railway 배포 핵심 설정

- Runtime: Dockerfile (Python 3.12 + FastAPI + Uvicorn)
- Persistent volume mount path: `/data`
- Required variables:
  - `JEJUNO_DATA_DIR=/data`
  - `SESSION_COOKIE_SECURE=1`
  - `SECRET_KEY=<긴 랜덤 문자열>`
- Public Networking: Generate Domain
- Healthcheck path: `/health`

## 현재 데이터 보존 방식
- Git에는 실제 운영 DB인 `data/jejuno.db`를 올리지 않습니다.
- 최초 배포용 복사본 `seed/initial_jejuno.db`만 Git에 포함합니다.
- Railway Volume의 `/data/jejuno.db`가 없을 때만 `start.sh`가 최초 1회 복사합니다.
- 이후 재배포에서는 `/data/jejuno.db`를 덮어쓰지 않습니다.
