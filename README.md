# JEJUNO SQLite 관리자 버전

제주노 초기 프로토타입에 **SQLite 데이터베이스 + 관리자 로그인 + 장소 CRUD**를 연결한 실행형 버전입니다.

## 구현된 기능

- 공개 홈: 지역 / 카테고리 / 동행 / 특징 필터
- SQLite에서 공개 장소를 실시간 조회
- 장소별 고유 URL: `/place/<slug>`
- 관리자 최초 1회 계정 생성: `/admin/setup`
- 관리자 로그인: `/admin/login`
- 장소 등록 / 조회 / 수정 / 삭제(CRUD)
- 공개/비공개 즉시 전환
- 저장 시 승인 절차 없이 즉시 반영
- 삭제 시 확인창 후 즉시 삭제
- JEJUNO PICK 설정
- 예시 데이터 여부 표시
- 주차/반려동물/아이동반/무장애를 `가능/불가/제한/미확인`으로 구분
- 비밀번호는 PBKDF2 해시로 저장
- 관리자 POST 작업에 간단한 CSRF 보호 적용

## 실행 방법 (Windows 기준)

1. Python 3.11 이상 설치
2. 이 폴더에서 터미널 열기
3. 아래 명령 실행

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

4. 브라우저에서 `http://127.0.0.1:5000` 접속
5. 최초 관리자 설정: `http://127.0.0.1:5000/admin/setup`
6. 관리자 계정 생성 후 `/admin`에서 장소 관리

## 데이터 파일

기본 SQLite 파일:

```text
data/jejuno.db
```

이 파일 하나를 백업하면 장소와 관리자 계정을 함께 백업할 수 있습니다.

## 중요: Vercel 배포 관련

이 버전은 **쓰기 가능한 SQLite 파일이 계속 유지되는 서버 환경**을 전제로 합니다.
Vercel의 일반 서버리스 실행 환경에 로컬 SQLite 파일을 두고 운영하면, 관리자에서 저장한 데이터가 영구적으로 유지된다고 보장하기 어렵습니다.

따라서 SQLite 요구사항을 유지한다면 다음 배포 단계에서 **Railway/Render의 영구 볼륨 또는 일반 VPS**처럼 파일이 유지되는 환경을 추천합니다.

Vercel을 계속 사용하려면 SQLite 파일 대신 서버리스 환경에 맞는 외부 DB로 바꾸는 편이 맞습니다.

## 공개/삭제 정책

이 프로젝트에는 승인 상태가 없습니다.

- 관리자 폼에서 `공개` 체크 후 저장 → 즉시 공개
- 목록에서 `공개/비공개` 버튼 → 즉시 전환
- `삭제` → 확인 후 즉시 DB에서 삭제

삭제 복구 기능은 아직 없습니다. 운영 전에는 DB 파일 백업을 권장합니다.

## 테스트

```bash
python -m unittest discover -s tests -v
```

검증 항목:
- 최초 관리자 생성
- 로그인
- 장소 생성
- 공개 API 반영
- 장소 수정
- 공개/비공개 전환
- 삭제

## V2 업데이트: 제주 소식 + 홍보 구조

- 홈페이지에 편집형 `JEJUNO TODAY` 섹션 추가
- 행사·축제 / 전시 / 마켓 / 체험·교육 / 공모·지원 / 지역소식 관리
- `/news` 제주 소식 목록 및 카테고리·키워드 검색
- `/news/{slug}` 소식 상세와 출처·확인일·공식 링크
- 관리자에서 제주 소식 등록·수정·공개/비공개·삭제
- `홈 상단 노출`과 `광고·협찬` 표시를 별도로 관리
- `/partnership` 광고·제휴 안내 페이지 추가
- 유료 홍보는 일반 추천과 구분해 `광고·협찬` 라벨 표시

외부 사이트의 게시물 원문/이미지를 자동 복제하지 않고, 운영자가 확인한 사실을 요약해 출처와 확인일을 기록하는 방식을 기본으로 합니다.

## 이미지 파일 업로드

- 저장 위치는 DB 파일과 같은 영구 데이터 디렉터리의 `uploads/issues/`, `uploads/places/`입니다. Railway에서는 `/data/jejuno.db`와 `/data/uploads/`를 같은 Volume에서 사용합니다. 재배포 시 업로드 파일을 복사하거나 초기화하지 않습니다.
- `20261011_image_uploads` migration은 먼저 SQLite online backup을 `/data/backups/jejuno-before-image-upload-*.db`에 생성하고 무결성을 검사합니다. 백업 실패 시 시작을 중단합니다. 기존 행과 `image_url`은 변경하지 않고 `images`, `image_file_gc` 테이블만 추가합니다.
- `images`에는 장소/이슈 외래키, 랜덤 파일 경로, WebP MIME, 너비·높이·바이트 크기·순서를 저장합니다. 순서 0은 대표사진, 1~4는 장소 추가사진입니다. 업로드 대표사진이 기존 URL보다 우선하고, 삭제하면 URL 또는 이모지로 돌아갑니다.
- JPG/JPEG·PNG·WebP만 허용하며 확장자·MIME·실제 디코딩 형식을 모두 검사합니다. 파일당 10MiB, 2,500만 화소를 넘는 이미지와 움직이는 이미지·HEIC·SVG는 거절합니다. Pillow로 회전을 보정하고 긴 변 최대 1920px, WebP quality 86으로 재인코딩하며 EXIF/GPS/XMP를 제거합니다. [Pillow 이미지 저장 문서](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#webp)
- 파일 입력은 모바일 기본 사진 선택기를 사용합니다. 업로드 전 미리보기는 브라우저에서만 생성되고, 콘텐츠 저장 버튼을 누를 때 전송됩니다. iPhone에서 HEIC 파일이 선택되면 JPG/PNG 변환 안내가 표시됩니다.
- `/uploads/{kind}/{filename}`은 DB 등록 여부와 공개 여부/소유자/관리자 권한을 검사합니다. 이 경로를 별도의 공개 StaticFiles 또는 CDN 원본으로 교체하면 비공개 보호가 사라지므로 유지해야 합니다.
- 콘텐츠와 이미지 메타데이터 변경은 하나의 DB transaction으로 처리합니다. 교체·삭제 파일은 참조를 확인한 뒤 제거하며 일시적인 파일 삭제 실패는 `image_file_gc`에 남겨 다음 저장 또는 서버 재시작 때 재시도합니다. DB 복원 시에는 동일 시점의 `uploads/` 파일 백업도 함께 복원해야 합니다.

## 관리자 CSV 일괄 등록

`/admin/imports`에서 카페·맛집을 각각 업로드합니다. CSV는 UTF-8/BOM, 5MB 이하, 최대 500행입니다. 카페 37열 / 맛집 38열의 최신 양식과 4개 시트 Excel 양식은 관리자 화면에서 다운로드합니다. 분류 선택 목록은 다운로드 당시 활성 DB taxonomy를 사용합니다. Excel 복수 선택은 `|`로 직접 입력하며 맛집 세부유형은 부모 분류에 속해야 합니다.

업로드는 장소를 저장하지 않습니다. 미리보기에서 정상/경고/오류/중복을 확인하고 신규·업데이트·건너뛰기를 선택합니다. 경고와 중복은 기본 건너뛰기이며 오류 행은 저장할 수 없습니다. 동일 이름·주소, 같은 주소, 유사 이름·가까운 좌표를 중복 후보로 표시합니다. `place_id`는 같은 카테고리의 현재 장소만 허용하며 대상 확인이 필요합니다.

신규 장소의 기본값은 비공개·미확인·PICK 아님입니다. UPDATE는 비어 있지 않은 관리 대상 셀만 반영합니다. CSV에 없는 설명·추천이유·예약·웨이팅·정보출처·정보확인일·추천시간대·디저트 특징은 보존합니다. 꿀팁은 기존 `places.note`를 사용하고, 비고는 관리자 전용 `admin_import_note`에 저장합니다. 값을 지우려면 수동 수정 화면을 사용합니다.

좌표가 없으면 미리보기의 주소 위치 찾기 버튼으로 기존 Kakao geocoder를 호출합니다. 실패한 주소는 경고를 확인한 후 좌표 없이 등록할 수 있습니다. 사진은 기존 `/uploads/` 이미지 또는 HTTP(S) URL 참조만 허용합니다. 외부 다운로드는 하지 않으며 채워진 사진 슬롯의 교체는 수동 수정에서만 가능합니다.

실제 저장 직전에 `DATABASE`와 같은 디렉터리의 `backups/jejuno-before-{cafe|restaurant}-csv-import-*.db`를 생성하고 무결성을 검사합니다. `BEGIN IMMEDIATE` 거래 안에서 전체 행과 재요청 방지 영수증을 함께 저장합니다. 미리보기 후 장소·분류가 변경되면 재업로드가 필요합니다. 미리보기 파일은 DB 디렉터리의 비공개 `csv-import-previews/`에 저장되며 1시간 후 만료됩니다. 영수증에는 작업 ID와 결과 ID·백업 경로만 남습니다.

추가 migration은 기존 장소를 재작성하지 않고 `csv_import_runs`, `place_image_references`, `places.admin_import_note`만 추가합니다. 기존 이미지 메타데이터와 파일은 보존됩니다. 외부 이미지 참조도 기존 갤러리·수동 교체·삭제·병합 흐름에 연결됩니다. 운영 DB나 볼륨을 seed 데이터로 교체하지 않습니다.

테스트 양식: `tests/fixtures/cafe-import.csv`, `tests/fixtures/restaurant-import.csv` (각 정상 2행·파일 내 중복 1행·분류 오류 1행). 운영 데이터에 남기지 않는 개발용 예제입니다. Python 검증은 `python -m unittest discover -s tests -p test_csv_import.py`, 프론트 회귀 검증은 `node --test tests/*.test.cjs`로 실행합니다.
