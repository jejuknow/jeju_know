# 운영 안전성 점검 (2026-11-17)

## 세션·로그인

`jejuno_session`은 무작위 불투명 토큰만 담는다. SQLite `auth_sessions`에는 토큰의 SHA-256 해시와 최소 세션 상태를 보관한다. 운영 쿠키는 Secure/HttpOnly/SameSite=Lax, Path=/이며 일반 회원은 7일, 관리자는 8시간, 비로그인은 1시간 절대 만료다. 요청으로 만료를 연장하지 않는다. 기존 7일 관리자 세션도 원래 발급 시각을 기준으로 8시간 상한을 적용한다.

로그인 시 토큰을 교체하고 이전 토큰을 삭제한다. 로그아웃은 서버 세션을 삭제한다. 관리자 비밀번호/아이디 변경과 비활성화는 auth_version 변경 및 세션 삭제로 기존 세션을 철회한다. 변경 전 비밀번호 원문은 저장하지 않는다. Argon2id와 기존 관리자 해시의 로그인 시 업그레이드를 유지한다. 2FA는 이번 범위에서 구현하지 않았다.

기존 관리자 로그인 제한은 IP 및 아이디 각각 15분 동안 10회 시도다. SQLite의 원자적 카운터와 HMAC 식별자를 재사용하며 재배포로 초기화하지 않는다. 성공/실패를 포함한 시도 제한이며 계정 영구 차단이 아니다. 사용자명이 바뀌어도 IP 제한은 유지한다. 실패 응답은 동일하고 제한 이벤트 로그에는 원문 IP·아이디·비밀번호를 쓰지 않는다.

## CORS·키·오류

CORSMiddleware와 Access-Control-Allow-Origin/Credentials 헤더를 사용하지 않는다. UI/API는 같은 origin에서 사용하며 www가 아닌 호스트의 GET/HEAD는 www로 이동한다. 임의 origin 및 localhost에 인증 API 교차 출처 읽기 권한을 추가하지 않는다. 공개 GET API도 cross-origin 허용을 새로 추가하지 않았다. CSRF 검증은 별도로 유지한다.

Railway 운영에는 `SECRET_KEY` 환경변수가 필요하다. 기존 로컬 설치의 secret_key.txt 호환은 유지하고 새 파일은 0600으로 생성한다. 문의 AES-GCM 키인 `PII_ENCRYPTION_KEY`와 `SECRET_KEY`는 서버 전용이며 값을 HTML이나 로그에 출력하지 않는다. `KAKAO_MAP_JS_KEY`는 브라우저 공개 키다. 키 회전 및 카카오 콘솔 허용 도메인 변경은 이 작업에서 수행하지 않는다.

운영 debug는 기본 False이며 API 문서 비공개 정책을 유지한다. 일반 500 응답은 안전한 한국어 문구만 표시한다. 자체 오류 로그는 예외 유형과 파일/행/함수 위치만 남기며 요청 본문, 로컬 변수, 임의 예외 메시지를 출력하지 않는다.

## 회원 탈퇴·기록·업로드

탈퇴 시 username을 무작위 값으로 바꾸고 nickname은 ‘탈퇴한 사용자’, password_hash는 빈 값, last_login_at은 NULL, status는 deleted로 처리하며 모든 회원 세션을 삭제한다. 장소·ISSUE·수정 제안·문의와 관계 ID는 보존한다. 문의 암호화 내용은 탈퇴 시 자동 삭제하지 않으며 /privacy에 실제 보관 상태와 /contact 삭제 요청 방법을 안내한다. 미확정 법적 보유기간은 정하지 않았다. 백업 보관 규칙 및 관리자 세션 시간을 정책 문구에도 반영했다.

기존 admin_activity_logs를 재사용한다. 장소/ISSUE/수정 제안/CSV/BEST/광고/문의/관리자 계정 변경은 이미 기록한다. 이번에는 관리자 로그아웃, 수동 백업, 임시 복구 검증, 백업 다운로드를 추가했다. 로그 조회와 백업은 슈퍼관리자만 허용한다. 메타데이터에 비밀번호·해시·토큰·문의 본문을 기록하지 않는다.

이미지는 실제 디코더와 형식 허용목록, 32MB/5천만 화소 제한, 무작위 파일명, 경로 검증과 WebP 재인코딩을 유지한다. EXIF/GPS 등을 제거하고 실행 가능한 파일을 제공하지 않는다. iPhone의 이름/MIME 불일치와 HEIF/MPO 정지 프레임 처리 정책은 변경하지 않았다.

## Dashboard와 DB 변경

좌표 누락은 활성 장소의 latitude 또는 longitude NULL, 사진 누락은 대표 슬롯(로컬/외부 참조)·기존 image_url·병합된 갤러리 fallback이 모두 없는 경우다. 외부 URL의 응답 여부를 매번 크롤링하지 않는다. 오래된 정보는 기존 checked_at의 달력 6개월 기준이며 확인일 없음은 별도다.

집계는 SQL COUNT/FILTER/EXISTS를 사용한다. 중복 탐지는 기존 알고리즘을 유지하면서 `place_duplicate_revision` 1행과 장소/중복 제외 기록의 INSERT/UPDATE/DELETE 트리거 6개로 결과 캐시를 무효화한다. 변경이 없는 Dashboard 방문에서는 모든 장소를 다시 Python 객체로 읽지 않는다. 트리거 변경은 롤백 시 함께 롤백된다. migration은 사전 SQLite 백업·무결성 확인 후 수행하며 기존 모든 테이블의 행 지문이 바뀌지 않았음을 확인한다.

백업·복구 절차 및 동일 Volume 백업의 한계는 [backup-recovery.md](backup-recovery.md)에 있다.
