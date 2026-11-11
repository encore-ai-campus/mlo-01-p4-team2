import dataclasses
import uuid
from unittest import mock

from django.db import IntegrityError, transaction
from django.db.models import Model
from django.test import TestCase, TransactionTestCase, tag
from django.utils import timezone

from events.api import event_id_for
from events.models import EventOutbox
from foundation.errors import Conflict, Forbidden, InvalidRequest, NotFound, StaleVersion
from foundation.logging import request_id_var
from foundation.models import AppendOnlyError, AuditLog
from masters import api
from masters.models import BidPolicy, Media, MediaDeactivation, Segment, SegmentVersion, Slot
from tests.support.concurrency import run_together
from tests.support.factories import DAY, NOW, SHARES, make_admin, make_advertiser
from tests.support.cases import Cases


def new_slot(admin, media=None, name="메인 배너", floor=100, base=200, cap=500):
    media = media or api.create_media(admin, f"매체-{uuid.uuid4().hex[:6]}")
    return api.create_slot(admin, media.media_id, name, floor, base, cap, "초기 설정", audience="individual", **SHARES)


def versions(slot_id):
    return list(BidPolicy.objects.filter(slot_id=slot_id).order_by("policy_version").values_list("policy_version", flat=True))


class PolicyServiceTests(Cases, TestCase):
    def setUp(self):
        self.admin = make_admin()

    def case_create_slot_creates_first_policy_atomically(self):
        slot = new_slot(self.admin, floor=100, base=200, cap=500)
        self.assertEqual((slot.policy.policy_version, slot.policy.floor, slot.policy.base, slot.policy.cap), (1, 100, 200, 500))
        self.assertEqual(slot.policy.changed_by, self.admin.pk)
        # 정책 저장이 실패하면 슬롯도 남지 않는다
        media = api.create_media(self.admin, "원자성 매체")
        with mock.patch.object(BidPolicy._default_manager, "create", side_effect=IntegrityError("boom")):
            with self.assertRaises(Conflict):
                api.create_slot(self.admin, media.media_id, "배너", 100, 200, 300, "사유", audience="individual", **SHARES)
        self.assertFalse(Slot.objects.filter(media_id=media.media_id).exists())

    def case_every_policy_version_records_its_event_in_the_same_transaction(self):
        """정책 버전마다 `ad.bid_policy_changed` 하나(계획 docs/plans/00 P2-3): 만들기(v1 = INITIAL)·정책 변경·일괄 변경 모두, 정책 행과 함께 커밋되거나 함께 되돌려진다."""
        def policy_events(slot_id):
            return list(EventOutbox.objects.filter(event_name="ad.bid_policy_changed", aggregate_id=slot_id).order_by("created_at").values_list("event_id", "payload"))
        slot = new_slot(self.admin)
        v2 = api.add_policy_version(self.admin, slot.slot_id, 110, 200, 500, "사유", expected_version=1, **SHARES)
        v3 = api.add_policy_version_bulk(self.admin, {slot.slot_id: 2}, 120, 200, 500, "사유", **SHARES)[0]
        rows = policy_events(slot.slot_id)
        self.assertEqual([e for e, _ in rows], [event_id_for("ad.bid_policy_changed", p.bid_policy_id) for p in (slot.policy, v2, v3)])  # 버전마다 다른 이벤트, 원천에서 다시 계산한 ID
        self.assertEqual([(p["policy_version"], p["floor_amount"], p["change_source"]) for _, p in rows], [(1, 100, "INITIAL"), (2, 110, "MANUAL"), (3, 120, "MANUAL")])
        with mock.patch("masters.services.events.record_bid_policy_changed", side_effect=RuntimeError("outbox down")):  # 이벤트 쓰기가 실패하면 정책 행도 남지 않는다
            media = api.create_media(self.admin, "이벤트 실패 매체")
            with self.assertRaises(RuntimeError):
                api.create_slot(self.admin, media.media_id, "배너", 100, 200, 300, "사유", audience="individual", **SHARES)
            with self.assertRaises(RuntimeError):
                api.add_policy_version(self.admin, slot.slot_id, 130, 200, 500, "사유", expected_version=3, **SHARES)
        self.assertFalse(Slot.objects.filter(media_id=media.media_id).exists())
        self.assertEqual(versions(slot.slot_id), [1, 2, 3])
        with self.assertRaises(StaleVersion):  # 정책 저장이 실패하면 이벤트도 없다
            api.add_policy_version(self.admin, slot.slot_id, 130, 200, 500, "사유", expected_version=1, **SHARES)
        self.assertEqual(len(policy_events(slot.slot_id)), 3)

    def case_policy_service_rejects_zero_negative_and_order_violation(self):
        media = api.create_media(self.admin, "검증 매체")
        cases = [(0, 500, 900), (-1, 500, 900), (99, 200, 300), (1000, 500, 900), (100, 900, 500), (100, 200, 2**53), (100.5, 200, 300), (True, 200, 300),
                 ("100", 200, 300), (None, 200, 300)]  # 99: 하한 최소 100 미만(6-10c)
        for floor, base, cap in cases:
            with self.assertRaises(InvalidRequest, msg=(floor, base, cap)):
                api.create_slot(self.admin, media.media_id, "배너", floor, base, cap, "사유", audience="individual", **SHARES)
        self.assertFalse(Slot.objects.exists())

    def case_the_floor_is_at_least_100_in_all_three_places(self):
        """하한 최소 100(설계 §10 "구성별 반응 비율 과금", 6-10c): 비율 ≥ 1% 와 함께 반응 하나의 과금(내림)이 0 이 되지 않게 한다. 세 곳이 같은 검사를 쓴다."""
        slot = new_slot(self.admin)
        media = api.create_media(self.admin, "하한 매체")
        places = {
            "만들기": lambda floor: api.create_slot(self.admin, media.media_id, f"배너{floor}", floor, 200, 300, "사유", audience="individual", **SHARES).policy,
            "정책 변경": lambda floor: api.add_policy_version(self.admin, slot.slot_id, floor, 200, 300, "사유", expected_version=versions(slot.slot_id)[-1], **SHARES),
            "일괄 변경": lambda floor: api.add_policy_version_bulk(self.admin, {slot.slot_id: versions(slot.slot_id)[-1]}, floor, 200, 300, "사유", **SHARES)[0],
        }
        for place, call in places.items():
            before = BidPolicy.objects.count()
            with self.assertRaises(InvalidRequest, msg=place) as caught:
                call(99)
            self.assertEqual(caught.exception.errors, [{"pointer": "/floor", "message": "100 이상 9007199254740991 이하의 정수여야 합니다."}], place)
            self.assertEqual(BidPolicy.objects.count(), before, place)
            self.assertEqual(call(100).floor, 100, place)

    def case_charge_shares_are_checked_the_same_way_in_all_three_places(self):
        """반응 유형별 과금 비율(설계 §10 "구성별 반응 비율 과금", 6-10b): 각각 정수 1~98, 합 100. 만들기·정책 변경·일괄 변경이 같은 검사를 쓰고, 거절하면 아무것도 남지 않는다."""
        slot = new_slot(self.admin)
        media = api.create_media(self.admin, "비율 매체")
        bad = [(0, 2, 98), (2, 0, 98), (2, 98, 0), (99, 1, 0), (97, 1, 1), (98, 2, 1), (50.0, 25, 25), (True, 1, 98), ("98", 1, 1), (None, 1, 99)]
        good = [(1, 1, 98), (1, 98, 1), (98, 1, 1), (34, 33, 33)]
        places = {
            "만들기": lambda i, s: api.create_slot(self.admin, media.media_id, f"배너{i}", 100, 200, 300, "사유", audience="individual", **s).policy,
            "정책 변경": lambda i, s: api.add_policy_version(self.admin, slot.slot_id, 100, 200, 300, "사유", expected_version=versions(slot.slot_id)[-1], **s),
            "일괄 변경": lambda i, s: api.add_policy_version_bulk(self.admin, {slot.slot_id: versions(slot.slot_id)[-1]}, 100, 200, 300, "사유", **s)[0],
        }
        for place, call in places.items():
            for i, values in enumerate(bad):
                before = (Slot.objects.count(), BidPolicy.objects.count())
                with self.assertRaises(InvalidRequest, msg=(place, values)):
                    call(i, dict(zip(("impression_share", "click_share", "conversion_share"), values)))
                self.assertEqual((Slot.objects.count(), BidPolicy.objects.count()), before, (place, values))
            for i, values in enumerate(good):
                policy = call(f"ok{i}", dict(zip(("impression_share", "click_share", "conversion_share"), values)))
                self.assertEqual((policy.impression_share, policy.click_share, policy.conversion_share), values, place)
        with self.assertRaises(InvalidRequest) as caught:
            api.create_slot(self.admin, media.media_id, "합", 100, 200, 300, "사유", audience="individual", impression_share=97, click_share=1, conversion_share=1)
        self.assertEqual(caught.exception.errors, [{"pointer": "/shares", "message": "노출·클릭·전환 비율의 합은 100이어야 합니다(지금 99)."}])

    def case_create_slot_validates_name_reason_and_media(self):
        media = api.create_media(self.admin, "입력 매체")
        for kwargs in (dict(name=""), dict(name="  "), dict(name="x" * 101), dict(name="zero​width"),
                       dict(reason="x" * 201), dict(reason="zero​width")):
            args = dict(name="배너", reason="사유")
            args.update(kwargs)
            with self.assertRaises(InvalidRequest, msg=kwargs):
                api.create_slot(self.admin, media.media_id, args["name"], 100, 200, 300, args["reason"], audience="individual", **SHARES)
        with self.assertRaises(NotFound):
            api.create_slot(self.admin, uuid.uuid4(), "배너", 100, 200, 300, "사유", audience="individual", **SHARES)

    def case_a_slot_may_be_created_without_a_reason_but_a_policy_change_may_not(self):
        """만들 때만 빈 사유를 "없음"으로 채운다(6-8c). 가격을 바꾸는 정책 변경·일괄 변경은 사유가 필수다."""
        media = api.create_media(self.admin, "사유 매체")
        for i, blank in enumerate(("", "   ", None)):
            slot = api.create_slot(self.admin, media.media_id, f"배너{i}", 100, 200, 300, blank, audience="individual", **SHARES)
            self.assertEqual(slot.policy.reason, "없음")
        for blank in ("", "   ", None):
            with self.assertRaises(InvalidRequest, msg=blank):
                api.add_policy_version(self.admin, slot.slot_id, 100, 200, 300, blank, expected_version=1, **SHARES)
            with self.assertRaises(InvalidRequest, msg=blank):
                api.add_policy_version_bulk(self.admin, {slot.slot_id: 1}, 100, 200, 300, blank, **SHARES)

    def case_slot_name_unique_per_media_in_service(self):
        slot = new_slot(self.admin, name="상단")
        with self.assertRaises(Conflict):
            api.create_slot(self.admin, slot.media_id, "상단", 100, 200, 300, "사유", audience="individual", **SHARES)
        other = api.create_media(self.admin, "다른 매체")
        api.create_slot(self.admin, other.media_id, "상단", 100, 200, 300, "사유", audience="individual", **SHARES)  # 다른 매체는 가능

    def case_media_name_conflict(self):
        api.create_media(self.admin, "중복")
        with self.assertRaises(Conflict):
            api.create_media(self.admin, "중복")

    def case_add_policy_version_increments_per_slot(self):
        a, b = new_slot(self.admin), new_slot(self.admin)
        api.add_policy_version(self.admin, a.slot_id, 110, 210, 510, "인상", expected_version=1, **SHARES)
        p = api.add_policy_version(self.admin, a.slot_id, 120, 220, 520, "또 인상", expected_version=2, **SHARES)
        self.assertEqual(p.policy_version, 3)
        self.assertEqual(versions(a.slot_id), [1, 2, 3])
        self.assertEqual(versions(b.slot_id), [1])  # 슬롯끼리 영향 없음
        self.assertEqual(api.get_slot(a.slot_id).policy.cap, 520)  # 현재 값 = 최대 버전

    def case_policy_stale_expected_version_rejected(self):
        slot = new_slot(self.admin)
        api.add_policy_version(self.admin, slot.slot_id, 110, 210, 510, "A 관리자", expected_version=1, **SHARES)
        with self.assertRaises(StaleVersion):
            api.add_policy_version(self.admin, slot.slot_id, 990, 990, 990, "B 관리자(옛 화면)", expected_version=1, **SHARES)
        self.assertEqual(versions(slot.slot_id), [1, 2])  # B 의 변경은 저장되지 않았다
        self.assertEqual(api.get_slot(slot.slot_id).policy.floor, 110)

    def case_policy_invalid_values_not_saved_and_version_not_consumed(self):
        slot = new_slot(self.admin)
        with self.assertRaises(InvalidRequest):
            api.add_policy_version(self.admin, slot.slot_id, 300, 200, 500, "잘못", expected_version=1, **SHARES)
        self.assertEqual(versions(slot.slot_id), [1])
        api.add_policy_version(self.admin, slot.slot_id, 110, 210, 510, "정상", expected_version=1, **SHARES)
        self.assertEqual(versions(slot.slot_id), [1, 2])

    def case_unknown_slot_is_not_found(self):
        for bad in (uuid.uuid4(), "not-a-uuid"):
            with self.assertRaises(NotFound):
                api.add_policy_version(self.admin, bad, 100, 200, 300, "사유", expected_version=1, **SHARES)
            with self.assertRaises(NotFound):
                api.get_slot(bad)

    def case_bulk_policy_applies_per_slot_versions(self):
        a, b = new_slot(self.admin), new_slot(self.admin)
        api.add_policy_version(self.admin, b.slot_id, 110, 210, 510, "b 만 한 번 더", expected_version=1, **SHARES)
        out = api.add_policy_version_bulk(self.admin, {a.slot_id: 1, b.slot_id: 2}, 500, 600, 700, "일괄", **SHARES)
        self.assertEqual([p.policy_version for p in out], [2, 3])  # 슬롯마다 자기 버전이 쌓인다
        self.assertEqual((api.get_slot(a.slot_id).policy.cap, api.get_slot(b.slot_id).policy.cap), (700, 700))

    def case_bulk_policy_is_all_or_nothing(self):
        a, b = new_slot(self.admin), new_slot(self.admin)
        with self.assertRaises(StaleVersion):  # 두 번째 슬롯의 본 버전이 틀림
            api.add_policy_version_bulk(self.admin, {a.slot_id: 1, b.slot_id: 5}, 500, 600, 700, "일괄", **SHARES)
        self.assertEqual((versions(a.slot_id), versions(b.slot_id)), ([1], [1]))
        with self.assertRaises(NotFound):  # 없는 슬롯이 섞임
            api.add_policy_version_bulk(self.admin, {a.slot_id: 1, uuid.uuid4(): 1}, 500, 600, 700, "일괄", **SHARES)
        self.assertEqual(versions(a.slot_id), [1])
        with self.assertRaises(InvalidRequest):
            api.add_policy_version_bulk(self.admin, {}, 500, 600, 700, "빈 목록", **SHARES)

    def case_inactive_flags_hide_slots_from_default_listing(self):
        slot = new_slot(self.admin)
        other = new_slot(self.admin)
        self.assertEqual({s.slot_id for s in api.list_slots()}, {slot.slot_id, other.slot_id})
        api.set_slot_active(self.admin, slot.slot_id, False)
        self.assertEqual({s.slot_id for s in api.list_slots()}, {other.slot_id})
        self.assertEqual({s.slot_id for s in api.list_slots(active_only=False)}, {slot.slot_id, other.slot_id})
        api.set_media_active(self.admin, other.media_id, False)  # 매체가 꺼지면 그 슬롯도 빠진다
        self.assertEqual(api.list_slots(), [])
        self.assertFalse(api.get_slot(slot.slot_id).is_active)  # 개별 조회는 상태를 값으로 돌려준다
        with self.assertRaises(NotFound):
            api.set_slot_active(self.admin, uuid.uuid4(), True)
        with self.assertRaises(NotFound):
            api.set_media_active(self.admin, uuid.uuid4(), True)

    def case_slot_display_is_validated_audited_and_shown_on_the_slot(self):
        """구성 표시 정보(설계 §10 "구성 표시 정보"): 규칙은 DB CHECK 와 같고 어긋나면 400(바뀌지 않음), 바꾸면 감사에 바꾼 값과 이전 값,
        모든 슬롯 읽기(`get_slot`·`list_slots`·`get_slots_with_policy`)에 실린다. 화면 정보라 이벤트를 쓰지 않는다."""
        slot = new_slot(self.admin)
        self.assertIsNone(api.get_slot(slot.slot_id).display)  # 처음은 정보 없음
        events_before = EventOutbox.objects.count()
        rejected = {
            (3, None, "cover"): "/width", (None, 2, "contain"): "/width", (0, 2, "cover"): "/width", (3, 10001, "cover"): "/height", (True, 2, "cover"): "/width",
            ("3", 2, "cover"): "/width", (3, 2, None): "/fit", (3, 2, "fill"): "/fit", (None, None, "stretch"): "/width", (None, None, "cover"): "/width",
        }
        for (width, height, fit), pointer in rejected.items():
            with self.subTest((width, height, fit)), self.assertRaises(InvalidRequest) as caught:
                api.set_slot_display(self.admin, slot.slot_id, width, height, fit)
            self.assertEqual(caught.exception.errors[0]["pointer"], pointer)
        self.assertIsNone(api.get_slot(slot.slot_id).display)
        self.assertFalse(AuditLog.objects.filter(action="masters.slot_display_changed").exists())

        api.set_slot_display(self.admin, slot.slot_id, 260, 175, "stretch")
        api.set_slot_display(self.admin, slot.slot_id, None, None, "contain")  # 이미지 전체는 비율 없이도 된다
        self.assertEqual(api.get_slot(slot.slot_id).display, api.SlotDisplay(width=None, height=None, fit="contain"))
        api.set_slot_display(self.admin, slot.slot_id, 3, 2, "cover")
        shown = api.SlotDisplay(width=3, height=2, fit="cover")
        self.assertEqual(api.get_slot(slot.slot_id).display, shown)
        self.assertEqual({s.slot_id: s.display for s in api.list_slots()}[slot.slot_id], shown)
        self.assertEqual(api.get_slots_with_policy([slot.slot_id])[slot.slot_id].display, shown)
        api.set_slot_display(self.admin, slot.slot_id, None, None, "")  # 화면의 "없음"(빈 값) = 지움
        self.assertIsNone(api.get_slot(slot.slot_id).display)
        details = [e.detail for e in AuditLog.objects.filter(action="masters.slot_display_changed", target_id=str(slot.slot_id)).order_by("created_at", "pk")]
        self.assertEqual(details, [
            {"width": 260, "height": 175, "fit": "stretch", "previous": {"width": None, "height": None, "fit": None}},
            {"width": None, "height": None, "fit": "contain", "previous": {"width": 260, "height": 175, "fit": "stretch"}},
            {"width": 3, "height": 2, "fit": "cover", "previous": {"width": None, "height": None, "fit": "contain"}},
            {"width": None, "height": None, "fit": None, "previous": {"width": 3, "height": 2, "fit": "cover"}},
        ])
        self.assertEqual(EventOutbox.objects.count(), events_before)
        with self.assertRaises(NotFound):
            api.set_slot_display(self.admin, uuid.uuid4(), 3, 2, "cover")

    def case_only_turning_an_active_media_off_records_a_deactivation(self):
        """6-1a: 켜져 있던 매체를 실제로 끌 때만 기록한다(이미 꺼진 매체를 다시 끄거나 켜면 없음). 반응 판정은 이 기록에서 (결정 시각, 접수 시각] 안의 가장 이른 비활성화를 찾는다."""
        media = api.create_media(self.admin, "끄는 매체")
        before = timezone.now()
        api.set_media_active(self.admin, media.media_id, False)
        api.set_media_active(self.admin, media.media_id, False)  # 이미 꺼져 있다
        api.set_media_active(self.admin, media.media_id, True)
        first = MediaDeactivation.objects.get(media_id=media.media_id).deactivated_at
        api.set_media_active(self.admin, media.media_id, False)
        self.assertEqual(MediaDeactivation.objects.filter(media_id=media.media_id).count(), 2)
        self.assertEqual(api.first_deactivation(media.media_id, before, timezone.now()), first)  # 가장 이른 것
        self.assertEqual(api.first_deactivation(media.media_id, before, first), first)  # 끝(접수 시각)은 포함
        self.assertIsNone(api.first_deactivation(media.media_id, first, first))  # 시작(결정 시각)은 제외
        self.assertIsNone(api.first_deactivation(uuid.uuid4(), before, timezone.now()))

    def case_list_and_get_media_for_the_operator_screens(self):
        a, _ = api.create_media(self.admin, "나 매체"), api.create_media(self.admin, "가 매체")
        api.set_media_active(self.admin, a.media_id, False)
        self.assertEqual([m.name for m in api.list_media(self.admin)], ["가 매체", "나 매체"])  # 이름순, 비활성 포함
        self.assertEqual(api.get_media(self.admin, a.media_id), dataclasses.replace(a, is_active=False))
        for missing in (uuid.uuid4(), "not-a-uuid"):
            with self.assertRaises(NotFound):
                api.get_media(self.admin, missing)

    def case_policy_history_newest_first(self):
        slot = new_slot(self.admin)
        api.add_policy_version(self.admin, slot.slot_id, 110, 210, 510, "버전 2", expected_version=1, **SHARES)
        history = api.list_policy_versions(self.admin, slot.slot_id)
        self.assertEqual([(p.policy_version, p.floor, p.reason) for p in history], [(2, 110, "버전 2"), (1, 100, "초기 설정")])
        with self.assertRaises(NotFound):
            api.list_policy_versions(self.admin, uuid.uuid4())

    def case_a_stale_bulk_change_names_the_slot(self):
        """일괄 변경이 충돌로 전부 취소될 때 어느 게재 위치 때문인지 알 수 있다."""
        a, b = new_slot(self.admin, name="첫 배너"), new_slot(self.admin, name="둘째 배너")
        with self.assertRaises(StaleVersion) as e:
            api.add_policy_version_bulk(self.admin, {a.slot_id: 1, b.slot_id: 5}, 500, 600, 700, "일괄", **SHARES)
        self.assertIn("둘째 배너", str(e.exception.detail))

    def case_list_slots_filters_by_media(self):
        a, _ = new_slot(self.admin), new_slot(self.admin)
        self.assertEqual([s.slot_id for s in api.list_slots(media_id=a.media_id)], [a.slot_id])

    def case_get_slots_with_policy_single_query(self):
        slots = [new_slot(self.admin) for _ in range(3)]
        api.add_policy_version(self.admin, slots[0].slot_id, 110, 210, 510, "버전 2", expected_version=1, **SHARES)
        ids = [s.slot_id for s in slots] + [uuid.uuid4()]  # 없는 ID 는 결과에 없다
        with self.assertNumQueries(1):
            result = api.get_slots_with_policy(ids)
        self.assertEqual(set(result), {s.slot_id for s in slots})
        self.assertEqual(result[slots[0].slot_id].policy.policy_version, 2)  # 현재 = 최대 버전
        self.assertEqual(result[slots[1].slot_id].policy.policy_version, 1)

    def case_bulk_lookups_drop_ids_that_are_not_uuids(self):
        """여러 ID 를 한 번에 찾는 조회는 형식이 틀린 값을 오류 없이 버린다(없는 ID 와 같다). 대문자 표기도 같은 ID 다."""
        slot = new_slot(self.admin)
        ids = ["not-a-uuid", "", None, str(slot.slot_id).upper(), slot.slot_id]
        self.assertEqual(set(api.get_slots_with_policy(ids)), {slot.slot_id})
        self.assertEqual(api.slot_names(ids), {slot.slot_id: slot.name})

    def case_api_returns_frozen_values_not_models(self):
        slot = new_slot(self.admin)
        for value in (slot, slot.policy, api.get_slot(slot.slot_id), api.create_media(self.admin, "값 매체"), api.list_slots()[0]):
            self.assertFalse(isinstance(value, Model))
            self.assertTrue(dataclasses.is_dataclass(value))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            slot.name = "바꿈"

    def test_policy_service(self):
        self.run_cases()


@tag("security")
class MastersAuthorizationAndAuditTests(Cases, TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.slot = new_slot(self.admin)

    def calls(self, actor):
        s = self.slot
        return [
            lambda: api.create_media(actor, "새 매체"),
            lambda: api.set_media_active(actor, s.media_id, False),
            lambda: api.create_slot(actor, s.media_id, "다른 배너", 100, 200, 300, "사유", audience="individual", **SHARES),
            lambda: api.add_policy_version(actor, s.slot_id, 100, 200, 300, "사유", expected_version=1, **SHARES),
            lambda: api.add_policy_version_bulk(actor, {s.slot_id: 1}, 100, 200, 300, "사유", **SHARES),
            lambda: api.set_slot_active(actor, s.slot_id, False),
            lambda: api.set_slot_display(actor, s.slot_id, 3, 2, "cover"),
            lambda: api.list_media(actor),
            lambda: api.list_policy_versions(actor, s.slot_id),
            lambda: api.get_media(actor, s.media_id),
        ]

    def case_non_admin_actor_rejected(self):
        inactive_admin = make_admin()
        inactive_admin.is_active = False
        inactive_admin.save()
        from django.contrib.auth.models import AnonymousUser

        before = (AuditLog.objects.count(), versions(self.slot.slot_id))
        for actor in (make_advertiser(), inactive_admin, AnonymousUser(), None):
            for call in self.calls(actor):
                with self.assertRaises(Forbidden):
                    call()
        self.assertEqual((AuditLog.objects.count(), versions(self.slot.slot_id)), before)
        self.assertEqual(Media.objects.count(), 1)

    def case_admin_actions_are_audited_with_request_id(self):
        AuditLog.objects.all()  # 시작 상태 기준: setUp 의 슬롯 생성 기록은 이미 있다
        token = request_id_var.set("11111111-2222-3333-4444-555555555555")
        try:
            api.add_policy_version(self.admin, self.slot.slot_id, 120, 220, 520, "정책 상향", expected_version=1, impression_share=1, click_share=98, conversion_share=1)
            api.set_slot_active(self.admin, self.slot.slot_id, False)
        finally:
            request_id_var.reset(token)
        entry = AuditLog.objects.get(action="masters.policy_added")
        self.assertEqual((entry.actor_id, entry.target_type, entry.target_id), (self.admin.pk, "masters.slot", str(self.slot.slot_id)))
        self.assertEqual(entry.request_id, "11111111-2222-3333-4444-555555555555")
        self.assertEqual(entry.detail["version"], 2)
        self.assertEqual({k: entry.detail[k] for k in ("impression_share", "click_share", "conversion_share")},
                         {"impression_share": 1, "click_share": 98, "conversion_share": 1})  # 바꾼 비율과 이전 비율이 감사 기록에 남는다(6-10b)
        self.assertEqual(entry.detail["previous"], {"floor": 100, "base": 200, "cap": 500, **SHARES})
        self.assertEqual(entry.detail["reason"], "정책 상향")
        created = AuditLog.objects.get(action="masters.slot_created", target_id=str(self.slot.slot_id))
        self.assertEqual({k: created.detail[k] for k in SHARES}, SHARES)  # 처음 비율도 감사 기록에 남는다
        self.assertTrue(AuditLog.objects.filter(action="masters.slot_active_changed", target_id=str(self.slot.slot_id)).exists())
        self.assertTrue(AuditLog.objects.filter(action__in=["masters.media_created", "masters.slot_created"]).count() >= 2)

    def case_audit_without_request_context_has_empty_request_id(self):
        entry = AuditLog.objects.get(action="masters.slot_created")
        self.assertEqual(entry.request_id, "")

    def test_masters_authorization_and_audit(self):
        self.run_cases()


class ConcurrentPolicyTests(TransactionTestCase):
    def test_concurrent_policy_saves_only_one_wins(self):
        """같은 `expected_version` 으로 동시에 저장하면 정확히 한쪽만 성공한다."""
        admin_a, admin_b = make_admin(), make_admin()
        slot = new_slot(admin_a)
        results, errors = run_together([lambda a=a, cap=cap: api.add_policy_version(a, slot.slot_id, 110, 210, cap, "동시 변경", expected_version=1, **SHARES)
                                        for a, cap in ((admin_a, 600), (admin_b, 700))])
        self.assertEqual(([type(e).__name__ for e in errors], sum(r is not None for r in results)), (["StaleVersion"], 1), errors)  # 진 쪽은 충돌 안내여야 한다(교착·잠금 초과 같은 DB 오류가 아니다)
        self.assertEqual(versions(slot.slot_id), [1, 2])  # 두 번째 버전이 정확히 하나
        self.assertIn(api.get_slot(slot.slot_id).policy.cap, (600, 700))


@tag("security")
class SegmentServiceTests(Cases, TestCase):
    """대상 그룹 카탈로그(TG-S1, DRD M7·M8, 설계 §10 "대상 그룹 타기팅" ②): 현재 값은 그룹 행, 판 표는 바뀔 때마다 그때 상태 전체를 남기는 이력,
    판마다 `ad.segment_changed` 하나 — 셋이 같은 트랜잭션이다."""

    def setUp(self):
        self.admin = make_admin()
        self.media = api.create_media(self.admin, "메타버스")

    def segment_events(self, segment_id):
        return list(EventOutbox.objects.filter(event_name="ad.segment_changed", aggregate_id=segment_id).order_by("created_at").values_list("event_id", "payload"))

    def case_create_writes_the_row_version_1_event_and_audit_together(self):
        g = api.create_segment(self.admin, self.media.media_id, "mlo", " MLO 과정 ", now=NOW)
        self.assertEqual((g.code, g.name, g.is_active, g.version, g.created_at), ("mlo", "MLO 과정", True, 1, NOW))
        v = SegmentVersion.objects.get(segment_id=g.segment_id)
        self.assertEqual((v.version, v.name, v.is_active, v.changed_by, v.changed_at), (1, "MLO 과정", True, self.admin.pk, NOW))
        [(event_id, payload)] = self.segment_events(g.segment_id)
        self.assertEqual(event_id, event_id_for("ad.segment_changed", v.segment_version_id))  # 원천 = 판 행
        self.assertEqual(payload, {"segment_id": str(g.segment_id), "segment_version_id": str(v.segment_version_id), "version": 1, "media_id": str(self.media.media_id),
                                   "code": "mlo", "name": "MLO 과정", "is_active": True, "changed_at": "2026-10-07T12:00:00+00:00"})  # 바꾼 관리자는 싣지 않는다
        audit = AuditLog.objects.get(action="masters.segment_created")
        self.assertEqual((audit.actor_id, audit.target_id, audit.detail["code"]), (self.admin.pk, str(g.segment_id), "mlo"))

    def case_the_code_must_match_the_format_and_is_never_trimmed(self):
        for bad in ("", "MLO", "1mlo", "-mlo", "ml_o", " mlo", "mlo ", "m" * 33, "과정", None, 7):
            with self.subTest(code=bad), self.assertRaises(InvalidRequest) as caught:
                api.create_segment(self.admin, self.media.media_id, bad, "이름")
            self.assertEqual(caught.exception.errors[0]["pointer"], "/code")
        for good in ("m", "mlo", "skn-2", "a" + "b" * 31):
            api.create_segment(self.admin, self.media.media_id, good, "이름")
        with self.assertRaises(IntegrityError), transaction.atomic():  # DB 도 형식을 지킨다(서비스를 거치지 않은 쓰기). 저장점 — 실패한 문장 뒤에도 시험 트랜잭션을 계속 쓴다
            Segment.objects.create(media_id=self.media.media_id, code="Bad", name="x")
        self.assertFalse(Segment.objects.filter(code="Bad").exists())

    def case_the_code_is_unique_per_media_but_free_across_media(self):
        api.create_segment(self.admin, self.media.media_id, "mlo", "MLO")
        with self.assertRaises(Conflict):
            api.create_segment(self.admin, self.media.media_id, "mlo", "다른 이름")
        other = api.create_media(self.admin, "다른 매체")
        api.create_segment(self.admin, other.media_id, "mlo", "MLO")  # 그룹은 매체별이다
        self.assertEqual(SegmentVersion.objects.count(), 2)  # 거절된 등록은 판도 이벤트도 남기지 않는다
        self.assertEqual(EventOutbox.objects.filter(event_name="ad.segment_changed").count(), 2)
        with self.assertRaises(NotFound):
            api.create_segment(self.admin, uuid.uuid4(), "skn", "SKN")
        with mock.patch("masters.services.events.record_segment_changed", side_effect=IntegrityError("outbox CHECK")), \
                self.assertRaises(IntegrityError) as caught:  # 코드 충돌이 아닌 무결성 오류는 409 로 바꾸지 않는다
            api.create_segment(self.admin, self.media.media_id, "aio", "AIO")
        self.assertNotIsInstance(caught.exception, Conflict)
        self.assertFalse(Segment.objects.filter(code="aio").exists())

    def case_the_code_cannot_be_changed(self):
        g = Segment.objects.get(pk=api.create_segment(self.admin, self.media.media_id, "mlo", "MLO").segment_id)
        g.code = "skn"
        with self.assertRaises(ValueError):
            g.save()
        self.assertEqual(Segment.objects.get(pk=g.pk).code, "mlo")

    def case_rename_archive_and_restore_each_add_a_full_version_and_an_event(self):
        g = api.create_segment(self.admin, self.media.media_id, "mlo", "MLO", now=NOW)
        g2 = api.update_segment(self.admin, g.segment_id, 1, name="MLO 과정", now=NOW + DAY)
        g3 = api.update_segment(self.admin, g.segment_id, 2, is_active=False, now=NOW + 2 * DAY)
        g4 = api.update_segment(self.admin, g.segment_id, 3, is_active=True, now=NOW + 3 * DAY)
        self.assertEqual([(x.version, x.name, x.is_active) for x in (g2, g3, g4)], [(2, "MLO 과정", True), (3, "MLO 과정", False), (4, "MLO 과정", True)])
        rows = list(SegmentVersion.objects.filter(segment_id=g.segment_id).order_by("version").values_list("version", "name", "is_active", "changed_at"))
        self.assertEqual(rows, [(1, "MLO", True, NOW), (2, "MLO 과정", True, NOW + DAY), (3, "MLO 과정", False, NOW + 2 * DAY), (4, "MLO 과정", True, NOW + 3 * DAY)])
        self.assertEqual([(p["version"], p["name"], p["is_active"], p["code"]) for _, p in self.segment_events(g.segment_id)],
                         [(1, "MLO", True, "mlo"), (2, "MLO 과정", True, "mlo"), (3, "MLO 과정", False, "mlo"), (4, "MLO 과정", True, "mlo")])
        current = Segment.objects.get(pk=g.segment_id)
        self.assertEqual((current.name, current.is_active), ("MLO 과정", True))  # 현재 값의 원본은 그룹 행
        self.assertEqual([x.version for x in api.list_segments(self.admin, self.media.media_id)], [4])
        changed = AuditLog.objects.filter(action="masters.segment_changed").order_by("log_id")  # UUIDv7 — 쓴 순서
        self.assertEqual([e.detail["previous"] for e in changed],
                         [{"name": "MLO", "is_active": True}, {"name": "MLO 과정", "is_active": True}, {"name": "MLO 과정", "is_active": False}])

    def case_a_change_that_changes_nothing_writes_nothing(self):
        g = api.create_segment(self.admin, self.media.media_id, "mlo", "MLO")
        for change in ({"name": " MLO "}, {"is_active": True}, {}):
            self.assertEqual(api.update_segment(self.admin, g.segment_id, 1, **change).version, 1, change)
        self.assertEqual(SegmentVersion.objects.count(), 1)
        self.assertFalse(AuditLog.objects.filter(action="masters.segment_changed").exists())

    def case_a_stale_version_or_a_bad_value_is_rejected_without_writing(self):
        g = api.create_segment(self.admin, self.media.media_id, "mlo", "MLO")
        api.update_segment(self.admin, g.segment_id, 1, name="MLO 2")
        for bad in (1, 3, None):
            with self.subTest(expected=bad), self.assertRaises(StaleVersion):
                api.update_segment(self.admin, g.segment_id, bad, is_active=False)
        for change in ({"name": " "}, {"is_active": "false"}, {"is_active": 0}):
            with self.subTest(change=change), self.assertRaises(InvalidRequest):
                api.update_segment(self.admin, g.segment_id, 2, **change)
        with self.assertRaises(NotFound):
            api.update_segment(self.admin, uuid.uuid4(), 1, name="x")
        self.assertEqual(SegmentVersion.objects.filter(segment_id=g.segment_id).count(), 2)
        self.assertTrue(Segment.objects.get(pk=g.segment_id).is_active)

    def case_an_event_failure_leaves_neither_the_row_nor_a_version(self):
        g = api.create_segment(self.admin, self.media.media_id, "mlo", "MLO")
        with mock.patch("masters.services.events.record_segment_changed", side_effect=RuntimeError("outbox down")):
            with self.assertRaises(RuntimeError):
                api.create_segment(self.admin, self.media.media_id, "skn", "SKN")
            with self.assertRaises(RuntimeError):
                api.update_segment(self.admin, g.segment_id, 1, is_active=False)
        self.assertFalse(Segment.objects.filter(code="skn").exists())
        self.assertEqual((Segment.objects.get(pk=g.segment_id).is_active, SegmentVersion.objects.count()), (True, 1))

    def case_only_admins_and_versions_are_append_only(self):
        g = api.create_segment(self.admin, self.media.media_id, "mlo", "MLO")
        adv = make_advertiser()
        for call in (lambda: api.create_segment(adv, self.media.media_id, "skn", "SKN"), lambda: api.update_segment(adv, g.segment_id, 1, name="x"),
                     lambda: api.list_segments(adv, self.media.media_id)):
            with self.assertRaises(Forbidden):
                call()
        v = SegmentVersion.objects.get()
        with self.assertRaises(AppendOnlyError):
            v.delete()
        with self.assertRaises(AppendOnlyError):
            SegmentVersion.objects.filter(pk=v.pk).update(name="x")
        with self.assertRaises(IntegrityError), transaction.atomic():  # 그룹 행은 지우지 않는다(판이 PROTECT 로 가리킨다 — ProtectedError 는 IntegrityError)
            Segment.objects.filter(pk=g.segment_id).delete()

    def test_segment_service(self):
        self.run_cases()


class ConcurrentSegmentTests(TransactionTestCase):
    def test_two_changes_from_the_same_version_one_wins(self):
        """같은 판에서 동시에 바꾸면 정확히 한쪽만 성공하고 진 쪽은 충돌 안내다(그룹 행 잠금 — 판 번호의 유일 제약 오류가 아니다)."""
        admin_a, admin_b = make_admin(), make_admin()
        g = api.create_segment(admin_a, api.create_media(admin_a, "매체").media_id, "mlo", "MLO")
        results, errors = run_together([lambda a=a, n=n: api.update_segment(a, g.segment_id, 1, name=n) for a, n in ((admin_a, "가"), (admin_b, "나"))])
        self.assertEqual(([type(e).__name__ for e in errors], sum(r is not None for r in results)), (["StaleVersion"], 1), errors)
        self.assertEqual(list(SegmentVersion.objects.order_by("version").values_list("version", flat=True)), [1, 2])
