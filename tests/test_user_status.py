import unittest
from datetime import date

from issue_discovery import schedule
from user_status import CORRECTION, INQUIRY, UNKNOWN, issue_approval, place_review


class UserStatusTests(unittest.TestCase):
    def test_approval_visibility_and_schedule_are_independent(self):
        issue = {"event_start": "2026-11-03", "event_end": "2026-11-05"}
        for day, expected in [(2,"예정"),(3,"진행중"),(5,"진행중"),(6,"종료")]:
            self.assertEqual(schedule(issue,date(2026,11,day))["event_status"],expected)
        for state, visible, expected in [("approved",True,"승인·공개"),("approved",False,"승인·비공개"),
                                         ("pending",False,"검토 대기"),("rejected",False,"반려")]:
            self.assertEqual(issue_approval(state,visible).label,expected)
        self.assertEqual(issue_approval("unexpected",True),UNKNOWN)

    def test_only_explicit_verification_earns_verified_badge(self):
        self.assertEqual(place_review("user","unverified").label,"검토 대기")
        self.assertEqual(place_review("jejuno","unverified").label,"JEJU_KNOW 등록")
        self.assertEqual(place_review("user","jejuno_verified").label,"JEJU_KNOW 확인")
        self.assertEqual(place_review("user","community_confirmed").label,"검토 대기")
        self.assertEqual(place_review("user","unknown"),UNKNOWN)
        self.assertEqual(place_review("unknown","unverified"),UNKNOWN)

    def test_current_storage_values_are_domain_specific(self):
        self.assertEqual([CORRECTION[k].label for k in ("pending","accepted","rejected")],["검토 대기","반영","반려"])
        self.assertEqual([INQUIRY[k].label for k in ("unread","read","done")],["접수","확인중","처리완료"])
        self.assertEqual(INQUIRY.get("unexpected",UNKNOWN).label,"상태 확인 필요")
