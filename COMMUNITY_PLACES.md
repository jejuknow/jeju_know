# 장소 참여·월별 PICK 운영

- FIND(`/find`), NEW(`/new`), MAP(`/map`)은 기존 `places`와 `/api/places`를 공유한다.
- 회원 등록은 즉시 공개되며 `사용자 등록 · 정보 미확인`으로 표시한다. 추천 이유, 확인 상태, PICK, BEST, 공개 여부는 서버에서 관리자에게만 허용한다.
- 작성자는 미확인 장소만 직접 수정·삭제할 수 있다. 확인 완료 후에는 수정 제안을 사용한다. 회원 탈퇴 시 장소·제안은 보존한다.
- 사용자 삭제는 공개 목록에서 제외하는 논리 삭제다. 관리자 삭제는 사진 파일 정리 정책을 유지하되 PICK·제안·모음 이력이 있는 장소 행은 보관한다.

## 관리자

- `/admin/community`: 사용자 장소, 미확인/확인 완료/숨김/중복 의심, 수정 제안 처리.
- `/admin/places/{id}/edit`: 사실 정보, 확인 상태, NEW 예외 노출, 기존 장소 관리.
- `/admin/places/{id}/merge`: 유지할 장소를 먼저 미리 본 뒤 병합한다. 양쪽 원본 행과 사진을 유지하고 제안·모음·PICK 연결을 보존한다. 이전 상세 URL은 유지할 공개 장소로 301 이동한다. 중복 월의 PICK 값은 유지할 장소의 값이 우선하며 원본 월별 기록도 남는다.
- `/admin/picks`: 연도·월·순서·설명·활성화 관리. 한국 시간의 현재 월 활성 PICK만 홈과 MAP에 표시한다. 과거 기록은 삭제하지 않는다.
- `/admin/best`: 기존 추천 모음에 장소를 직접 추가·제외하고 순서를 조정한다. 신규 회원 장소를 자동 편입하지 않는다.
- 수정 제안 반영 시 원본이 제안 이후 변경되었다면 충돌을 안내하고 덮어쓰지 않는다. 기타 제안은 장소 수정에서 직접 반영하고 처리 메모를 남긴다.

## DB 보존

시작 시 `20261012_community_places` migration 전에 SQLite 온라인 백업을 생성하고 무결성을 확인한다. 실패하면 시작을 중단한다. `/data/jejuno.db`나 `/data/uploads`를 덮어쓰거나 seed로 교체하지 않는다.

- `places`: 등록자, 출처, 확인 상태, 오픈 연도/월, 홈페이지, NEW 예외, 확인 일시/주체, 논리 삭제·병합 연결 컬럼 추가.
- 새 테이블: `place_correction_suggestions`, `place_pick_periods`, `best_collections`, `best_collection_places`, `place_merge_log`.
- 기존 PICK 플래그를 migration 시점의 현재 월 기록으로 옮기고 기존 플래그도 보존한다.
- 기존 추천 모음은 별도 DB 없이 3개 태그 조회로 구현되어 있었다. 동일한 모음과 당시 해당 장소를 한 번 저장해 연결을 보존한다. 이후에는 관리자만 구성한다.
- 기존 장소를 임의로 방문·확인한 상태로 올리지 않는다.
- 운영 Volume 백업: `/data/backups/jejuno-before-community-places-<날짜-시간>.db`.

## 검증

운영 DB를 사용하지 않는 임시 데이터 디렉터리를 `JEJUNO_DATA_DIR`로 지정한 후 실행한다.

```
python -m unittest discover -s tests -v
node --test tests/*.test.cjs
```

권한 우회, CSRF, 입력값/URL 검증, XSS, 중복 경고, 월 변경, NEW 현재 연도, 병합 후 사진·URL·관계, 재시작 보존, 이전 로그인/이슈/업로드/필터 기능을 검증한다.
