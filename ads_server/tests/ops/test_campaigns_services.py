import io
import uuid
from unittest import mock

from django.db import IntegrityError, connection, transaction
from django.http import Http404
from django.test import Client, TestCase, TransactionTestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext

from assets.api import ImageRejected, asset_exists
from campaigns import api
from campaigns.changes import record_change
from campaigns.models import AdGroup, AdGroupProfile, AdGroupProfileVersion, AdGroupSegment, Bid, Campaign, ChangeLog, Creative
from events.api import OutsideTransaction, event_id_for
from events.models import EventOutbox
from foundation.errors import Conflict, Forbidden, InvalidRequest, StaleVersion
from foundation.models import AuditLog
from masters import api as masters
from tests.support import image_factory
from tests.support.concurrency import overlap, run_together
from tests.support.factories import DAY, NOW, SUBJECT, World, make_admin, make_advertiser, make_slot, png_file
from tests.support.cases import Cases

SEC = DAY / 86400
ENDS_AT_ERROR = [{"pointer": "/ends_at", "message": "종료 시각은 시작보다 뒤이고 지금보다 뒤여야 합니다."}]



def internal_client(admin, label="분류 모듈"):
    """C8 시험: 관리자가 발급한 내부 키로 인증한 클라이언트(`InternalIdentity`)."""
    return masters.authenticate_internal_key(masters.issue_internal_key(admin, label, now=NOW).key)


def bare_ad_group(campaign_id, creative_id=None, name="옛 광고"):
    """0005 전에 만든 광고처럼 카테고리 줄이 없는 광고그룹(ORM 으로만 — 광고 만들기 서비스는 줄을 함께 만든다)."""
    return AdGroup.objects.create(campaign_id=campaign_id, name=name, creative_id=creative_id, created_at=NOW, updated_at=NOW)


def ad_with_creative(w, campaign_id, name, color):
    """`w.slot` 에 놓인 광고 하나와 그 기본 소재(이미지 색 `color` — 광고마다 다른 이미지). 광고 ID 를 돌려준다."""
    g = api.create_ad_group(w.adv, campaign_id, name, [w.slot.slot_id], now=NOW).ad_group_id
    api.create_creative(w.adv, g, f"{name} 소재", "https://example.com/x", png_file(color), now=NOW)
    return g


class CampaignServiceTests(Cases, TestCase):
    def setUp(self):
        self.w = World()

    def case_create_campaign_validation(self):
        adv = self.w.adv
        bad = [
            dict(name=""), dict(name="x" * 101), dict(daily_budget=0), dict(daily_budget=1.5), dict(daily_budget=True), dict(daily_budget=2**53),
            dict(ends_at=NOW - DAY), dict(ends_at=NOW), dict(starts_at=NOW + 20 * DAY),  # 종료가 시작보다 앞/지금 이전
            dict(starts_at=NOW.replace(tzinfo=None)),
        ]
        for kw in bad:
            args = dict(name="새", starts_at=NOW, ends_at=NOW + DAY, daily_budget=100)
            args.update(kw)
            with self.assertRaises(InvalidRequest, msg=kw):
                api.create_campaign(adv, args["name"], args["starts_at"], args["ends_at"], args["daily_budget"], now=NOW)
        self.assertEqual(len(api.list_campaigns(adv, now=NOW)), 1)

    def case_only_advertisers_use_advertiser_functions_and_only_admins_admin_functions(self):
        for actor in (self.w.admin, None):
            with self.assertRaises(Forbidden):
                api.create_campaign(actor, "c", NOW, None, 10, now=NOW)
        cid = self.w.campaign.campaign_id
        for actor in (self.w.adv, make_advertiser(), None):
            with self.assertRaises(Forbidden):
                api.suspend_campaign(actor, cid, "사유", now=NOW)
            with self.assertRaises(Forbidden):
                api.release_campaign(actor, cid, now=NOW)
            with self.assertRaises(Forbidden):
                api.reject_review_request(actor, uuid.uuid4(), "사유", now=NOW)

    def case_campaign_view_shows_ended_when_period_passed_even_if_status_not_updated(self):
        later = NOW + 11 * DAY
        view = api.get_campaign(self.w.adv, self.w.campaign.campaign_id, now=later)
        self.assertEqual((view.status, view.ended_from), ("ENDED", "ACTIVE"))
        self.assertEqual(api.get_campaign(self.w.adv, self.w.campaign.campaign_id, now=NOW).status, "ACTIVE")

    def case_update_campaign_fields_and_pause_resume(self):
        cid, adv = self.w.campaign.campaign_id, self.w.adv
        v = api.update_campaign(adv, cid, {"name": "새 이름", "daily_budget": 777, "status": "PAUSED"}, now=NOW)
        self.assertEqual((v.name, v.daily_budget, v.status), ("새 이름", 777, "PAUSED"))
        self.assertEqual(api.update_campaign(adv, cid, {"status": "PAUSED"}, now=NOW).status, "PAUSED")  # 같은 값은 무변화
        self.assertEqual(api.update_campaign(adv, cid, {"status": "ACTIVE"}, now=NOW).status, "ACTIVE")
        for bad in ({}, {"starts_at": NOW}, {"status": "SUSPENDED"}, {"status": "ENDED"}, {"name": ""}, {"daily_budget": 0}):
            with self.assertRaises(InvalidRequest, msg=bad):
                api.update_campaign(adv, cid, bad, now=NOW)

    def case_an_ad_may_combine_any_slots(self):
        """과금 단위가 없어(6-10d) 비율이 다른 구성도 한 광고에 함께 고른다 — 과금은 구성마다 그 정책의 비율로 나뉜다.
        만든 결과가 알리는 구성(정렬)은 나중에 읽은 것과 같다."""
        click_heavy = make_slot(self.w.admin, unit="CLICK")  # 클릭 98%
        group = api.create_ad_group(self.w.adv, self.w.campaign.campaign_id, "g", [click_heavy.slot_id, self.w.slot.slot_id], now=NOW)
        self.assertEqual(group.slot_ids, tuple(sorted([self.w.slot.slot_id, click_heavy.slot_id], key=str)))
        self.assertEqual(group.slot_ids, api.get_ad_group(self.w.adv, group.ad_group_id).slot_ids)

    def case_ad_group_slot_validation(self):
        inactive = make_slot(self.w.admin)
        masters.set_slot_active(self.w.admin, inactive.slot_id, False)
        media_off = make_slot(self.w.admin)
        masters.set_media_active(self.w.admin, media_off.media_id, False)
        s = self.w.slot.slot_id
        for slots in ([], [uuid.uuid4()], [inactive.slot_id], [media_off.slot_id], [s, s], ["nope"], "not-a-list"):
            with self.assertRaises(InvalidRequest, msg=slots):
                api.create_ad_group(self.w.adv, self.w.campaign.campaign_id, "g", slots, now=NOW)
        with self.assertRaises(InvalidRequest):
            api.create_ad_group(self.w.adv, self.w.campaign.campaign_id, " ", [s], now=NOW)  # 빈 이름
        with self.assertRaises(InvalidRequest):
            api.create_ad_group(self.w.adv, self.w.campaign.campaign_id, "g", [s], bid_amount=0, now=NOW)
        self.assertEqual(len(api.list_ad_groups(self.w.adv, self.w.campaign.campaign_id)), 1)  # 실패한 시도는 남지 않는다

    def case_list_ad_groups_reads_a_fixed_number_of_queries(self):
        """계획 3-4g ④: 질의 수가 광고그룹 수와 무관하다(전에는 그룹마다 현재 입찰·게재 위치를 따로 읽어 2 + 2N). 결과는 하나씩 읽은 것과 같다."""
        cid = self.w.campaign.campaign_id
        with CaptureQueriesContext(connection) as one:
            api.list_ad_groups(self.w.adv, cid)
        other = make_slot(self.w.admin)
        api.create_ad_group(self.w.adv, cid, "둘째", [other.slot_id, self.w.slot.slot_id], now=NOW + 60 * SEC)  # 입찰 없음
        third = api.create_ad_group(self.w.adv, cid, "셋째", [other.slot_id], bid_amount=400, now=NOW + 120 * SEC)
        api.add_bid(self.w.adv, third.ad_group_id, 450, expected_version=1, now=NOW + 120 * SEC)
        with CaptureQueriesContext(connection) as three:
            groups = api.list_ad_groups(self.w.adv, cid)
        self.assertEqual(len(one), len(three))
        self.assertEqual([(g.name, g.current_bid and (g.current_bid.bid_version, g.current_bid.amount)) for g in groups],
                         [("그룹", (1, 300)), ("둘째", None), ("셋째", (2, 450))])
        self.assertEqual(groups, [api.get_ad_group(self.w.adv, g.ad_group_id) for g in groups])  # 게재 위치 순서·입찰 값까지 하나씩 읽은 것과 같다

    def case_update_ad_group_replaces_slots_atomically(self):
        other = make_slot(self.w.admin)
        view = api.update_ad_group(self.w.adv, self.w.group.ad_group_id, {"slot_ids": [other.slot_id], "name": "이름", "enabled": False}, now=NOW)
        self.assertEqual((view.slot_ids, view.name, view.enabled), ((other.slot_id,), "이름", False))
        with self.assertRaises(InvalidRequest):  # 없는 위치가 섞이면 이름도 바뀌지 않는다
            api.update_ad_group(self.w.adv, self.w.group.ad_group_id, {"name": "바뀌면 안 됨", "slot_ids": [other.slot_id, uuid.uuid4()]}, now=NOW)
        after = api.get_ad_group(self.w.adv, self.w.group.ad_group_id)
        self.assertEqual((after.slot_ids, after.name), ((other.slot_id,), "이름"))
        for bad in ({"enabled": "yes"}, {"pricing_unit": "CLICK"}, {}):  # 과금 단위는 없어져(6-10d) 모르는 필드다
            with self.assertRaises(InvalidRequest, msg=bad):
                api.update_ad_group(self.w.adv, self.w.group.ad_group_id, bad, now=NOW)

    def case_bid_out_of_range_is_stored(self):
        """입력은 거절하지 않는다: 정책 100~500 에서 50 도 5,000 도 저장된다(적용 금액 계산은 engine 몫)."""
        low = api.add_bid(self.w.adv, self.w.group.ad_group_id, 50, expected_version=1, now=NOW)
        high = api.add_bid(self.w.adv, self.w.group.ad_group_id, 5000, expected_version=2, now=NOW)
        self.assertEqual((low.bid_version, low.amount, high.bid_version, high.amount), (2, 50, 3, 5000))
        self.assertEqual([b.amount for b in api.list_bids(self.w.adv, self.w.group.ad_group_id)], [5000, 50, 300])
        self.assertEqual(api.get_ad_group(self.w.adv, self.w.group.ad_group_id).current_bid.amount, 5000)

    def case_every_bid_version_records_its_event_in_the_same_transaction(self):
        """입찰 버전마다 `ad.bid_added` 하나(계획 docs/plans/00 P2-4): 광고그룹을 만들 때(입찰액이 있을 때만 v1)·입찰 추가 모두, 입찰 행과 함께 커밋되거나 함께 되돌려진다."""
        def bid_events(ad_group_id):
            return list(EventOutbox.objects.filter(event_name="ad.bid_added", aggregate_id=ad_group_id).order_by("created_at").values_list("event_id", "payload"))
        gid = self.w.group.ad_group_id
        v2 = api.add_bid(self.w.adv, gid, 400, expected_version=1, now=NOW + SEC)
        bids = api.list_bids(self.w.adv, gid)[::-1]  # 오래된 것부터
        rows = bid_events(gid)
        self.assertEqual([e for e, _ in rows], [event_id_for("ad.bid_added", b.bid_id) for b in bids])  # 버전마다 다른 이벤트, 원천에서 다시 계산한 ID
        self.assertEqual([(p["bid_version"], p["amount"], p["created_at"]) for _, p in rows], [(1, 300, NOW.isoformat()), (2, 400, (NOW + SEC).isoformat())])
        self.assertEqual(v2.bid_id, bids[-1].bid_id)
        empty = api.create_ad_group(self.w.adv, self.w.campaign.campaign_id, "빈 그룹", [self.w.slot.slot_id], now=NOW)
        self.assertEqual(bid_events(empty.ad_group_id), [])  # 입찰액 없이 만들면 입찰도 이벤트도 없다
        with mock.patch("campaigns.services.events.record_bid_added", side_effect=RuntimeError("outbox down")):  # 이벤트 쓰기가 실패하면 입찰 행도 남지 않는다
            with self.assertRaises(RuntimeError):
                api.create_ad_group(self.w.adv, self.w.campaign.campaign_id, "실패 그룹", [self.w.slot.slot_id], bid_amount=500, now=NOW)
            with self.assertRaises(RuntimeError):
                api.add_bid(self.w.adv, gid, 450, expected_version=2, now=NOW)
        self.assertFalse(AdGroup.objects.filter(name="실패 그룹").exists())
        self.assertEqual([b.bid_version for b in api.list_bids(self.w.adv, gid)], [2, 1])
        with self.assertRaises(StaleVersion):  # 입찰 저장이 실패하면 이벤트도 없다
            api.add_bid(self.w.adv, gid, 450, expected_version=1, now=NOW)
        self.assertEqual(len(bid_events(gid)), 2)

    def case_campaign_changes_are_logged_with_their_event_at_each_documented_point(self):
        """`ad.campaign_changed`(계획 docs/plans/00 §5.4): 생성·상태(일시 중지·재개·중단·해제·조기 종료·기간 종료)·일예산·종료 시각마다 로그 한 줄 + 이벤트 하나.
        이름만 바꾸거나 같은 값으로 저장하면 쓰지 않는다. 이벤트가 실패하면 캠페인 변경도 남지 않는다."""
        def changes(campaign_id):
            rows = ChangeLog.objects.filter(target_type="campaign", target_id=campaign_id).order_by("changed_at", "change_id")
            return [(r.change_type, r.previous_value, r.new_value) for r in rows]
        adv, admin, cid, end = self.w.adv, self.w.admin, self.w.campaign.campaign_id, NOW + 10 * DAY
        api.update_campaign(adv, cid, {"name": "새 이름"}, now=NOW + SEC)
        api.update_campaign(adv, cid, {"daily_budget": 60_000, "ends_at": NOW + 20 * DAY, "status": "PAUSED"}, now=NOW + 2 * SEC)  # 한 요청에 세 줄(상태는 전이가 쓴다)
        api.update_campaign(adv, cid, {"daily_budget": 60_000}, now=NOW + 3 * SEC)
        api.update_campaign(adv, cid, {"status": "ACTIVE"}, now=NOW + 4 * SEC)
        api.suspend_campaign(admin, cid, "사유", now=NOW + 5 * SEC)
        api.release_campaign(admin, cid, now=NOW + 6 * SEC)
        api.end_campaign(adv, cid, now=NOW + 7 * SEC)
        iso = lambda t: t.isoformat()
        self.assertEqual(changes(cid), [
            ("CREATED", None, {"status": "ACTIVE", "daily_budget": 50_000, "starts_at": iso(NOW - DAY), "ends_at": iso(end)}),
            ("STATUS_CHANGED", {"status": "ACTIVE"}, {"status": "PAUSED"}),
            ("BUDGET_CHANGED", {"daily_budget": 50_000}, {"daily_budget": 60_000}),
            ("PERIOD_CHANGED", {"ends_at": iso(end)}, {"ends_at": iso(NOW + 20 * DAY)}),
            ("STATUS_CHANGED", {"status": "PAUSED"}, {"status": "ACTIVE"}),
            ("STATUS_CHANGED", {"status": "ACTIVE"}, {"status": "SUSPENDED"}),
            ("STATUS_CHANGED", {"status": "SUSPENDED"}, {"status": "ACTIVE"}),
            ("STATUS_CHANGED", {"status": "ACTIVE", "ends_at": iso(NOW + 20 * DAY)}, {"status": "ENDED", "ends_at": iso(NOW + 7 * SEC)}),  # 조기 종료는 종료 시각도
        ])
        due = api.create_campaign(adv, "기간 종료", NOW - DAY, NOW + DAY, 1_000, now=NOW)
        api.suspend_campaign(admin, due.campaign_id, "사유", now=NOW)
        self.assertEqual(api.end_expired_campaigns(now=NOW + 2 * DAY), 1)
        self.assertEqual(changes(due.campaign_id)[-1], ("STATUS_CHANGED", {"status": "SUSPENDED"}, {"status": "ENDED"}))  # 일괄 종료도 잠근 행의 이전 상태로
        for r in ChangeLog.objects.filter(target_type="campaign"):
            e = EventOutbox.objects.get(pk=event_id_for("ad.campaign_changed", r.change_id))
            self.assertEqual((e.aggregate_id, e.created_at, e.payload["change_type"], e.payload["previous_value"], e.payload["new_value"]),
                             (r.campaign_id, r.changed_at, r.change_type, r.previous_value, r.new_value))
        live = api.create_campaign(adv, "이벤트 실패", NOW - DAY, None, 1_000, now=NOW)
        campaigns_before = len(api.list_campaigns(adv, now=NOW))
        with mock.patch("campaigns.changes.events.record_campaign_changed", side_effect=RuntimeError("outbox down")):
            with self.assertRaises(RuntimeError):
                api.update_campaign(adv, live.campaign_id, {"daily_budget": 5_000}, now=NOW + SEC)
            with self.assertRaises(RuntimeError):
                api.create_campaign(adv, "만들기 실패", NOW - DAY, None, 1_000, now=NOW)
        self.assertEqual((api.get_campaign(adv, live.campaign_id, now=NOW).daily_budget, len(changes(live.campaign_id))), (1_000, 1))
        self.assertEqual(len(api.list_campaigns(adv, now=NOW)), campaigns_before)

    def case_ad_group_changes_are_logged_with_their_event_at_each_documented_point(self):
        """`ad.ad_group_changed`(계획 docs/plans/00 §5.5): 생성(소재 없음)·소재 선택(자동 선택 포함)·켜기/끄기·게재 위치마다 로그 한 줄 + 이벤트 하나.
        이름만 바꾸거나 같은 값(순서만 다른 게재 위치 포함)이면 쓰지 않는다. 이벤트가 실패하면 광고그룹 변경도 남지 않는다."""
        def changes(ad_group_id):
            rows = ChangeLog.objects.filter(target_type="ad_group", target_id=ad_group_id).order_by("changed_at", "change_id")
            return [(r.change_type, r.previous_value, r.new_value) for r in rows]
        adv, gid, slot, first = self.w.adv, self.w.group.ad_group_id, self.w.slot, self.w.creative.creative_id
        other = make_slot(self.w.admin)
        api.update_ad_group(adv, gid, {"name": "새 이름"}, now=NOW + SEC)
        api.update_ad_group(adv, gid, {"enabled": False, "slot_ids": [slot.slot_id, other.slot_id]}, now=NOW + 2 * SEC)
        api.update_ad_group(adv, gid, {"enabled": False, "slot_ids": [other.slot_id, slot.slot_id]}, now=NOW + 3 * SEC)
        second = api.create_creative(adv, gid, "둘째", "https://example.com/2", png_file((9, 9, 9)), now=NOW + 4 * SEC)  # 선택된 소재가 있어 연결만
        api.link_creative(adv, gid, second.creative_id, now=NOW + 5 * SEC, select=True)
        self.assertEqual(changes(gid), [
            ("CREATED", None, {"enabled": True, "slot_ids": [str(slot.slot_id)], "creative_id": None, "segment_ids": []}),  # 대상 그룹 없음 = 전체
            ("CREATIVE_SELECTED", {"creative_id": None}, {"creative_id": str(first)}),  # 소재가 없을 때의 자동 선택
            ("ENABLED_CHANGED", {"enabled": True}, {"enabled": False}),
            ("SLOTS_CHANGED", {"slot_ids": [str(slot.slot_id)]}, {"slot_ids": sorted([str(slot.slot_id), str(other.slot_id)])}),  # 지우기 전의 목록이 이전 값
            ("CREATIVE_SELECTED", {"creative_id": str(first)}, {"creative_id": str(second.creative_id)}),
        ])
        for r in ChangeLog.objects.filter(target_type="ad_group"):
            e = EventOutbox.objects.get(pk=event_id_for("ad.ad_group_changed", r.change_id))
            self.assertEqual((e.aggregate_id, e.created_at, e.payload["campaign_id"], e.payload["previous_value"], e.payload["new_value"]),
                             (r.target_id, r.changed_at, str(r.campaign_id), r.previous_value, r.new_value))
        groups_before = AdGroup.objects.count()
        with mock.patch("campaigns.changes.events.record_ad_group_changed", side_effect=RuntimeError("outbox down")):
            with self.assertRaises(RuntimeError):
                api.update_ad_group(adv, gid, {"enabled": True}, now=NOW + 6 * SEC)
            with self.assertRaises(RuntimeError):
                api.create_ad_group(adv, self.w.campaign.campaign_id, "실패 그룹", [slot.slot_id], now=NOW)
        self.assertEqual((api.get_ad_group(adv, gid).enabled, len(changes(gid)), AdGroup.objects.count()), (False, 5, groups_before))

    def case_a_change_is_one_log_row_and_one_event_with_the_same_values(self):
        """변경 로그(계획 docs/plans/00 §5.2·P2-5): 변경 한 번 = 로그 한 줄 + 변경 이벤트 하나, 같은 값·같은 트랜잭션. 값이 그대로면 아무것도 쓰지 않는다."""
        c, g, slot = self.w.campaign, self.w.group, self.w.slot
        before = ChangeLog.objects.count()  # 세계를 만들 때 캠페인 생성(CREATED)도 한 줄 생긴다
        args = dict(target_type="ad_group", target_id=g.ad_group_id, campaign_id=c.campaign_id, change_type="SLOTS_CHANGED")
        other = uuid.uuid4()
        change_id = record_change(**args, previous={"slot_ids": [slot.slot_id]}, new={"slot_ids": {other, slot.slot_id}}, now=NOW)
        row = ChangeLog.objects.get(pk=change_id)
        self.assertEqual((row.previous_value, row.new_value), ({"slot_ids": [str(slot.slot_id)]}, {"slot_ids": sorted([str(other), str(slot.slot_id)])}))  # UUID 목록은 정렬한 문자열
        event = EventOutbox.objects.get(pk=event_id_for("ad.ad_group_changed", change_id))
        self.assertEqual((event.aggregate_id, event.created_at, event.payload["previous_value"], event.payload["new_value"]),
                         (g.ad_group_id, NOW, row.previous_value, row.new_value))  # 로그 = 이벤트(정합성 원천)
        self.assertIsNone(record_change(**args, previous={"slot_ids": [slot.slot_id]}, new={"slot_ids": (slot.slot_id,)}, now=NOW))  # 그대로면 쓰지 않는다
        created = record_change(target_type="campaign", target_id=c.campaign_id, campaign_id=c.campaign_id, change_type="CREATED", previous=None,
                                new={"status": "ACTIVE", "daily_budget": 50_000, "ends_at": NOW + DAY, "starts_at": NOW}, now=NOW)
        payload = EventOutbox.objects.get(pk=event_id_for("ad.campaign_changed", created)).payload
        self.assertEqual((payload["previous_value"], payload["new_value"]["ends_at"], payload["campaign_id"]), (None, (NOW + DAY).isoformat(), str(c.campaign_id)))
        self.assertEqual(ChangeLog.objects.count(), before + 2)
        for bad in (1.5, NOW.replace(tzinfo=None)):  # 형식을 모르는 값·시간대 없는 시각은 조용히 바꾸지 않는다
            with self.assertRaises(TypeError, msg=bad):
                record_change(**args, previous={"x": 1}, new={"x": bad}, now=NOW)
        with mock.patch("campaigns.changes.connection", mock.Mock(in_atomic_block=False)), self.assertRaises(OutsideTransaction):  # 트랜잭션 밖이면 로그를 쓰기 전에 막는다
            record_change(**args, previous={"slot_ids": []}, new={"slot_ids": [slot.slot_id]}, now=NOW)
        with mock.patch("campaigns.changes.events.record_campaign_changed", side_effect=RuntimeError("outbox down")):  # 이벤트가 실패하면 로그도 남지 않는다
            with self.assertRaises(RuntimeError), transaction.atomic():
                record_change(target_type="campaign", target_id=c.campaign_id, campaign_id=c.campaign_id, change_type="BUDGET_CHANGED",
                              previous={"daily_budget": 50_000}, new={"daily_budget": 70_000}, now=NOW)
        self.assertEqual(ChangeLog.objects.count(), before + 2)
        for kind, previous in (({"target_type": "campaign", "change_type": "SLOTS_CHANGED"}, {"a": 1}), ({"target_type": "ad_group", "change_type": "CREATED"}, {"a": 1})):
            with self.assertRaises(IntegrityError, msg=kind), transaction.atomic():  # DB 가 막는다: 대상에 없는 변경 종류, CREATED 의 이전 값
                ChangeLog.objects.create(target_id=g.ad_group_id, campaign_id=c.campaign_id, previous_value=previous, new_value={"a": 2}, changed_at=NOW, **kind)

    def case_bid_validation(self):
        for bad in (0, -1, 1.5, True, "30", None, 2**53):
            with self.assertRaises(InvalidRequest, msg=bad):
                api.add_bid(self.w.adv, self.w.group.ad_group_id, bad, expected_version=1, now=NOW)

    def case_bid_stale_version_conflict(self):
        gid = self.w.group.ad_group_id
        api.add_bid(self.w.adv, gid, 400, expected_version=1, now=NOW)
        with self.assertRaises(StaleVersion):
            api.add_bid(self.w.adv, gid, 450, expected_version=1, now=NOW)  # 옛 화면
        with self.assertRaises(StaleVersion):
            api.add_bid(self.w.adv, gid, 450, expected_version=None, now=NOW)  # 첫 입찰이라고 착각
        self.assertEqual([b.bid_version for b in api.list_bids(self.w.adv, gid)], [2, 1])
        empty = api.create_ad_group(self.w.adv, self.w.campaign.campaign_id, "빈 그룹", [self.w.slot.slot_id], now=NOW)
        self.assertIsNone(empty.current_bid)
        self.assertEqual(api.add_bid(self.w.adv, empty.ad_group_id, 200, expected_version=None, now=NOW).bid_version, 1)  # 첫 입찰은 None

    def case_quota_limits(self):
        adv, cid = self.w.adv, self.w.campaign.campaign_id
        with override_settings(MAX_CAMPAIGNS_PER_ADVERTISER=1):
            with self.assertRaises(api.LimitExceeded):
                api.create_campaign(adv, "둘째", NOW, None, 10, now=NOW)
            other = make_advertiser()  # 다른 광고주는 자기 몫이 있다
            api.create_campaign(other, "남의 첫째", NOW, None, 10, now=NOW)
        with override_settings(MAX_AD_GROUPS_PER_ADVERTISER=1):
            with self.assertRaises(api.LimitExceeded):
                api.create_ad_group(adv, cid, "둘째", [self.w.slot.slot_id], now=NOW)
        with override_settings(MAX_UPLOAD_BYTES_PER_ADVERTISER=10):
            with self.assertRaises(api.LimitExceeded):
                api.create_creative(adv, self.w.group.ad_group_id, "t", "https://a.example", png_file(), now=NOW)
        with override_settings(MAX_CREATIVES_PER_ADVERTISER=1):
            with self.assertRaises(api.LimitExceeded):
                api.create_creative(adv, self.w.group.ad_group_id, "t", "https://a.example", png_file(), now=NOW)

    def case_an_ad_group_links_several_creatives_and_keeps_its_choice(self):
        """광고그룹당 소재 수 상한은 없다 — 올려서 연결해도 이미 선택한 소재는 그대로다(6-9c)."""
        second = api.create_creative(self.w.adv, self.w.group.ad_group_id, "둘째", "https://a.example", png_file((1, 2, 3)), now=NOW)
        self.assertEqual([c.creative_id for c in api.list_creatives(self.w.adv, self.w.group.ad_group_id)], [self.w.creative.creative_id, second.creative_id])
        self.assertEqual(api.get_ad_group(self.w.adv, self.w.group.ad_group_id).creative_id, self.w.creative.creative_id)

    def case_creative_image_validated_and_urls_https_only(self):
        w = World(with_creative=False)
        gid = w.group.ad_group_id
        for bad in (b"just text, not an image", image_factory.other_format_bytes("GIF"), image_factory.SVG_WITH_SCRIPT):
            with self.assertRaises(ImageRejected):
                api.create_creative(w.adv, gid, "t", "https://a.example", io.BytesIO(bad), now=NOW)
        for url in ("http://a.example", "javascript:alert(1)", "https://user:pw@evil.example/x", "https://a.example/ x", "//a.example", "", "https://", None, "https://a.example/" + "x" * 2100):
            with self.assertRaises(InvalidRequest, msg=url):
                api.create_creative(w.adv, gid, "t", url, png_file(), now=NOW)
        for title in ("", " ", "x" * 201, "a‮b"):
            with self.assertRaises(InvalidRequest, msg=title):
                api.create_creative(w.adv, gid, title, "https://a.example", png_file(), now=NOW)
        self.assertEqual(api.list_creatives(w.adv, gid), [])  # 실패한 시도는 남지 않는다
        c = api.create_creative(w.adv, gid, "정상", "https://a.example/path?q=1", png_file(), now=NOW)
        self.assertTrue(asset_exists(c.image_name))
        self.assertTrue(c.image_name.endswith(".png"))

    def case_update_creative_and_replace_image_keeps_old_file(self):
        cid = self.w.creative.creative_id
        v = api.update_creative(self.w.adv, cid, {"title": "새 제목", "redirect_url": "https://b.example"}, now=NOW)
        self.assertEqual((v.title, v.redirect_url), ("새 제목", "https://b.example"))
        for bad in ({}, {"image_name": "x"}, {"redirect_url": "http://b.example"}, {"enabled": False}):  # 소재 끄기는 없다(6-9b) — 광고를 끈다
            with self.assertRaises(InvalidRequest, msg=bad):
                api.update_creative(self.w.adv, cid, bad, now=NOW)
        old = self.w.creative.image_name
        new = api.replace_creative_image(self.w.adv, cid, png_file((9, 9, 9)), now=NOW)
        self.assertNotEqual(new.image_name, old)
        self.assertTrue(asset_exists(old) and asset_exists(new.image_name))  # 과거 결정이 참조할 수 있어 지우지 않는다

    # 구성별 노출 소재(설계 §10 "구성별 노출 소재", SCR-1): 광고(광고그룹)의 구성마다 나갈 소재를 따로 고른다. 없는 구성은 기본 소재(선택 소재).
    # 시험 수 상한(설계 §10 "시험 구성" ① 운영용 ≤ 300) 안에서 이 클래스의 경우로 둔다 — 준비는 경우마다 slot_creative_world() 로
    def slot_creative_world(self):
        """이 광고에 구성 둘(World 의 구성 + other), 옆 광고(입찰 없음 — 후보가 아니다)와 그 기본 소재 '가로'(wide)."""
        self.gid = self.w.group.ad_group_id
        self.other = make_slot(self.w.admin)
        api.update_ad_group(self.w.adv, self.gid, {"slot_ids": [self.w.slot.slot_id, self.other.slot_id]}, now=NOW)
        self.side = api.create_ad_group(self.w.adv, self.w.campaign.campaign_id, "옆", [self.other.slot_id], now=NOW)  # 입찰 없음 — 후보가 아니다
        self.wide = api.create_creative(self.w.adv, self.side.ad_group_id, "가로", "https://wide.example/landing", png_file((9, 9, 9)), now=NOW)  # 옆 광고의 기본 소재

    def pick(self, slot, creative, at=1):
        return api.select_slot_creative(self.w.adv, self.gid, slot.slot_id, creative and creative.creative_id, now=NOW + at * SEC)

    def shown(self, slot):
        [f] = api.candidate_facts_for_slot(slot.slot_id)
        return (f.creative_id, f.title, f.redirect_url, f.image_name)

    def case_each_slot_shows_its_own_creative_and_clearing_returns_to_the_default(self):
        self.slot_creative_world()
        w = self.w
        base = (w.creative.creative_id, w.creative.title, w.creative.redirect_url, w.creative.image_name)
        self.assertEqual((self.shown(w.slot), self.shown(self.other)), (base, base))
        view = self.pick(self.other, self.wide)
        self.assertEqual((view.creative_id, view.slot_creative_ids, view.updated_at),
                         (w.creative.creative_id, ((self.other.slot_id, self.wide.creative_id),), NOW + SEC))  # 기본 소재는 그대로, 광고 변경 시각은 오른다
        with self.assertNumQueries(1):  # 후보 조회는 그대로 한 번(①)
            facts = api.candidate_facts_for_slot(self.other.slot_id)
        self.assertEqual([(f.ad_group_id, f.creative_id, f.title, f.redirect_url, f.image_name) for f in facts],
                         [(self.gid, self.wide.creative_id, "가로", self.wide.redirect_url, self.wide.image_name)])
        self.assertEqual(self.shown(w.slot), base)  # 지정하지 않은 구성은 기본 소재
        self.assertIn(self.wide.creative_id, [c.creative_id for c in api.list_creatives(w.adv, self.gid)])  # 고르면 연결도 함께(O9)
        self.assertEqual(api.get_ad_group(w.adv, self.gid), view)
        self.assertEqual([g for g in api.list_ad_groups(w.adv, w.campaign.campaign_id) if g.ad_group_id == self.gid], [view])  # 목록도 같은 값
        cleared = self.pick(self.other, None, at=2)
        self.assertEqual((cleared.creative_id, cleared.slot_creative_ids, self.shown(self.other)), (w.creative.creative_id, (), base))

    def case_a_change_is_one_log_row_one_event_and_one_audit_and_a_repeat_writes_nothing(self):
        self.slot_creative_world()
        self.pick(self.other, self.wide)
        self.pick(self.other, self.wide, at=2)  # 그대로 — 아무것도 쓰지 않는다
        self.pick(self.other, None, at=3)
        rows = list(ChangeLog.objects.filter(target_id=self.gid, change_type="SLOT_CREATIVE_SELECTED").order_by("changed_at"))
        s, c = str(self.other.slot_id), str(self.wide.creative_id)
        self.assertEqual([(r.changed_at, r.previous_value, r.new_value) for r in rows], [
            (NOW + SEC, {"slot_id": s, "creative_id": None}, {"slot_id": s, "creative_id": c}),
            (NOW + 3 * SEC, {"slot_id": s, "creative_id": c}, {"slot_id": s, "creative_id": None}),  # 지움 = 기본 소재로
        ])
        for r in rows:
            e = EventOutbox.objects.get(pk=event_id_for("ad.ad_group_changed", r.change_id))
            self.assertEqual((e.payload["change_type"], e.payload["previous_value"], e.payload["new_value"]), ("SLOT_CREATIVE_SELECTED", r.previous_value, r.new_value))
        audits = AuditLog.objects.filter(action="ad_group.slot_creative_selected").order_by("created_at")
        self.assertEqual([(a.target_id, a.detail) for a in audits], [(str(self.gid), {"slot_id": s, "creative_id": c}), (str(self.gid), {"slot_id": s, "creative_id": None})])
        self.assertEqual(api.get_ad_group(self.w.adv, self.gid).updated_at, NOW + 3 * SEC)

    def case_slot_creative_refusals_change_nothing(self):
        self.slot_creative_world()
        w, stranger = self.w, World()
        gone = api.create_creative(w.adv, self.side.ad_group_id, "지운 것", "https://gone.example/", png_file((8, 8, 8)), now=NOW)
        api.archive_creative(w.adv, gone.creative_id, now=NOW)
        call = lambda gid, slot_id, creative_id: api.select_slot_creative(w.adv, gid, slot_id, creative_id, now=NOW + SEC)  # noqa: E731
        for args in ((self.gid, self.other.slot_id, stranger.creative.creative_id), (self.gid, self.other.slot_id, gone.creative_id),
                     (self.gid, self.other.slot_id, "not-a-uuid"), (stranger.group.ad_group_id, stranger.slot.slot_id, None)):
            with self.assertRaises(Http404, msg=args):  # 남의 소재·지운 소재·형식이 틀린 소재·남의 광고
                call(*args)
        for slot_id in (stranger.slot.slot_id, "not-a-uuid", None):
            with self.assertRaises(InvalidRequest, msg=slot_id) as caught:  # 이 광고의 구성이 아니다
                call(self.gid, slot_id, self.wide.creative_id)
            self.assertEqual(caught.exception.errors[0]["pointer"], "/slot_id")
        with self.assertRaises(Forbidden):
            api.select_slot_creative(w.admin, self.gid, self.other.slot_id, None, now=NOW)
        api.end_campaign(w.adv, w.campaign.campaign_id, now=NOW + SEC)
        with self.assertRaises(api.CampaignEnded):
            call(self.gid, self.other.slot_id, self.wide.creative_id)
        self.assertEqual(api.get_ad_group(w.adv, self.gid).slot_creative_ids, ())
        self.assertFalse(ChangeLog.objects.filter(change_type="SLOT_CREATIVE_SELECTED").exists())
        self.assertNotIn(self.wide.creative_id, [c.creative_id for c in api.list_creatives(w.adv, self.gid)])  # 연결도 생기지 않았다

    def case_a_creative_used_by_a_slot_counts_as_in_use(self):
        """④: 구성별로만 쓰는 소재도 "쓰는 소재"다 — 보관 불가, 소재 목록 "사용 중", 운영 모니터링 "선택됨". 구성을 빼면 지정도 사라진다."""
        self.slot_creative_world()
        w = self.w
        only = api.create_creative(w.adv, self.side.ad_group_id, "구성 전용", "https://only.example/", png_file((7, 7, 7)), now=NOW)  # 옆 광고는 다른 기본 소재
        self.assertTrue(next(o for o in api.list_owned_creatives(w.adv, now=NOW) if o.creative.creative_id == only.creative_id).deletable)
        self.pick(self.other, only)
        self.pick(w.slot, only, at=2)
        owned = next(o for o in api.list_owned_creatives(w.adv, now=NOW) if o.creative.creative_id == only.creative_id)
        self.assertEqual(([(u.ad_group_id, u.selected) for u in owned.uses], owned.in_use), ([(self.side.ad_group_id, False), (self.gid, True)], 1))
        rows = api.monitoring_list(w.admin, now=NOW + 3 * SEC)
        [row] = [r for r in rows[0:rows.count()] if r.campaign.campaign_id == w.campaign.campaign_id]
        self.assertIn(only.creative_id, row.selected_creative_ids)
        with self.assertRaises(Conflict):
            api.archive_creative(w.adv, only.creative_id, now=NOW + 3 * SEC)
        view = api.update_ad_group(w.adv, self.gid, {"slot_ids": [w.slot.slot_id]}, now=NOW + 4 * SEC)  # 남은 구성은 지정을 지킨다
        self.assertEqual(view.slot_creative_ids, ((w.slot.slot_id, only.creative_id),))
        api.update_ad_group(w.adv, self.gid, {"slot_ids": [w.slot.slot_id, self.other.slot_id]}, now=NOW + 5 * SEC)  # 다시 넣은 구성은 기본 소재로
        self.assertEqual(api.get_ad_group(w.adv, self.gid).slot_creative_ids, ((w.slot.slot_id, only.creative_id),))
        self.pick(w.slot, None, at=6)
        api.archive_creative(w.adv, only.creative_id, now=NOW + 7 * SEC)  # 아무도 쓰지 않으면 보관된다

    def case_without_a_default_only_the_chosen_slot_carries_the_ad(self):
        """⑥: 기본 소재가 없어도 구성 지정이 있으면 그 구성의 후보다. 고르기는 기본 소재를 정하지 않는다."""
        w = World(with_creative=False)
        other = make_slot(w.admin)
        api.update_ad_group(w.adv, w.group.ad_group_id, {"slot_ids": [w.slot.slot_id, other.slot_id]}, now=NOW)
        side = api.create_ad_group(w.adv, w.campaign.campaign_id, "옆", [other.slot_id], now=NOW)
        c = api.create_creative(w.adv, side.ad_group_id, "구성 전용", "https://only.example/", png_file(), now=NOW)
        view = api.select_slot_creative(w.adv, w.group.ad_group_id, other.slot_id, c.creative_id, now=NOW)
        self.assertIsNone(view.creative_id)
        self.assertEqual([(f.ad_group_id, f.creative_id) for f in api.candidate_facts_for_slot(other.slot_id)], [(w.group.ad_group_id, c.creative_id)])
        self.assertEqual(api.candidate_facts_for_slot(w.slot.slot_id), [])

    # ---------------------------------------------------------------- C8 내부 입력 API(ACW-3, 설계 §10 "내부 입력 API(C8)") — 쓰기

    def case_c8_categories_create_change_or_leave_each_ad_in_order(self):
        """② 항목마다 보낸 순서대로: 줄이 없는 광고(0005 전)는 보낸 값 + 가중치 0·0·0 으로 판 1(created), 값이 다르면 판 + 1(changed), 같으면 아무것도 쓰지 않음(unchanged).
        만들거나 바꾼 항목마다 판 표(O13) 한 줄과 이벤트 한 건이고 O12 = O13 의 최대 판이다. 같은 광고는 대·소문자가 달라도 같은 광고다."""
        w, client = self.w, internal_client(self.w.admin)
        bare, g = bare_ad_group(w.campaign.campaign_id, w.creative.creative_id), w.group.ad_group_id
        later = NOW + SEC
        out = api.write_categories(client, [{"ad_group_id": str(bare.pk), "category": 3}, {"ad_group_id": str(g), "category": 5}], now=later)
        self.assertEqual([(r.ad_group_id, r.result, r.profile.version, r.profile.category, r.profile.changed_at) for r in out],
                         [(bare.pk, "created", 1, 3, later), (g, "changed", 2, 5, later)])
        p = AdGroupProfile.objects.get(ad_group=bare)
        self.assertEqual((p.weight_mlo, p.weight_aio, p.weight_skn, p.created_at, p.changed_at), (0, 0, 0, later, later))
        again = api.write_categories(client, [{"ad_group_id": str(g).upper(), "category": 5}], now=later + SEC)
        self.assertEqual([(r.result, r.profile.version, r.profile.changed_at) for r in again], [("unchanged", 2, later)])
        versions = list(AdGroupProfileVersion.objects.order_by("ad_group_id", "version").values_list("ad_group_id", "version", "category", "changed_at"))
        self.assertEqual(sorted(versions), sorted([(g, 1, None, NOW), (g, 2, 5, later), (bare.pk, 1, 3, later)]))
        for row in AdGroupProfile.objects.all():  # 지금 줄 = 판 표의 최대 판(네 값까지)
            top = AdGroupProfileVersion.objects.filter(ad_group_id=row.ad_group_id).order_by("-version").first()
            self.assertEqual((top.version, top.category, top.weight_mlo, top.weight_aio, top.weight_skn, top.changed_at),
                             (row.version, row.category, row.weight_mlo, row.weight_aio, row.weight_skn, row.changed_at))
        events = EventOutbox.objects.filter(event_name="ad.ad_group_profile_changed")
        self.assertEqual(sorted(events.values_list("event_id", flat=True)),
                         sorted(event_id_for("ad.ad_group_profile_changed", f"{a}/{v}") for a, v, _c, _t in versions))
        self.assertEqual(events.get(event_id=event_id_for("ad.ad_group_profile_changed", f"{g}/2")).payload,
                         {"ad_group_id": str(g), "version": 2, "category": 5, "weight_mlo": 0, "weight_aio": 0, "weight_skn": 0, "changed_at": later.isoformat()})
        self.assertEqual(AdGroup.objects.get(pk=g).updated_at, NOW)  # 광고그룹 행은 잠그기만 하고 쓰지 않는다

    def case_c8_weights_write_leaves_the_category(self):
        """④ 는 가중치만, ② 는 카테고리만 바꾼다 — 서로 덮지 않는다(사용자 2026-11-12). 줄이 없는 광고는 ④ 에서 카테고리 NULL 로 판 1."""
        w, client = self.w, internal_client(self.w.admin)
        bare, g = bare_ad_group(w.campaign.campaign_id, w.creative.creative_id), w.group.ad_group_id
        api.write_categories(client, [{"ad_group_id": str(g), "category": 7}], now=NOW + SEC)
        out = api.write_weights(client, [{"ad_group_id": str(g), "weight_mlo": 50, "weight_aio": 30, "weight_skn": 20},
                                         {"ad_group_id": str(bare.pk), "weight_mlo": 0, "weight_aio": 100, "weight_skn": 0}], now=NOW + 2 * SEC)
        self.assertEqual([(r.result, r.profile.version, r.profile.category, r.profile.weight_mlo, r.profile.weight_aio, r.profile.weight_skn) for r in out],
                         [("changed", 3, 7, 50, 30, 20), ("created", 1, None, 0, 100, 0)])
        api.write_categories(client, [{"ad_group_id": str(g), "category": 9}], now=NOW + 3 * SEC)
        p = AdGroupProfile.objects.get(ad_group_id=g)
        self.assertEqual((p.version, p.category, p.weight_mlo, p.weight_aio, p.weight_skn), (4, 9, 50, 30, 20))  # 카테고리 쓰기가 가중치를 되돌리지 않는다
        same = api.write_weights(client, [{"ad_group_id": str(g), "weight_mlo": 50, "weight_aio": 30, "weight_skn": 20}], now=NOW + 4 * SEC)
        self.assertEqual([(r.result, r.profile.version) for r in same], [("unchanged", 4)])
        self.assertEqual(AdGroupProfileVersion.objects.filter(ad_group_id=g).count(), 4)

    def case_c8_a_batch_is_audited_once_by_the_key_owner(self):
        """감사는 묶음 한 번에 한 줄: 행위자 = 키의 주인 운영자, 대상 없음, `detail` 은 키 ID·이름과 항목 수·결과별 수. 모두 unchanged 여도, 빈 묶음이어도 한 줄.
        키(평문)·해시는 남지 않는다."""
        w = self.w
        issued = masters.issue_internal_key(w.admin, "분류 모듈", now=NOW)
        client = masters.authenticate_internal_key(issued.key)
        bare, g = bare_ad_group(w.campaign.campaign_id, w.creative.creative_id), w.group.ad_group_id
        other = api.create_ad_group(w.adv, w.campaign.campaign_id, "다른 광고", [w.slot.slot_id], now=NOW).ad_group_id
        api.write_categories(client, [{"ad_group_id": str(bare.pk), "category": 1}, {"ad_group_id": str(g), "category": 2},
                                      {"ad_group_id": str(other), "category": 2}], now=NOW + SEC)
        api.write_categories(client, [{"ad_group_id": str(g), "category": 2}], now=NOW + 2 * SEC)
        written = api.write_weights(client, [], now=NOW + 3 * SEC)
        self.assertEqual(written, [])
        logs = list(AuditLog.objects.filter(action__in=["ad_group.categories_written", "ad_group.weights_written"]).order_by("created_at", "pk"))
        base = {"client_id": str(issued.key_id), "client_label": "분류 모듈"}
        self.assertEqual([(a.action, a.actor_id, a.actor_username, a.target_type, a.target_id, a.detail) for a in logs], [
            ("ad_group.categories_written", w.admin.pk, w.admin.username, "", "", {**base, "items": 3, "created": 1, "changed": 2, "unchanged": 0}),
            ("ad_group.categories_written", w.admin.pk, w.admin.username, "", "", {**base, "items": 1, "created": 0, "changed": 0, "unchanged": 1}),
            ("ad_group.weights_written", w.admin.pk, w.admin.username, "", "", {**base, "items": 0, "created": 0, "changed": 0, "unchanged": 0}),
        ])
        secret = issued.key.split(".", 1)[1]
        self.assertNotIn(secret, str(list(AuditLog.objects.values_list("detail", flat=True))))
        self.assertEqual(AdGroupProfileVersion.objects.count(), 5)  # World·다른 광고의 판 1 둘 + 바뀐 둘 + 만든 하나(같은 값·빈 묶음은 아무것도 쓰지 않는다)

    def case_c8_a_batch_with_any_error_writes_nothing(self):
        """한 트랜잭션·전부 거절(사용자 2026-11-12): 형식·범위·합·같은 광고 두 번은 모든 항목에서 틀린 곳을 **모두** 모아 400, DB 를 열지 않는다.
        형식이 맞으면 없는 광고를 모아 400. 어느 쪽이든 O12·O13·outbox·감사가 그대로다."""
        w, client = self.w, internal_client(self.w.admin)
        g = str(w.group.ad_group_id)
        ids = [str(uuid.uuid4()) for _ in range(9)]

        def state():
            return (list(AdGroupProfile.objects.values_list("ad_group_id", "version", "category", "weight_mlo")), AdGroupProfileVersion.objects.count(),
                    EventOutbox.objects.count(), AuditLog.objects.filter(action__endswith="_written").count())

        before = state()
        categories = [
            "x", {"ad_group_id": g}, {"ad_group_id": ids[0], "category": 1, "extra": 1}, {"ad_group_id": "not-a-uuid", "category": 1},
            {"ad_group_id": g.upper(), "category": 1},
            {"ad_group_id": ids[1], "category": -1}, {"ad_group_id": ids[2], "category": 256}, {"ad_group_id": ids[3], "category": "1"},
            {"ad_group_id": ids[4], "category": True}, {"ad_group_id": ids[5], "category": 1.0}, {"ad_group_id": ids[6], "category": None},
        ]
        with self.assertRaises(InvalidRequest) as e:
            api.write_categories(client, categories, now=NOW)
        self.assertEqual([x["pointer"] for x in e.exception.errors], [
            "/items/0", "/items/1/category", "/items/2/extra", "/items/3/ad_group_id", "/items/4/ad_group_id",
            "/items/5/category", "/items/6/category", "/items/7/category", "/items/8/category", "/items/9/category", "/items/10/category",
        ])
        self.assertEqual(e.exception.errors[4]["message"], "같은 광고가 묶음에 두 번 있습니다.")
        weights = [
            {"ad_group_id": ids[0], "weight_mlo": -1, "weight_aio": 101, "weight_skn": None},
            {"ad_group_id": ids[1], "weight_mlo": True, "weight_aio": 0, "weight_skn": 0},
            {"ad_group_id": ids[2], "weight_mlo": 50, "weight_aio": 50, "weight_skn": 1},
            {"ad_group_id": ids[3], "weight_mlo": 1, "weight_aio": 1},
        ]
        with self.assertRaises(InvalidRequest) as e:
            api.write_weights(client, weights, now=NOW)
        self.assertEqual([x["pointer"] for x in e.exception.errors], [
            "/items/0/weight_mlo", "/items/0/weight_aio", "/items/0/weight_skn", "/items/1/weight_mlo", "/items/2", "/items/3/weight_skn",
        ])
        with self.assertRaises(InvalidRequest) as e:  # 형식은 맞고 둘째·넷째 광고가 없다 — 첫째(있는 광고)도 쓰지 않는다
            api.write_categories(client, [{"ad_group_id": g, "category": 1}, {"ad_group_id": ids[7], "category": 1}, {"ad_group_id": ids[8], "category": 2}], now=NOW)
        self.assertEqual([(x["pointer"], x["message"]) for x in e.exception.errors],
                         [("/items/1/ad_group_id", "광고를 찾을 수 없습니다."), ("/items/2/ad_group_id", "광고를 찾을 수 없습니다.")])
        for bad in ({"items": []}, "x", None, [{"ad_group_id": g, "category": 1}] * 201):
            with self.subTest(type(bad)), self.assertRaises(InvalidRequest) as e:
                api.write_categories(client, bad, now=NOW)
            self.assertEqual([x["pointer"] for x in e.exception.errors], ["/items"])  # 배열이 아니거나 201개 이상이면 위치 하나
        self.assertEqual(state(), before)

    def case_c8_batch_size_limits_and_the_empty_batch(self):
        """묶음은 0~200개(사용자 2026-11-12). 200개는 되고, 빈 묶음은 아무것도 바꾸지 않고 감사 한 줄만(모듈이 돌았다는 기록)."""
        w, client = self.w, internal_client(self.w.admin)
        groups = AdGroup.objects.bulk_create([AdGroup(campaign_id=w.campaign.campaign_id, name=f"광고 {i}", created_at=NOW, updated_at=NOW) for i in range(200)])
        out = api.write_weights(client, [{"ad_group_id": str(x.pk), "weight_mlo": 10, "weight_aio": 10, "weight_skn": 10} for x in groups], now=NOW)
        self.assertEqual({r.result for r in out}, {"created"})
        self.assertEqual(len(out), 200)
        before = (AdGroupProfile.objects.count(), AdGroupProfileVersion.objects.count(), EventOutbox.objects.count())
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(api.write_categories(client, [], now=NOW), [])
        self.assertEqual([q["sql"].split()[:3] for q in queries if not q["sql"].startswith(("SAVEPOINT", "RELEASE SAVEPOINT"))],
                         [["INSERT", "INTO", '"foundation_audit_log"']])  # 감사 한 줄뿐(빈 ID 목록은 DB 에 묻지 않는다)
        self.assertEqual((AdGroupProfile.objects.count(), AdGroupProfileVersion.objects.count(), EventOutbox.objects.count()), before)
        self.assertEqual(AuditLog.objects.filter(action="ad_group.categories_written").get().detail["items"], 0)

    def case_c8_ads_of_ended_campaigns_can_be_written(self):
        """끝난 캠페인의 광고에도 쓴다(설계 §4.5 예외 — 분석·ML 용 값이고 서빙·과금에 쓰지 않는다)."""
        w, client = self.w, internal_client(self.w.admin)
        api.end_campaign(w.adv, w.campaign.campaign_id, now=NOW)
        out = api.write_categories(client, [{"ad_group_id": str(w.group.ad_group_id), "category": 4}], now=NOW + SEC)
        self.assertEqual([(r.result, r.profile.version) for r in out], [("changed", 2)])

    def case_c8_a_key_whose_owner_is_no_longer_an_admin_is_refused(self):
        """키의 주인이 비활성화되거나 관리자가 아니게 되면 읽기·두 쓰기 모두 403 이고 아무것도 쓰지 않는다 — 인증은 주인을 보지 않고 서비스가 본다."""
        w = self.w
        g = str(w.group.ad_group_id)
        for change in ({"is_active": False}, {"role": "advertiser"}):
            owner = make_admin()
            issued = masters.issue_internal_key(owner, "모듈", now=NOW)
            for field, value in change.items():
                setattr(owner, field, value)
            owner.save()
            client = masters.authenticate_internal_key(issued.key)
            self.assertIsNotNone(client)
            with self.subTest(change):
                for call in (lambda: api.profile_sources(client, now=NOW), lambda: api.write_categories(client, [{"ad_group_id": g, "category": 1}], now=NOW),
                             lambda: api.write_weights(client, [], now=NOW)):
                    with self.assertRaises(Forbidden):
                        call()
        self.assertEqual((AdGroupProfileVersion.objects.count(), AuditLog.objects.filter(action__endswith="_written").count()), (1, 0))

    # ---------------------------------------------------------------- C8 — 읽기

    def case_c8_reads_only_live_ads_with_content(self):
        """범위(사용자 2026-11-12): 캠페인이 집행 중·일시 중지이고 기간이 끝나지 않았고 광고주가 활성(C3 와 같은 조건 — 시작 전도 나옴, 광고 OFF 도 나옴)이며,
        내보내는 소재 중 이름과 이동 주소가 **둘 다** 있는 소재가 하나 이상인 광고. 이미지는 기준이 아니다."""
        w, client = self.w, internal_client(self.w.admin)
        listed = lambda: {s.ad_group_id for s in api.profile_sources(client, now=NOW).items}  # noqa: E731
        base = w.group.ad_group_id
        no_creative = api.create_ad_group(w.adv, w.campaign.campaign_id, "소재 없음", [w.slot.slot_id], now=NOW).ad_group_id
        off = ad_with_creative(w, w.campaign.campaign_id, "꺼진 광고", color=(1, 1, 1))
        api.update_ad_group(w.adv, off, {"enabled": False}, now=NOW)
        later = api.create_campaign(w.adv, "시작 전", NOW + DAY, None, 1000, now=NOW)
        not_started = ad_with_creative(w, later.campaign_id, "시작 전 광고", color=(2, 2, 2))
        paused_c = api.create_campaign(w.adv, "일시 중지", NOW - DAY, None, 1000, now=NOW)
        paused = ad_with_creative(w, paused_c.campaign_id, "일시 중지 광고", color=(3, 3, 3))
        api.update_campaign(w.adv, paused_c.campaign_id, {"status": "PAUSED"}, now=NOW)
        self.assertEqual(listed(), {base, off, not_started, paused})
        self.assertNotIn(no_creative, listed())
        # 이름 또는 이동 주소가 공백뿐이면 그 소재는 내용이 없다 — 같은 이미지를 쓰는 다른 소재가 있어도 그 소재의 이름·링크로 본다
        Creative.objects.filter(pk=w.creative.creative_id).update(title="   ")
        self.assertNotIn(base, listed())
        Creative.objects.filter(pk=w.creative.creative_id).update(title="제목", redirect_url=" \t")
        self.assertNotIn(base, listed())
        Creative.objects.filter(pk=w.creative.creative_id).update(redirect_url="https://example.com/landing")
        self.assertIn(base, listed())
        # 캠페인 조건으로 빠지는 광고: 관리자 중단·종료·기간 지남·광고주 비활성
        Campaign.objects.filter(pk=paused_c.campaign_id).update(status="SUSPENDED")
        Campaign.objects.filter(pk=later.campaign_id).update(status="ENDED")
        self.assertEqual(listed(), {base, off})
        self.assertEqual({s.ad_group_id for s in api.profile_sources(client, now=NOW + 11 * DAY).items}, set())  # World 의 캠페인은 NOW + 10일에 끝난다
        w.adv.is_active = False
        w.adv.save()
        self.assertEqual(listed(), set())

    def case_c8_reads_unclassified_ads_including_those_without_a_row(self):
        """① 미분류 = 카테고리 NULL 인 광고와 줄이 없는 광고(0005 전). 분류하면(0 = 기타 포함) 다음 읽기에서 빠진다. ③ 은 같은 모양으로 전부."""
        w, client = self.w, internal_client(self.w.admin)
        bare, g = bare_ad_group(w.campaign.campaign_id, w.creative.creative_id), w.group.ad_group_id
        unclassified = lambda: [s.ad_group_id for s in api.profile_sources(client, unclassified=True, now=NOW).items]  # noqa: E731
        self.assertEqual(unclassified(), sorted([bare.pk, g]))
        page = {s.ad_group_id: s for s in api.profile_sources(client, now=NOW).items}
        self.assertIsNone(page[bare.pk].profile)
        self.assertEqual((page[g].profile.version, page[g].profile.category), (1, None))
        api.write_categories(client, [{"ad_group_id": str(g), "category": 0}], now=NOW)
        self.assertEqual(unclassified(), [bare.pk])
        api.write_weights(client, [{"ad_group_id": str(bare.pk), "weight_mlo": 1, "weight_aio": 0, "weight_skn": 0}], now=NOW)
        self.assertEqual(unclassified(), [bare.pk])  # 가중치만 들어온 광고는 아직 분류 전
        api.write_categories(client, [{"ad_group_id": str(bare.pk), "category": 2}], now=NOW)
        self.assertEqual(unclassified(), [])
        self.assertEqual(sorted(s.ad_group_id for s in api.profile_sources(client, now=NOW).items), sorted([bare.pk, g]))

    def case_c8_read_creatives_are_default_first_then_by_id(self):
        """소재 목록: 기본 소재(내용이 있을 때) 다음 구성별 소재를 소재 ID 순으로, 같은 소재는 한 번. 연결만 된 소재는 나오지 않는다. 기본 소재가 없는 광고도 구성별 소재로 나온다."""
        w, client = self.w, internal_client(self.w.admin)
        s2, s3 = make_slot(w.admin), make_slot(w.admin)
        g = api.create_ad_group(w.adv, w.campaign.campaign_id, "구성 셋", [w.slot.slot_id, s2.slot_id, s3.slot_id], now=NOW).ad_group_id
        default = api.create_creative(w.adv, g, "기본", "https://example.com/d", png_file((10, 10, 10)), now=NOW).creative_id
        a = api.create_creative(w.adv, None, "가", "https://example.com/a", png_file((11, 11, 11)), now=NOW).creative_id
        b = api.create_creative(w.adv, None, "나", "https://example.com/b", png_file((12, 12, 12)), now=NOW).creative_id
        linked = api.create_creative(w.adv, None, "연결만", "https://example.com/l", png_file((13, 13, 13)), now=NOW).creative_id
        api.link_creative(w.adv, g, linked, now=NOW)
        for slot, creative in ((w.slot, b), (s2, a), (s3, default)):
            api.select_slot_creative(w.adv, g, slot.slot_id, creative, now=NOW)
        item = {s.ad_group_id: s for s in api.profile_sources(client, now=NOW).items}[g]
        self.assertEqual([c.creative_id for c in item.creatives], [default, *sorted([a, b])])
        c = item.creatives[0]
        self.assertEqual((c.title, c.redirect_url), ("기본", "https://example.com/d"))
        no_default = api.create_ad_group(w.adv, w.campaign.campaign_id, "기본 없음", [w.slot.slot_id], now=NOW).ad_group_id
        api.select_slot_creative(w.adv, no_default, w.slot.slot_id, a, now=NOW)
        item = {s.ad_group_id: s for s in api.profile_sources(client, now=NOW).items}[no_default]
        self.assertEqual([c.creative_id for c in item.creatives], [a])
        Creative.objects.filter(pk=default).update(title="")  # 기본 소재에 내용이 없으면 구성별 소재만
        item = {s.ad_group_id: s for s in api.profile_sources(client, now=NOW).items}[g]
        self.assertEqual([c.creative_id for c in item.creatives], sorted([a, b]))

    def case_c8_read_pages_follow_the_id_cursor(self):
        """광고 ID 오름차순 커서: `limit` 1~200(그 밖은 400 /limit), `after` 다음부터, 페이지가 차지 않으면 `next_after` None. 훑는 사이 만든 광고(ID 가 더 큼)는 뒤 페이지에 나오고
        어떤 광고도 두 번 나오지 않는다."""
        w, client = self.w, internal_client(self.w.admin)
        for i in range(3):
            ad_with_creative(w, w.campaign.campaign_id, f"광고 {i}", color=(20 + i, 0, 0))
        everything = [s.ad_group_id for s in api.profile_sources(client, now=NOW).items]
        self.assertEqual(everything, sorted(everything))
        first = api.profile_sources(client, limit=2, now=NOW)
        self.assertEqual(([s.ad_group_id for s in first.items], first.next_after), (everything[:2], everything[1]))
        newer = ad_with_creative(w, w.campaign.campaign_id, "훑는 중에", color=(30, 0, 0))
        seen, after = [s.ad_group_id for s in first.items], first.next_after
        while after is not None:
            page = api.profile_sources(client, after=after, limit=2, now=NOW)
            seen += [s.ad_group_id for s in page.items]
            after = page.next_after
        self.assertEqual(seen, everything + [newer])
        self.assertEqual(len(api.profile_sources(client, limit=200, now=NOW).items), 5)
        tail = api.profile_sources(client, limit=1, after=newer, now=NOW)
        self.assertEqual((tail.items, tail.next_after), ((), None))
        for bad in (0, 201, True, "5", None):
            with self.subTest(bad), self.assertRaises(InvalidRequest) as e:
                api.profile_sources(client, limit=bad, now=NOW)
            self.assertEqual([x["pointer"] for x in e.exception.errors], ["/limit"])

    def case_c8_read_query_count_does_not_grow_with_ads(self):
        """질의는 광고 한 번 + 구성별 소재 한 번 — 광고 하나일 때와 구성별 소재가 있는 광고 여럿일 때 같다."""
        w, client = self.w, internal_client(self.w.admin)
        with CaptureQueriesContext(connection) as one:
            self.assertEqual(len(api.profile_sources(client, now=NOW).items), 1)
        for i in range(4):
            g = ad_with_creative(w, w.campaign.campaign_id, f"광고 {i}", color=(40 + i, 0, 0))
            extra = api.create_creative(w.adv, None, f"구성 {i}", "https://example.com/s", png_file((50 + i, 0, 0)), now=NOW).creative_id
            api.select_slot_creative(w.adv, g, w.slot.slot_id, extra, now=NOW)
        bare_ad_group(w.campaign.campaign_id, w.creative.creative_id)
        with CaptureQueriesContext(connection) as many:
            page = api.profile_sources(client, now=NOW)
        self.assertEqual(len(page.items), 6)
        self.assertEqual(sum(len(s.creatives) for s in page.items), 1 + 4 * 2 + 1)
        self.assertEqual(len(many), len(one))
        self.assertEqual(len(one), 2)

    def case_c8_reads_the_same_campaign_condition_as_public_images(self):
        """캠페인 조건은 C3 이미지 공개(`is_asset_servable`)와 같은 Q 하나다: 같은 광고 세계에서 목록에 나온 광고의 소재 이미지는 모두 열리고,
        캠페인 조건으로 빠진 광고만 쓰는 이미지는 열리지 않는다. 내용 조건으로만 빠진 광고는 이 대조에서 뺀다(C3 는 내용 조건을 보지 않는다)."""
        w, client = self.w, internal_client(self.w.admin)
        worlds = {}
        for i, (status, starts, ends) in enumerate((("ACTIVE", NOW - DAY, None), ("PAUSED", NOW - DAY, NOW + DAY), ("ACTIVE", NOW + DAY, None),
                                                    ("SUSPENDED", NOW - DAY, None), ("ENDED", NOW - DAY, None), ("ACTIVE", NOW - 2 * DAY, NOW - DAY))):
            c = api.create_campaign(w.adv, f"캠페인 {i}", NOW - 3 * DAY, None, 1000, now=NOW - 3 * DAY)
            g = ad_with_creative(w, c.campaign_id, f"광고 {i}", color=(60 + i, 0, 0))
            Campaign.objects.filter(pk=c.campaign_id).update(status=status, starts_at=starts, ends_at=ends)
            worlds[g] = AdGroup.objects.get(pk=g).creative.image_name
        listed = {s.ad_group_id: s for s in api.profile_sources(client, now=NOW).items}
        for g, image in worlds.items():
            self.assertEqual(g in listed, api.is_asset_servable(image, now=NOW), g)
            if g in listed:
                self.assertTrue(all(api.is_asset_servable(c.image_name, now=NOW) for c in listed[g].creatives))
        self.assertEqual(sum(g in listed for g in worlds), 3)

    def test_campaign_service(self):
        self.run_cases()


@tag("security")
class ImageHoldingTests(Cases, TestCase):
    """이전 이미지는 지우지 않으므로, 교체를 반복해도 광고주가 **보유한 파일 총량**이 상한을 넘지 못한다."""

    def distinct_images(self, n):
        return [png_file((i * 9 % 256, i * 17 % 256, 100 + i)) for i in range(1, n + 1)]

    def held_bytes(self, user):
        from campaigns.models import CreativeImage

        return sum(dict(CreativeImage.objects.filter(creative__owner=user).values_list("image_name", "image_bytes")).values())

    def case_repeated_image_replacement_cannot_bypass_the_quota(self):
        w = World()
        limit = w.creative.image_bytes + w.creative.image_bytes // 2  # 현재 이미지 + 절반
        accepted = 0
        with override_settings(MAX_UPLOAD_BYTES_PER_ADVERTISER=limit):
            for image in self.distinct_images(6):
                try:
                    api.replace_creative_image(w.adv, w.creative.creative_id, image, now=NOW)
                    accepted += 1
                except api.LimitExceeded:
                    pass
        self.assertEqual(accepted, 0)  # 첫 교체부터 (현재 + 새 이미지) 가 상한을 넘는다
        self.assertLessEqual(self.held_bytes(w.adv), limit)

    def case_replacements_up_to_the_limit_then_rejected_and_rejected_uploads_leave_no_file(self):
        w = World()
        size = w.creative.image_bytes
        with override_settings(MAX_UPLOAD_BYTES_PER_ADVERTISER=size * 3 + size // 2):
            results = []
            for image in self.distinct_images(6):
                try:
                    api.replace_creative_image(w.adv, w.creative.creative_id, image, now=NOW)
                    results.append("ok")
                except api.LimitExceeded:
                    results.append("limit")
        self.assertIn("limit", results)
        self.assertLessEqual(self.held_bytes(w.adv), size * 3 + size // 2)  # 보유량은 어떤 순서로도 상한을 넘지 않는다
        current = api.get_creative(w.adv, w.creative.creative_id)
        rejected_name = None
        with override_settings(MAX_UPLOAD_BYTES_PER_ADVERTISER=self.held_bytes(w.adv)):
            from assets.api import validate_image

            probe = png_file((250, 1, 2))
            image = validate_image(probe)
            probe.seek(0)
            with self.assertRaises(api.LimitExceeded):
                api.replace_creative_image(w.adv, w.creative.creative_id, probe, now=NOW)
            rejected_name = image.name
        self.assertFalse(asset_exists(rejected_name))  # 거절된 업로드는 파일을 남기지 않는다
        self.assertEqual(api.get_creative(w.adv, w.creative.creative_id).image_name, current.image_name)

    def case_reuploading_a_file_already_held_adds_nothing(self):
        w = World()
        limit = w.creative.image_bytes  # 딱 현재 이미지 하나만큼
        with override_settings(MAX_UPLOAD_BYTES_PER_ADVERTISER=limit):
            same = api.replace_creative_image(w.adv, w.creative.creative_id, png_file(), now=NOW)  # World 와 같은 이미지
        self.assertEqual(same.image_name, w.creative.image_name)
        self.assertEqual(self.held_bytes(w.adv), limit)

    def case_other_advertisers_quota_is_independent(self):
        w, other = World(), World()
        with override_settings(MAX_UPLOAD_BYTES_PER_ADVERTISER=w.creative.image_bytes):
            with self.assertRaises(api.LimitExceeded):
                api.replace_creative_image(w.adv, w.creative.creative_id, png_file((7, 7, 7)), now=NOW)
            self.assertIsNotNone(api.replace_creative_image(other.adv, other.creative.creative_id, png_file(), now=NOW))  # 같은 이미지는 그대로

    def case_old_image_stays_servable_after_replacement_until_the_campaign_stops(self):
        """과거 결정의 이미지 URL 이 교체 직후 404 가 되면 매체의 광고가 깨진다. 단 중단·종료는 이전 이미지에도 적용된다."""
        from records import api as records

        w = World(ends_in=None)  # /assets/ 는 실제 시계로 판정한다 — 기간이 있으면 그 끝(실제 날짜)부터 이 시험이 깨진다(6-16b)
        decision = records.decide(w.slot.media_id, w.slot.slot_id, subject_id=SUBJECT, now=NOW)
        old_url_name = decision.chosen.image_name
        api.replace_creative_image(w.adv, w.creative.creative_id, png_file((1, 200, 3)), now=NOW)
        new_name = api.get_creative(w.adv, w.creative.creative_id).image_name
        self.assertNotEqual(old_url_name, new_name)
        with override_settings(ASSET_SERVABLE_CHECK="campaigns.api.is_asset_servable"):
            client = Client()
            for name in (old_url_name, new_name):
                self.assertEqual(client.get(f"/assets/{name}").status_code, 200, name)  # 과거 결정의 URL 도 열린다
            api.suspend_campaign(w.admin, w.campaign.campaign_id, "부적절")
            for name in (old_url_name, new_name):
                self.assertEqual(client.get(f"/assets/{name}").status_code, 404, name)  # 중단은 이전 이미지에도
            api.release_campaign(w.admin, w.campaign.campaign_id)
            self.assertEqual(client.get(f"/assets/{old_url_name}").status_code, 200)
            api.end_campaign(w.adv, w.campaign.campaign_id)
            for name in (old_url_name, new_name):
                self.assertEqual(client.get(f"/assets/{name}").status_code, 404, name)
        self.assertFalse(api.is_asset_servable(old_url_name, now=NOW + 11 * DAY))

    def case_one_ad_cannot_fill_the_period_and_another_the_status(self):
        """`is_asset_servable` 의 캠페인 조건은 연결 하나에 함께 걸린다(`_servable_campaign_q` 를 한 filter() 안에): **소재 하나**가 두 광고에 연결되어 있고, A 는 집행 중이지만
        기간이 지났고 B 는 종료(`ENDED`)이며 끝 시각이 없다 — 조건을 나눠 거르면 B 의 연결이 기간을, A 의 연결이 상태를 채워 열린다(이미지 보유 행 하나에 연결 둘).
        같은 세계에서 C8 읽기에도 둘 다 나오지 않는다."""
        w = World()
        image, later = w.creative.image_name, NOW + 2 * DAY
        Campaign.objects.filter(pk=w.campaign.campaign_id).update(ends_at=NOW + DAY)  # A: 집행 중, 기간은 NOW + 1일에 끝남
        ended = api.create_campaign(w.adv, "끝남", NOW - DAY, None, 1000, now=NOW)
        b = api.create_ad_group(w.adv, ended.campaign_id, "B", [w.slot.slot_id], now=NOW).ad_group_id
        api.link_creative(w.adv, b, w.creative.creative_id, now=NOW)  # 같은 소재를 B 에도(끝나기 전에 연결)
        Campaign.objects.filter(pk=ended.campaign_id).update(status="ENDED", ends_at=None)
        self.assertTrue(api.is_asset_servable(image, now=NOW))  # 그때는 A 가 둘 다 채운다
        self.assertFalse(api.is_asset_servable(image, now=later))
        self.assertEqual(api.profile_sources(internal_client(w.admin), now=later).items, ())

    def test_image_holding(self):
        self.run_cases()


class InputHardeningTests(Cases, TestCase):
    def setUp(self):
        self.w = World(with_creative=False)

    def create(self, url):
        return api.create_creative(self.w.adv, self.w.group.ad_group_id, "t", url, png_file(), now=NOW)

    def case_malformed_urls_are_input_errors_not_exceptions(self):
        for url in ("https://[", "https://example.com:bogus/", "https://example.com:99999/", "https://example.com:0/", "https://[::1", "https://exa mple.com", "https://", "https:///path"):
            with self.assertRaises(InvalidRequest, msg=url):
                self.create(url)
        self.assertEqual(api.list_creatives(self.w.adv, self.w.group.ad_group_id), [])  # 실패한 시도는 남지 않는다
        c = self.create("https://example.com:8443/path")
        self.assertEqual(c.redirect_url, "https://example.com:8443/path")

    def case_bad_url_on_update_changes_nothing(self):
        c = self.create("https://example.com/ok")
        for url in ("https://[", "https://example.com:bogus/", "https://example.com:99999/"):
            with self.assertRaises(InvalidRequest, msg=url):
                api.update_creative(self.w.adv, c.creative_id, {"title": "바뀌면 안 됨", "redirect_url": url}, now=NOW)
        after = api.get_creative(self.w.adv, c.creative_id)
        self.assertEqual((after.title, after.redirect_url), ("t", "https://example.com/ok"))

    def case_out_of_range_datetimes_are_input_errors(self):
        from datetime import datetime, timedelta, timezone

        too_early = datetime.min.replace(tzinfo=timezone(timedelta(hours=9)))  # UTC 로 바꾸면 표현 범위를 벗어난다
        too_late = datetime.max.replace(tzinfo=timezone(timedelta(hours=-9)))
        for bad in (too_early, too_late):
            with self.assertRaises(InvalidRequest, msg=bad):
                api.create_campaign(self.w.adv, "시각 시험", bad, None, 100, now=NOW)
            with self.assertRaises(InvalidRequest, msg=bad):
                api.update_campaign(self.w.adv, self.w.campaign.campaign_id, {"ends_at": bad}, now=NOW)
        self.assertEqual(len(api.list_campaigns(self.w.adv, now=NOW)), 1)  # 만들어지거나 바뀐 것이 없다

    def test_input_hardening(self):
        self.run_cases()


@tag("security")
class CampaignOwnershipTests(TestCase):
    def test_other_users_objects_are_404(self):
        w = World()
        intruder = make_advertiser()
        cid, gid, crid = w.campaign.campaign_id, w.group.ad_group_id, w.creative.creative_id
        calls = [
            lambda: api.get_campaign(intruder, cid, now=NOW),
            lambda: api.update_campaign(intruder, cid, {"name": "x"}, now=NOW),
            lambda: api.end_campaign(intruder, cid, now=NOW),
            lambda: api.create_review_request(intruder, cid, "m", now=NOW),
            lambda: api.list_review_requests(intruder, cid),
            lambda: api.list_ad_groups(intruder, cid),
            lambda: api.create_ad_group(intruder, cid, "g", [w.slot.slot_id], now=NOW),
            lambda: api.get_ad_group(intruder, gid),
            lambda: api.update_ad_group(intruder, gid, {"name": "x"}, now=NOW),
            lambda: api.add_bid(intruder, gid, 990, expected_version=1, now=NOW),
            lambda: api.list_bids(intruder, gid),
            lambda: api.create_creative(intruder, gid, "t", "https://a.example", png_file(), now=NOW),
            lambda: api.list_creatives(intruder, gid),
            lambda: api.get_creative(intruder, crid),
            lambda: api.update_creative(intruder, crid, {"title": "x"}, now=NOW),
            lambda: api.replace_creative_image(intruder, crid, png_file(), now=NOW),
        ]
        for call in calls:
            with self.assertRaises(Http404):
                call()
        for call in (lambda: api.get_campaign(w.adv, "not-a-uuid", now=NOW), lambda: api.get_creative(w.adv, uuid.uuid4())):
            with self.assertRaises(Http404):  # 형식이 틀린 ID·없는 ID 도 같은 404
                call()
        self.assertEqual(api.list_campaigns(intruder, now=NOW), [])
        self.assertEqual(api.get_campaign(w.adv, cid, now=NOW).name, "캠페인")  # 주인의 데이터는 그대로
        self.assertEqual(api.get_ad_group(w.adv, gid).current_bid.amount, 300)


class CandidateFactsTests(Cases, TestCase):
    def case_candidate_facts_single_query_and_structure_only(self):
        w = World()
        slot_id = w.slot.slot_id
        second = World()  # 같은 슬롯을 고르지 않는 다른 광고주
        def group(name, **kw):
            return api.create_ad_group(w.adv, w.campaign.campaign_id, name, [slot_id], **kw, now=NOW)
        group("소재 없는 그룹", bid_amount=450)  # 같은 슬롯·입찰이 있어도 소재가 없으면 후보가 아니다
        with self.assertNumQueries(1):
            facts = api.candidate_facts_for_slot(slot_id)
        self.assertEqual(len(facts), 1)  # 소재까지 갖춘 광고그룹은 World 의 것 하나
        f = facts[0]
        self.assertEqual((f.ad_group_id, f.bid_amount, f.bid_version, f.campaign_status, f.ad_group_enabled), (w.group.ad_group_id, 300, 1, "ACTIVE", True))
        self.assertEqual((f.owner_id, f.period_start, f.period_end, f.daily_budget), (w.adv.pk, NOW - DAY, NOW + 10 * DAY, 50_000))
        self.assertEqual((f.title, f.redirect_url, f.image_name), ("제목", "https://example.com/landing", w.creative.image_name))
        self.assertEqual(api.candidate_facts_for_slot(second.slot.slot_id)[0].ad_group_id, second.group.ad_group_id)  # 슬롯별로 다르다
        self.assertEqual(api.candidate_facts_for_slot(uuid.uuid4()), [])

    def case_latest_bid_is_used_and_structural_exclusions(self):
        w = World()
        api.add_bid(w.adv, w.group.ad_group_id, 770, expected_version=1, now=NOW)
        self.assertEqual(api.candidate_facts_for_slot(w.slot.slot_id)[0].bid_amount, 770)  # 최대 버전
        for change, view in (
            (lambda: api.update_ad_group(w.adv, w.group.ad_group_id, {"enabled": False}, now=NOW), "광고그룹 꺼짐"),
            (lambda: api.update_ad_group(w.adv, w.group.ad_group_id, {"enabled": True}, now=NOW), None),
            (lambda: api.update_campaign(w.adv, w.campaign.campaign_id, {"status": "PAUSED"}, now=NOW), "캠페인 일시 중지"),
            (lambda: api.update_campaign(w.adv, w.campaign.campaign_id, {"status": "ACTIVE"}, now=NOW), None),
            (lambda: api.suspend_campaign(w.admin, w.campaign.campaign_id, "사유", now=NOW), "관리자 중단"),
            (lambda: api.release_campaign(w.admin, w.campaign.campaign_id, now=NOW), None),
        ):
            change()
            self.assertEqual(len(api.candidate_facts_for_slot(w.slot.slot_id)), 0 if view else 1, view)

    def case_group_without_bid_or_creative_is_not_a_candidate(self):
        w = World(with_creative=False)
        self.assertEqual(api.candidate_facts_for_slot(w.slot.slot_id), [])  # 소재 없음
        creative = api.create_creative(w.adv, w.group.ad_group_id, "t", "https://a.example", png_file(), now=NOW)
        self.assertEqual(len(api.candidate_facts_for_slot(w.slot.slot_id)), 1)
        nobid = api.create_ad_group(w.adv, w.campaign.campaign_id, "입찰 없음", [w.slot.slot_id], now=NOW)
        api.link_creative(w.adv, nobid.ad_group_id, creative.creative_id, now=NOW, select=True)
        self.assertEqual(len(api.candidate_facts_for_slot(w.slot.slot_id)), 1)  # 입찰 없는 그룹은 소재가 있어도 빠진다

    def case_expired_but_not_yet_ended_campaign_is_still_returned_for_engine_to_judge(self):
        w = World()
        self.assertEqual(len(api.candidate_facts_for_slot(w.slot.slot_id)), 1)  # 기간 판정은 조회에 넣지 않는다(engine 한 곳)

    def case_campaign_end_times_drop_ids_that_are_not_uuids(self):
        """여러 캠페인을 한 번에 찾는 조회는 형식이 틀린 값을 오류 없이 버린다(없는 ID 와 같다)."""
        w = World()
        ids = ["not-a-uuid", "", None, str(uuid.uuid4()), w.campaign.campaign_id]
        self.assertEqual(api.campaign_end_times(ids), {w.campaign.campaign_id: NOW + 10 * DAY})

    def test_candidate_facts(self):
        self.run_cases()


@tag("security")
class AssetServableTests(TestCase):
    def test_is_asset_servable_by_campaign_state(self):
        w = World()
        name, cid = w.creative.image_name, w.campaign.campaign_id
        self.assertTrue(api.is_asset_servable(name, now=NOW))
        api.update_campaign(w.adv, cid, {"status": "PAUSED"}, now=NOW)
        self.assertTrue(api.is_asset_servable(name, now=NOW))  # 일시 중지는 미리보기를 위해 서빙
        api.update_campaign(w.adv, cid, {"status": "ACTIVE"}, now=NOW)
        api.suspend_campaign(w.admin, cid, "부적절", now=NOW)
        self.assertFalse(api.is_asset_servable(name, now=NOW))
        api.release_campaign(w.admin, cid, now=NOW)
        self.assertTrue(api.is_asset_servable(name, now=NOW))
        self.assertFalse(api.is_asset_servable(name, now=NOW + 11 * DAY))  # 상태 갱신 전이라도 기간이 지나면 막힘
        api.end_campaign(w.adv, cid, now=NOW)
        self.assertFalse(api.is_asset_servable(name, now=NOW))
        self.assertFalse(api.is_asset_servable("0" * 64 + ".png", now=NOW))  # 등록되지 않은 이름


class AuditTests(TestCase):
    def test_advertiser_and_admin_actions_are_audited(self):
        w = World()
        api.update_campaign(w.adv, w.campaign.campaign_id, {"name": "바뀐 이름"}, now=NOW)
        api.suspend_campaign(w.admin, w.campaign.campaign_id, "사유", now=NOW)
        seen = set(AuditLog.objects.values_list("action", flat=True))
        self.assertLessEqual({"campaign.created", "ad_group.created", "creative.created", "campaign.updated", "campaign.suspended"}, seen)
        entry = AuditLog.objects.get(action="campaign.suspended")
        self.assertEqual((entry.actor_id, entry.detail["reason"]), (w.admin.pk, "사유"))


class ConcurrentBidTests(TransactionTestCase):
    def test_concurrent_bids_with_same_expected_version_only_one_wins(self):
        w = World()
        results, errors = run_together([lambda a=a: api.add_bid(w.adv, w.group.ad_group_id, a, expected_version=1, now=NOW) for a in (31, 32)])
        self.assertEqual(([type(e).__name__ for e in errors], sum(r is not None for r in results)), (["StaleVersion"], 1), errors)  # 진 쪽은 충돌 안내여야 한다(교착·잠금 초과 같은 DB 오류가 아니다)
        self.assertEqual([b.bid_version for b in api.list_bids(w.adv, w.group.ad_group_id)], [2, 1])


@tag("security")
class CreateAdTests(Cases, TestCase):
    """광고 만들기(`create_ad`, 6-9d — Step 3-0 S3 를 대체): 이미 있는 캠페인에 광고그룹(입찰 포함)과 소재를 한 트랜잭션으로. 하나라도 실패하면 아무것도
    남지 않는다(설계 §10 "광고 만들기 흐름"). 캠페인은 `create_campaign` 으로 따로 만든다."""

    def setUp(self):
        self.admin = make_admin()
        self.adv = make_advertiser()
        self.slot = make_slot(self.admin, unit="CLICK")
        self.campaign = api.create_campaign(self.adv, "가을 캠페인", NOW, NOW + 10 * DAY, 5000, now=NOW)

    def create(self, ad_group=None, creative=None, user=None, campaign_id=None):
        return api.create_ad(
            user or self.adv, campaign_id or self.campaign.campaign_id,
            ad_group={"name": "로비 배너", "slot_ids": [self.slot.slot_id], "bid_amount": 300, **(ad_group or {})},
            creative={"title": "제목", "redirect_url": "https://example.com/landing", "image_file": png_file(), **(creative or {})},
            now=NOW,
        )

    def assertNothingCreated(self):
        self.assertEqual((AdGroup.objects.count(), Bid.objects.count(), Creative.objects.count()), (0, 0, 0))
        self.assertEqual((AdGroupProfile.objects.count(), AdGroupProfileVersion.objects.count(), EventOutbox.objects.filter(event_name="ad.ad_group_profile_changed").count()),
                         (0, 0, 0))  # 카테고리 줄·판과 그 이벤트도 함께 되돌려진다
        self.assertFalse(AuditLog.objects.filter(action__in=["ad_group.created", "creative.created"]).exists())

    def case_creates_the_ad_group_and_its_creative_in_one_go(self):
        ad = self.create()
        self.assertIsInstance(ad, api.CreatedAd)
        self.assertEqual((ad.campaign.campaign_id, ad.ad_group.name, ad.creative.title), (self.campaign.campaign_id, "로비 배너", "제목"))
        self.assertEqual((ad.ad_group.campaign_id, ad.ad_group.creative_id), (ad.campaign.campaign_id, ad.creative.creative_id))  # 연결하고 선택했다
        self.assertEqual(ad.ad_group.current_bid.amount, 300)
        self.assertEqual(sorted(AuditLog.objects.filter(actor_id=self.adv.pk).values_list("action", flat=True)), ["ad_group.created", "campaign.created", "creative.created"])

    def case_a_new_ad_starts_unclassified_with_zero_weights_and_one_profile_event(self):
        """광고를 만들면 같은 트랜잭션에서 카테고리·가중치 줄 하나(분류 전·0·0·0·판 1)와 그 이벤트 하나(설계 §10 "광고 카테고리·과정 가중치").
        이벤트는 판의 상태 전체 일곱 키이고 줄 ID 는 싣지 않는다. 바꾼 사람은 표에도 이벤트에도 없다. 이벤트 ID 는 (광고, 판) 원천 ID 에서 만든다."""
        ad = self.create()
        p = AdGroupProfile.objects.get()
        self.assertEqual((p.ad_group_id, p.category, p.weight_mlo, p.weight_aio, p.weight_skn, p.version, p.created_at, p.changed_at),
                         (ad.ad_group.ad_group_id, None, 0, 0, 0, 1, NOW, NOW))
        self.assertNotIn("changed_by", {f.name for f in AdGroupProfile._meta.get_fields()})  # 바꾼 사람 열은 두지 않는다(사용자 결정)
        v = AdGroupProfileVersion.objects.get()  # 판 표(O13)에도 판 1 한 줄 = 지금 줄(ACW-3)
        self.assertEqual((v.ad_group_id, v.version, v.category, v.weight_mlo, v.weight_aio, v.weight_skn, v.changed_at), (p.ad_group_id, 1, None, 0, 0, 0, NOW))
        row = EventOutbox.objects.get(event_name="ad.ad_group_profile_changed")
        gid = ad.ad_group.ad_group_id
        self.assertEqual((row.event_id, row.aggregate_id, row.schema_version, row.created_at),
                         (event_id_for("ad.ad_group_profile_changed", f"{gid}/1"), gid, 1, NOW))
        self.assertEqual(row.payload, {  # jsonb 는 키 순서를 지키지 않는다 — 빌더의 키 순서는 case_payload_shapes 가 본다
            "ad_group_id": str(gid), "version": 1, "category": None, "weight_mlo": 0, "weight_aio": 0, "weight_skn": 0, "changed_at": NOW.isoformat(),
        })

    def case_a_rejected_image_leaves_no_ad_group(self):
        with self.assertRaises(ImageRejected):
            self.create(creative={"image_file": io.BytesIO(b"not an image")})
        self.assertNothingCreated()

    def case_a_failure_in_any_part_leaves_nothing(self):
        cases = {
            "광고그룹 이름": ({"name": ""}, None),
            "광고그룹 위치": ({"slot_ids": [uuid.uuid4()]}, None),
            "입찰 금액": ({"bid_amount": 0}, None),
            "이동 주소": (None, {"redirect_url": "http://example.com"}),
            "소재 제목": (None, {"title": ""}),
        }
        for label, (ad_group, creative) in cases.items():
            with self.subTest(label), self.assertRaises(InvalidRequest):
                self.create(ad_group, creative)
            self.assertNothingCreated()

    def case_errors_say_which_part_they_belong_to(self):
        """두 부분에 같은 이름의 칸이 있을 수 있다 — 오류가 어느 부분의 칸인지 알려야 화면이 그 칸 옆에 보여 준다."""
        for part, override, pointer in (
            ("ad_group", {"ad_group": {"name": ""}}, "/ad_group/name"),
            ("creative", {"creative": {"title": ""}}, "/creative/title"),
        ):
            with self.subTest(part), self.assertRaises(InvalidRequest) as e:
                self.create(**override)
            self.assertEqual([x["pointer"] for x in e.exception.errors], [pointer])

    def case_another_advertisers_or_an_ended_campaign_is_refused(self):
        theirs = api.create_campaign(make_advertiser(), "남의 캠페인", NOW, None, 1000, now=NOW)
        with self.assertRaises(Http404):
            self.create(campaign_id=theirs.campaign_id)
        api.end_campaign(self.adv, self.campaign.campaign_id, now=NOW)
        with self.assertRaises(api.CampaignEnded):
            self.create()
        self.assertNothingCreated()

    @override_settings(MAX_AD_GROUPS_PER_ADVERTISER=0)
    def case_a_limit_leaves_nothing(self):
        with self.assertRaises(api.LimitExceeded):
            self.create()
        self.assertNothingCreated()

    def test_create_ad(self):
        self.run_cases()


class CampaignEndsAtTests(Cases, TestCase):
    def setUp(self):
        self.adv = make_advertiser()

    def case_create_refuses_an_end_in_the_past_even_after_the_start(self):
        with self.assertRaises(InvalidRequest) as caught:
            api.create_campaign(self.adv, "지난 캠페인", NOW - 2 * DAY, NOW - DAY, 1000, now=NOW)
        self.assertEqual(caught.exception.errors, ENDS_AT_ERROR)

    def case_update_refuses_an_end_before_a_future_start(self):
        c = api.create_campaign(self.adv, "다음 주 캠페인", NOW + 5 * DAY, None, 1000, now=NOW)
        with self.assertRaises(InvalidRequest) as caught:
            api.update_campaign(self.adv, c.campaign_id, {"ends_at": NOW + DAY}, now=NOW)
        self.assertEqual(caught.exception.errors, ENDS_AT_ERROR)

    def test_campaign_ends_at(self):
        self.run_cases()


@tag("security")
class AdGroupSegmentTests(Cases, TestCase):
    """TG-S2 광고그룹의 대상 그룹 T(DRD O11, 설계 §10 "대상 그룹 타기팅" ③·⑧): 0개 이상, 비면 전체. **새로 고르는** 그룹은 살아 있고 고른 구성의 매체 것이어야 한다.
    이미 연결된 그룹은 보관되거나 구성이 바뀌어도 남는다. 바꿀 때마다 변경 로그 `SEGMENTS_CHANGED` + 이벤트(값이 그대로면 없음)."""

    def setUp(self):
        self.w = World(ends_in=None)
        self.adv, self.gid, self.media_id = self.w.adv, self.w.group.ad_group_id, self.w.slot.media_id
        self.mlo = masters.create_segment(self.w.admin, self.media_id, "mlo", "MLO")
        self.skn = masters.create_segment(self.w.admin, self.media_id, "skn", "SKN")

    def logs(self, ad_group_id, kind):
        return list(ChangeLog.objects.filter(target_id=ad_group_id, change_type=kind).order_by("changed_at", "change_id").values_list("previous_value", "new_value"))

    def links(self, ad_group_id):
        return sorted(AdGroupSegment.objects.filter(ad_group_id=ad_group_id).values_list("segment_id", flat=True), key=str)

    def case_create_links_targets_and_the_created_log_carries_them(self):
        g = api.create_ad_group(self.adv, self.w.campaign.campaign_id, "대상", [self.w.slot.slot_id], segment_ids=[self.skn.segment_id, self.mlo.segment_id], now=NOW)
        expected = sorted([self.mlo.segment_id, self.skn.segment_id], key=str)
        self.assertEqual((list(g.segment_ids), self.links(g.ad_group_id)), (expected, expected))
        self.assertEqual(self.logs(g.ad_group_id, "CREATED")[0][1]["segment_ids"], [str(s) for s in expected])  # 정렬한 UUID 문자열
        self.assertEqual(api.get_ad_group(self.adv, g.ad_group_id).segment_ids, tuple(expected))
        everyone = api.create_ad_group(self.adv, self.w.campaign.campaign_id, "전체", [self.w.slot.slot_id], now=NOW)
        self.assertEqual((everyone.segment_ids, self.logs(everyone.ad_group_id, "CREATED")[0][1]["segment_ids"]), ((), []))

    def case_new_targets_must_be_live_and_of_a_chosen_slot_media(self):
        other_media = masters.create_segment(self.w.admin, make_slot(self.w.admin).media_id, "aio", "AIO")
        archived = masters.create_segment(self.w.admin, self.media_id, "old", "옛 과정")
        masters.update_segment(self.w.admin, archived.segment_id, 1, is_active=False)
        groups = AdGroup.objects.count()
        for bad in ([other_media.segment_id], [archived.segment_id], [uuid.uuid4()], ["not-a-uuid"], [self.mlo.segment_id, self.mlo.segment_id], "mlo"):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidRequest) as caught:
                    api.create_ad_group(self.adv, self.w.campaign.campaign_id, "x", [self.w.slot.slot_id], segment_ids=bad)
                self.assertEqual(caught.exception.errors[0]["pointer"], "/segment_ids")
                with self.assertRaises(InvalidRequest):
                    api.update_ad_group(self.adv, self.gid, {"segment_ids": bad})
        self.assertEqual((AdGroup.objects.count(), self.links(self.gid), self.logs(self.gid, "SEGMENTS_CHANGED")), (groups, [], []))  # 아무것도 쓰지 않았다

    def case_update_replaces_targets_and_logs_each_change_once(self):
        seen = api.get_ad_group(self.adv, self.gid).updated_at
        api.update_ad_group(self.adv, self.gid, {"segment_ids": [self.mlo.segment_id]}, now=NOW + DAY, expected_updated_at=seen)
        api.update_ad_group(self.adv, self.gid, {"segment_ids": [self.mlo.segment_id]}, now=NOW + 2 * DAY)  # 같은 값 — 로그 없음
        api.update_ad_group(self.adv, self.gid, {"segment_ids": [self.skn.segment_id, self.mlo.segment_id]}, now=NOW + 3 * DAY)
        api.update_ad_group(self.adv, self.gid, {"segment_ids": []}, now=NOW + 4 * DAY)
        both = sorted([str(self.mlo.segment_id), str(self.skn.segment_id)])
        self.assertEqual(self.logs(self.gid, "SEGMENTS_CHANGED"), [
            ({"segment_ids": []}, {"segment_ids": [str(self.mlo.segment_id)]}),
            ({"segment_ids": [str(self.mlo.segment_id)]}, {"segment_ids": both}),
            ({"segment_ids": both}, {"segment_ids": []}),
        ])
        for change_id in ChangeLog.objects.filter(target_id=self.gid, change_type="SEGMENTS_CHANGED").values_list("change_id", flat=True):
            self.assertTrue(EventOutbox.objects.filter(pk=event_id_for("ad.ad_group_changed", change_id)).exists())
        self.assertEqual(self.links(self.gid), [])
        with self.assertRaises(StaleVersion):  # 옛 화면
            api.update_ad_group(self.adv, self.gid, {"segment_ids": [self.mlo.segment_id]}, expected_updated_at=seen)

    def case_a_linked_target_stays_when_archived_or_when_its_media_leaves_the_slots(self):
        api.update_ad_group(self.adv, self.gid, {"segment_ids": [self.mlo.segment_id]})
        masters.update_segment(self.w.admin, self.mlo.segment_id, 1, is_active=False)
        api.update_ad_group(self.adv, self.gid, {"segment_ids": [self.mlo.segment_id, self.skn.segment_id]})  # 보관된 mlo 는 이미 연결돼 있어 남는다
        elsewhere = make_slot(self.w.admin)
        api.update_ad_group(self.adv, self.gid, {"slot_ids": [elsewhere.slot_id]})  # 그 매체가 구성에서 빠져도 연결은 남는다(화면이 경고)
        self.assertEqual(self.links(self.gid), sorted([self.mlo.segment_id, self.skn.segment_id], key=str))
        api.update_ad_group(self.adv, self.gid, {"segment_ids": [self.skn.segment_id]})  # 빼기는 언제나 된다
        for again in ([self.mlo.segment_id], [self.skn.segment_id, self.mlo.segment_id]):  # 뺀 뒤 다시 고르면 새로 고르는 것 — 보관됐다
            with self.assertRaises(InvalidRequest):
                api.update_ad_group(self.adv, self.gid, {"segment_ids": again})

    def case_create_ad_links_targets_and_points_errors_at_the_ad_group(self):
        ad = {"name": "광고", "slot_ids": [self.w.slot.slot_id], "bid_amount": 300, "segment_ids": [self.mlo.segment_id]}
        created = api.create_ad(self.adv, self.w.campaign.campaign_id, ad, creative_id=self.w.creative.creative_id, now=NOW)
        self.assertEqual(self.links(created.ad_group.ad_group_id), [self.mlo.segment_id])
        masters.update_segment(self.w.admin, self.skn.segment_id, 1, is_active=False)
        groups = AdGroup.objects.count()
        with self.assertRaises(InvalidRequest) as caught:
            api.create_ad(self.adv, self.w.campaign.campaign_id, {**ad, "segment_ids": [self.skn.segment_id]}, creative_id=self.w.creative.creative_id, now=NOW)
        self.assertEqual((caught.exception.errors[0]["pointer"], AdGroup.objects.count()), ("/ad_group/segment_ids", groups))  # 한 트랜잭션 — 광고그룹도 남지 않는다

    def case_listing_reads_every_groups_targets_in_one_query(self):
        api.update_ad_group(self.adv, self.gid, {"segment_ids": [self.mlo.segment_id]})
        def listed():
            with CaptureQueriesContext(connection) as q:
                rows = api.list_ad_groups(self.adv, self.w.campaign.campaign_id)
            return rows, len(q)
        rows, one = listed()
        for i in range(3):
            api.create_ad_group(self.adv, self.w.campaign.campaign_id, f"더 {i}", [self.w.slot.slot_id], segment_ids=[self.skn.segment_id])
        rows, four = listed()
        self.assertEqual(one, four)  # 질의 수는 광고그룹 수와 무관
        self.assertEqual([r.segment_ids for r in rows], [(self.mlo.segment_id,)] + [(self.skn.segment_id,)] * 3)

    def test_ad_group_segment(self):
        self.run_cases()


class ConcurrentSegmentChoiceTests(TransactionTestCase):
    def test_choosing_waits_for_an_archive_in_progress_and_then_refuses(self):
        """고르는 동안 보관되는 경쟁(설계 §10 "대상 그룹 타기팅" ⑧): 보관이 그룹 행을 쥔 사이 고르기는 공유 잠금에서 기다리고, 보관이 커밋된 뒤 400 이다
        (잠그지 않고 읽으면 보관 전 값을 보고 연결해 보관된 그룹이 새로 골라진다)."""
        w = World(ends_in=None)
        g = masters.create_segment(w.admin, w.slot.media_id, "mlo", "MLO")
        archive, choose = overlap(self, lambda: masters.update_segment(w.admin, g.segment_id, 1, is_active=False), "INSERT", "masters_segment_version",
                                  lambda: api.update_ad_group(w.adv, w.group.ad_group_id, {"segment_ids": [g.segment_id]}))
        self.assertEqual(archive.errors, [])
        self.assertEqual([type(e) for e in choose.errors], [InvalidRequest])
        self.assertFalse(AdGroupSegment.objects.exists())
