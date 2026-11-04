"""Presentation only: each domain translates its own stored or derived values."""
from typing import NamedTuple


class Badge(NamedTuple):
    label: str
    tone: str = "neutral"


UNKNOWN = Badge("상태 확인 필요")
WAITING = Badge("검토 대기")
VERIFIED = Badge("JEJU_KNOW 확인", "brand")
EDITORIAL = Badge("JEJU_KNOW 등록")
REJECTED = Badge("반려", "attention")
VISIBILITY = {True: Badge("공개", "positive"), False: Badge("비공개")}
CORRECTION = {"pending": WAITING, "accepted": Badge("반영", "positive"), "rejected": REJECTED}
ISSUE_APPROVAL = {"pending": WAITING, "approved": Badge("승인·공개", "positive"), "rejected": REJECTED}
SCHEDULE = {"upcoming": Badge("예정", "brand"), "ongoing": Badge("진행중", "positive"),
            "ended": Badge("종료"), "undated": Badge("일정 미등록")}
INQUIRY = {"unread": Badge("접수"), "read": Badge("확인중", "brand"), "done": Badge("처리완료", "positive")}


def place_review(source, verification):
    if verification == "jejuno_verified":
        return VERIFIED
    # The historical cleanup treated community_confirmed as non-editorial.
    if verification in ("unverified", "community_confirmed"):
        if source == "user":
            return WAITING
        if source == "jejuno":
            return EDITORIAL
    return UNKNOWN


def public_place_badge(source, verification, is_pick=False):
    """Public discovery labels, independent of internal review/provenance labels.

    PICK is rendered separately by cards. Defaults have no public status badge.
    Only the stored, explicit user source proves a member submission.
    """
    if is_pick:
        return Badge("")
    if verification == "jejuno_verified":
        return VERIFIED
    if source == "user":
        return Badge("사용자 등록")
    return Badge("")


def issue_approval(value, visible):
    # Approved posts may be hidden or past their public calendar window.
    if value == "approved" and not visible:
        return Badge("승인·비공개")
    return ISSUE_APPROVAL.get(value, UNKNOWN)
