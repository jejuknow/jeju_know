# JEJUNO 자체 회원 및 제주 이슈 게시

## DB / 배포

- `/data/jejuno.db`와 Railway Volume 경로는 그대로 사용한다. seed/시작 스크립트를 바꾸지 않는다.
- 시작 시 `20261008_local_users_issues` migration이 없을 때만 SQLite backup API로 `/data/backups/jejuno-before-user-auth-*.db`를 만들고 무결성을 검증한다. 실패하면 서버 시작과 migration을 중단한다.
- `users`, `auth_sessions`, `auth_rate_limits` 테이블과 `news_posts.author_id/author_type/deleted_at`만 추가한다. 공개 상태는 기존 `published` 컬럼을 계속 사용하며 중복된 `is_public`을 만들지 않는다.
- 기존 장소/관리자/뉴스의 원래 컬럼은 migration이 수정하지 않는다. 기존 뉴스는 `author_type=admin`, `author_id=NULL`로 읽힌다. 버전 기록으로 재배포 시 재실행하지 않는다.
- 배포 전 수동 운영 백업: `/data/backups/jejuno-before-user-auth-20261008-004710.db` (UTC).
- 롤백은 먼저 이전 애플리케이션 커밋만 재배포한다. 추가된 테이블/컬럼은 기존 코드와 호환된다. 신규 회원/게시물을 잃으므로 운영 DB를 백업 파일로 임의 교체하지 않는다.

## 인증 / 세션

- `argon2-cffi==25.1.0`, Argon2id: memory 19 MiB, time 2, parallelism 1, 독립적인 랜덤 salt. [OWASP 권장 최소 설정](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)을 사용한다. 해시 연산은 thread pool과 동시 실행 2개 제한으로 처리한다.
- 회원 비밀번호 12~128자. 이전 관리자 PBKDF2 해시는 로그인 시 검증한 뒤 Argon2id로 갱신한다. 기존 관리자 계정은 `admins`에서 유지한다. 신규 회원은 항상 role=user이며 폼에서 role/status를 받지 않는다.
- 기존 서명 쿠키를 서버 세션으로 전환하므로 **첫 배포 후 기존 관리자도 한 번 다시 로그인**한다. 관리자 아이디/비밀번호는 유지된다.
- 쿠키 `jejuno_session`에는 256-bit 랜덤 토큰만 저장한다. DB에는 토큰 SHA-256과 내부 ID, CSRF/안내 상태만 저장한다. 비밀번호/해시는 세션에 넣지 않는다.
- 운영에서는 Secure + HttpOnly + SameSite=Lax + Path=/, Domain 미설정. `SESSION_COOKIE_SECURE=0`은 더 이상 운영 보안을 낮추지 않는다. `create_app({"TESTING": True})`의 격리된 HTTP 테스트만 Secure를 해제한다.
- 로그인/비밀번호 변경 시 토큰 및 CSRF 회전. 절대 만료는 로그인 7일/익명 1시간. 로그아웃·비밀번호 변경·차단·탈퇴 시 서버 세션 삭제. 상태/권한은 요청마다 DB에서 재확인한다. 재배포에도 유효한 서버 세션은 유지된다.
- 로그인: IP/아이디 기준 15분 10회, 가입: IP 기준 1시간 5회, 비밀번호 확인: 15분 5회, 게시 저장: 1시간 30회. SQLite 원자적 카운터로 재시작에도 유지하고 만료 시 삭제한다. 키는 `SECRET_KEY` 기반 HMAC이며 원 IP/아이디를 rate-limit 테이블에 저장하지 않는다. Railway의 기존 신뢰 프록시 설정을 사용한다.
- 로그에 비밀번호, 해시, 토큰, 계정 폼 내용을 출력하지 않는다. API로 세션/회원 테이블을 노출하지 않는다.

## 권한 / 탈퇴

- 공개 이슈 조회는 비회원 가능. 회원 작성은 승인 없이 즉시 공개. 작성자 본인만 회원 경로에서 수정/삭제한다. ID를 바꾸면 403, 없는/삭제된 글은 404.
- 회원 수정은 필드 allowlist를 사용하고 작성자/관리자 노출 옵션/공개 여부를 받지 않는다. 관리자가 숨긴 글은 작성자가 수정해도 숨김 유지.
- `/admin/users`, `/admin/user-posts`는 기존 관리자 및 active인 role=admin 회원만 접근 가능. 웹에서 회원을 admin으로 승격하는 기능은 없다.
- 회원의 게시물 삭제는 `deleted_at` 기록 및 비공개 처리. 관리자는 기존 뉴스 CRUD에서 모든 글 수정/숨김/영구 삭제 가능.
- 탈퇴는 현재 비밀번호와 확인 동의를 요구한다. status=deleted, 임의 식별자로 username 교체, nickname=탈퇴한 사용자, password_hash 제거, last_login_at 제거 및 모든 세션 폐기. 글과 내부 작성자 연결은 유지한다. 차단은 기존 글을 자동으로 지우지 않는다.
- 현재 DB에서는 회원 식별 정보를 제거하지만 이전 백업에는 이전 시점 정보가 남을 수 있다. 운영자의 백업 보존/폐기 정책 적용이 필요하다.

## 입력 및 화면

- 모든 변경 경로에 세션 CSRF 검사, SQL 매개변수 사용, 템플릿 자동 escaping. 본문은 일반 텍스트 + pre-wrap으로 표시한다.
- 외부 URL은 http/https만 허용하고 자격 증명/공백/따옴표/제어문자 URL을 거부한다. 기존 잘못된 URL은 DB를 변경하지 않고 출력만 생략한다. 서버에서 외부 URL을 fetch하지 않는다.
- 제목 200자, 요약 600자, 본문 20,000자, 닉네임 2~30자, URL 2048자, 요청 본문 최대 128 KiB. 날짜 형식과 범위도 검증한다.
- 이메일/전화번호 등 추가 개인정보가 없으므로 암호화 키를 생성하거나 수집하지 않는다. 향후 도입 시 AES-256-GCM 및 Railway `PII_ENCRYPTION_KEY`, 필요 시 별도 HMAC 검색 키 설계가 필요하다. 비밀번호 찾기/이메일 복구는 현재 제공하지 않는다.

## 검증

임시 DB 환경변수를 지정한 뒤 `python -m unittest discover -s tests -p 'test_*.py' -v`를 실행한다. app 모듈 import가 DB를 열기 때문에 실제 DB를 가리킨 상태로 테스트를 시작하지 않는다.
