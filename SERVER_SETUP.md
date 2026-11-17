# JEJUNO 서버 준비 기준

## 현재 앱 구조
- Python 3.11+ (권장 3.12)
- FastAPI + Uvicorn
- SQLite (`data/jejuno.db`)
- Node.js 사용 안 함

## 권장 VPS 사양
- OS: Ubuntu 24.04 LTS
- CPU: 1 vCPU 이상
- RAM: 1GB 최소 / 2GB 권장
- SSD: 10GB 이상
- 공개 IPv4 또는 외부 접속 가능한 주소
- 22(SSH), 80(HTTP), 443(HTTPS) 포트 사용 가능
- root 또는 sudo 권한
- 로컬 디스크가 재부팅/재배포 후에도 유지되는 영구 저장소

## 서버 구매 후 기본 순서
1. Ubuntu 업데이트
2. Docker Engine + Compose 설치
3. 프로젝트 업로드
4. `.env.server.example` -> `.env` 복사
5. SECRET_KEY 서버에서 생성
6. `docker compose up -d --build`
7. `http://서버IP:8000` 확인
8. 도메인 연결 후 Caddy/Nginx와 HTTPS 설정
9. `SESSION_COOKIE_SECURE=1`로 변경 후 재시작
10. 앱이 한국시간 03:00에 SQLite 자동 백업을 실행합니다. 별도 cron은 필요하지 않습니다. 수동 실행은 `deploy/backup.sh manual`, 복구 검증은 `deploy/backup.sh verify`를 사용합니다. [복구 절차](docs/backup-recovery.md)를 확인하세요.

## SQLite 보존
`docker-compose.yml`의 `./data:/data` 바인드 마운트 때문에 컨테이너를 다시 만들어도 DB는 서버의 `data/jejuno.db`에 남습니다.

## 주의
운영 Volume의 기존 DB를 로컬 DB나 seed 파일로 교체하지 마세요. 재배포는 기존 `/data/jejuno.db`를 유지합니다. 장애 복구가 필요한 경우 쓰기 중단·현재 DB 보존·백업 검증·운영자 승인 절차를 먼저 따릅니다.
