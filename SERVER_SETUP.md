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
10. `deploy/backup.sh`를 하루 1회 실행하도록 cron 등록

## SQLite 보존
`docker-compose.yml`의 `./data:/data` 바인드 마운트 때문에 컨테이너를 다시 만들어도 DB는 서버의 `data/jejuno.db`에 남습니다.

## 주의
현재 포함된 `data/jejuno.db`는 이 대화에서 생성된 프로토타입 DB입니다. 사용자의 PC에서 이미 관리자 CRUD로 데이터를 추가했다면, 서버 배포 전에 PC의 최신 `jejuno.db`로 이 파일을 교체해야 합니다.
