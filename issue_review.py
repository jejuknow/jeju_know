"""Member ISSUE submissions share the existing news, author, schedule and image records."""
import re
from difflib import SequenceMatcher

from admin_settings import catalog
from community_migrations import today
from issue_calendar_migration import apply_columns
from issue_discovery import PUBLIC_CATEGORIES, public_where


def migrate(database):
    # Approval defaults preserve the old publication policy; new member submissions
    # explicitly insert pending/private. Never rewrite historical visibility or authors.
    apply_columns(database, "20261102_issue_review", "jejuno-before-user-issue-submission", {
        "approval_status": "TEXT NOT NULL DEFAULT 'approved' CHECK(approval_status IN ('pending','approved','rejected'))",
        "reviewed_by": "TEXT", "reviewed_at": "TEXT", "rejection_reason": "TEXT NOT NULL DEFAULT ''",
        "review_version": "INTEGER NOT NULL DEFAULT 0",
    }, "CREATE INDEX IF NOT EXISTS issue_review_queue ON news_posts(approval_status,id) WHERE deleted_at IS NULL")


def review_label(item):
    if item["approval_status"] == "pending":
        return "검토 대기"
    if item["approval_status"] == "rejected":
        return "반려"
    if item["event_status"] == "종료":
        return "종료"
    return "승인·공개" if item["published"] and item["event_status"] != "일정 미등록" else "비공개"


def submission_error(conn, data):
    if data["category"] not in PUBLIC_CATEGORIES.values():
        return "공개 ISSUE 카테고리를 선택해주세요."
    if data["region"] not in {r["value"] for r in catalog(conn, "region")} or not data["summary"]:
        return "지역과 한줄 소개를 입력해주세요."
    if not data["event_start"] or not data["event_end"]:
        return "시작일과 종료일을 모두 선택해주세요."
    if data["event_end"] < today().isoformat():
        return "이미 종료된 ISSUE는 등록할 수 없습니다."
    if any(re.search(r"<\s*/?\s*[a-zA-Z!][^>]*>", data[k]) for k in
           ("title", "summary", "body", "venue", "address", "source_name")):
        return "HTML 태그 없이 일반 텍스트로 입력해주세요."


def has_poster(conn, plan, news_id):
    return 0 in plan.new or (0 not in plan.remove and bool(news_id) and
        conn.execute("SELECT 1 FROM images WHERE issue_id=? AND sort_order=0", (news_id,)).fetchone() is not None)


def candidates(conn, item, *, public_only):
    """Warn on overlapping events, never expose another author's unpublished title."""
    where = "deleted_at IS NULL AND approval_status!='rejected'"
    params = []
    if public_only:
        where, params = public_where(today())
    rows = conn.execute(f"""SELECT id,slug,title,venue,address,event_start,event_end
        FROM news_posts WHERE {where} AND id!=? AND event_start<=? AND event_end>=?
        ORDER BY id DESC""", [*params, item.get("id") or 0, item["event_end"], item["event_start"]])
    def norm(text):
        return "".join((text or "").casefold().split())
    title = norm(item["title"])
    hits = []
    for row in rows:
        other = norm(row["title"])
        same_place = any(norm(item.get(k)) and norm(item[k]) == norm(row[k]) for k in ("address", "venue"))
        similarity = SequenceMatcher(None, title, other).ratio()
        if title and (title == other or similarity >= .82 or (same_place and similarity >= .55)):
            hits.append(dict(row))
            if len(hits) == 5:
                break
    return hits
