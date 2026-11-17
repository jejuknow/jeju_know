# SQLite 백업과 장애 복구

운영 DB는 `/data/jejuno.db`, 백업은 같은 영구 Volume의 `/data/backups`다. 별도 스케줄러 의존성 없이 앱 lifespan에서 매일 한국시간 03:00 백업한다. 재시작 시 누락된 회차를 보충한다. OS 파일 잠금과 잠금 안의 회차 재확인으로 여러 프로세스의 중복 실행을 방지한다. SQLite backup API로 WAL까지 포함한 일관된 스냅샷을 만들고, integrity_check와 places/users/news_posts 조회 후에만 완성 파일로 게시한다. 120초 제한을 넘긴 작업은 실패로 기록한다.

자동 백업 이름은 `jejuno-auto-YYYYMMDD-HHMMSS.db`, 수동은 `jejuno-manual-...db`이며 한국시간이다. 충돌 시 마이크로초 접미사를 사용한다. 최근 자동 백업 14개만 유지한다. 수동·migration 백업은 정리 대상이 아니다. 자동 백업이 36시간 이상 없거나 최근 백업/복구 검증 실패 시 Dashboard에 경고한다. 실패 원인은 유형만 로그에 남긴다.

슈퍼관리자만 `/admin/backups`에서 수동 생성, 임시 복구 검증, 다운로드를 할 수 있다. 다운로드에는 계정 정보와 해시 등이 포함되므로 개인 공개 폴더에 두지 않는다. 공개 URL로 백업을 제공하지 않는다. 복구 검증은 임시 디렉터리에 SQLite backup API로 복원한 뒤 integrity_check와 주요 테이블 개수를 원본 백업과 비교하고 임시 파일을 정리한다. 운영 DB에 쓰지 않는다.

CLI: `python sqlite_backups.py --database /data/jejuno.db manual` 또는 `verify`. 기존 `deploy/backup.sh`도 같은 API를 호출한다. Railway는 `start.sh`의 Uvicorn 앱으로 실행하며 별도 cron 서비스는 필요하지 않다. 앱이 멈춘 동안에는 자동 백업도 멈추므로 장애 장기화 시 Volume 및 백업 상태를 직접 확인한다.

## 실제 복구 — 반드시 운영자 승인 후

1. 쓰기 요청을 막고 **모든 앱 worker, 스케줄러 및 외부 쓰기 프로세스**를 중단한다. 운영자의 승인과 선택한 복구 시점을 기록한다.
2. 현재 DB와 같은 이름의 `-wal`, `-shm` 파일이 있다면 함께 별도 복구 전 보관 경로에 보존한다. DB를 열 수 있으면 위 CLI로 추가 수동 백업한다. 손상된 DB는 버리지 않는다.
3. 선택한 백업을 독립된 임시 경로로 복사하여 `PRAGMA integrity_check` = `ok`, 주요 테이블 및 장소·회원·ISSUE 개수를 확인한다. 최근 기록이 백업 시점 이후 유실될 수 있음을 운영자가 확인한다.
4. **운영자 승인 없이 이 단계를 실행하지 않는다.** 원래 DB/WAL/SHM을 보관한 후 검증한 독립 DB를 `/data/jejuno.db`에 배치한다. 이전 DB의 WAL/SHM을 새 DB 옆에 남겨두지 않는다. 소유권·쓰기 권한을 앱 사용자에 맞춘다. 복구용 CLI는 제공하지 않아 실수로 운영 DB를 덮어쓰지 않도록 한다.
5. 앱을 다시 시작한다. `/health`, 서버 로그, integrity_check, 장소·회원·ISSUE 개수를 확인한다. Home/FIND/NEW/ISSUE/MAP/BEST/상세/관리자에서 읽기 및 승인된 쓰기 점검을 한다.
6. 문제가 있으면 쓰기를 다시 중단하고 복구 전 보관본으로 되돌릴지 운영자가 결정한다. 검증 완료 전 보관본을 삭제하지 않는다.

## 보장 범위와 별도 보관

이 기능은 **DB 백업**이다. `/data/uploads` 이미지와 `SECRET_KEY`, 문의 암호화에 쓰는 `PII_ENCRYPTION_KEY` 등 Railway 환경변수는 포함하지 않는다. 운영자는 이미지와 키를 별도 암호화된 저장소에 안전하게 보관해야 한다. 동일 Volume 내 백업은 Volume 자체 분실·계정 침해까지 보호하지 않으므로 외부 보관/Volume 스냅샷 정책은 별도 결정이 필요하다. 미확정 법적 보유기간이나 자동 콘텐츠 삭제 정책은 추가하지 않는다.
