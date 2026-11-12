"""이벤트 outbox (Step 4-1a): 같은 트랜잭션에서만 쓰고, 이벤트 ID 는 결정적이며, 같은 이벤트는 한 줄만 남는다."""
import datetime
import json
import uuid

from django.db import transaction
from django.test import TestCase, TransactionTestCase, tag

from events import api as events
from events import schemas
from events.models import EventOutbox
from events.outbox import enqueue
from tests.support.cases import Cases

NOW = datetime.datetime(2026, 10, 10, 3, 0, 0, 123456, tzinfo=datetime.timezone.utc)
DECISION = uuid.UUID("0199ee00-0000-7000-8000-000000000001")


def decision_fields(**override):
    fields = dict(
        decision_id=DECISION, media_id=uuid.uuid4(), slot_id=uuid.uuid4(), decided_at=NOW, audience="individual", subject_id="s1",
        selected={"campaign_id": uuid.uuid4(), "ad_group_id": uuid.uuid4(), "creative_id": uuid.uuid4(), "charge_shares": {"IMPRESSION": 98, "CLICK": 1, "CONVERSION": 1},
                  "bid_id": uuid.uuid4(), "bid_version": 1, "bid_policy_id": uuid.uuid4(), "policy_version": 2, "bid_amount": 300, "applied_amount": 300},
        selection_policy="weighted-by-bid/v1", candidate_count=3, rejected_counts={"budget": 1},
    )
    return {**fields, **override}


@tag("security")
class EventIdTests(Cases, TestCase):
    def case_id_is_deterministic_per_source_and_kind(self):
        a = schemas.event_id_of(schemas.DECISION, DECISION)
        self.assertEqual(a, schemas.event_id_of(schemas.DECISION, DECISION))
        self.assertNotEqual(a, schemas.event_id_of(schemas.REACTION, DECISION))  # 종류가 다르면 다른 이벤트
        self.assertNotEqual(a, schemas.event_id_of(schemas.DECISION, uuid.uuid4()))  # 원천이 다르면 다른 이벤트
        self.assertEqual(a.version, 5)

    def case_reaction_received_and_judged_events_of_one_inbox_row_differ(self):
        inbox = uuid.uuid4()
        self.assertNotEqual(schemas.event_id_of(schemas.REACTION_RECEIVED, inbox), schemas.event_id_of(schemas.REACTION, inbox))

    def test_event_id(self):
        self.run_cases()


@tag("security")
class EnqueueTests(Cases, TestCase):
    def case_writes_one_row_with_the_envelope(self):
        event_id = events.record_decision(now=NOW, **decision_fields())
        row = EventOutbox.objects.get()
        self.assertEqual((row.event_id, row.event_name, row.schema_version, row.aggregate_id, row.sent_at), (event_id, "ad.decision", 2, DECISION, None))  # 대상 그룹 규칙 없는 결정 = v2(행마다 판)
        self.assertEqual(schemas.SCHEMA_VERSIONS, {  # 종류별(6-10c): 결정만 3(2: 단위 → `charge_shares`, 3: 대상 그룹 — TG-S3)
            "ad.decision": 3, "ad.reaction_received": 1, "ad.reaction": 1, "ad.charge": 1,
            "ad.bid_policy_changed": 1, "ad.bid_added": 1, "ad.campaign_changed": 1, "ad.ad_group_changed": 1, "ad.segment_changed": 1,
            "ad.ad_group_profile_changed": 1,
        })
        self.assertEqual(row.created_at, NOW)  # produced_at = outbox 에 쓴 시각
        self.assertEqual(row.payload["decision_id"], str(DECISION))
        json.dumps(row.payload)  # JSON 으로 직렬화된다

    def case_the_same_event_twice_leaves_one_unchanged_row(self):
        events.record_decision(now=NOW, **decision_fields())
        first = EventOutbox.objects.get()
        events.record_decision(now=NOW + datetime.timedelta(hours=1), **decision_fields(candidate_count=99))  # 재처리: 같은 원천 → 같은 이벤트
        row = EventOutbox.objects.get()
        self.assertEqual((row.payload, row.created_at), (first.payload, first.created_at))  # 처음 것이 그대로다

    def case_sent_rows_stay_sent_when_re_enqueued(self):
        events.record_decision(now=NOW, **decision_fields())
        EventOutbox.objects.update(sent_at=NOW)
        events.record_decision(now=NOW, **decision_fields())
        self.assertIsNotNone(EventOutbox.objects.get().sent_at)  # 이미 보낸 이벤트를 다시 대기열로 되돌리지 않는다

    def case_payload_shapes(self):
        p = schemas.decision_payload(**decision_fields(audience="shared", subject_id=None, selected=None))
        self.assertEqual((p["selected"], p["subject_id"], p["decided_at"]), (None, None, "2026-10-10T03:00:00.123456+00:00"))
        self.assertNotIn("owner_id", json.dumps(schemas.decision_payload(**decision_fields())))  # 광고주 계정은 싣지 않는다
        basis = {"person": "p1", "audience": "individual", "v": 2}
        r = schemas.reaction_payload(
            inbox_id=uuid.uuid4(), reaction_id=uuid.uuid4(), media_id=uuid.uuid4(), decision_id=DECISION, type="CLICK", occurred_at=NOW, received_at=NOW,
            subject_id="reported", source="media", status="ACCEPTED", reason_code=None, applied_rule=None, basis=basis,
        )
        self.assertEqual((r["person_id"], r["audience"], r["subject_id"], r["basis"]), ("p1", "individual", "reported", basis))  # 사람·슬롯 성격은 basis 에서
        r = schemas.reaction_payload(
            inbox_id=uuid.uuid4(), reaction_id=uuid.uuid4(), media_id=uuid.uuid4(), decision_id=DECISION, type="CLICK", occurred_at=NOW, received_at=NOW,
            subject_id="reported", source="media", status="REJECTED", reason_code="unknown_decision", applied_rule="known-decision",
            basis={"person": None, "audience": None, "v": 2},
        )
        self.assertEqual((r["person_id"], r["audience"]), (None, None))  # null 규칙
        c = schemas.charge_payload(
            entry_id=uuid.uuid4(), campaign_id=uuid.uuid4(), charge_policy="share/v1", created_at=NOW, reaction_ref="m:r", decision_id=DECISION,
            requested_amount=30, charged_amount=20, shortfall=10, budget_day=datetime.date(2026, 10, 10),
        )
        self.assertEqual((c["budget_day"], c["requested_amount"], c["shortfall"]), ("2026-10-10", 30, 10))
        g = schemas.ad_group_profile_changed_payload(ad_group_id=DECISION, version=1, category=None, weight_mlo=0, weight_aio=0, weight_skn=0, changed_at=NOW)
        self.assertEqual(list(g), ["ad_group_id", "version", "category", "weight_mlo", "weight_aio", "weight_skn", "changed_at"])  # 과정 순서 MLO → AIO → SKN, 바꾼 사람 없음
        self.assertEqual((g["category"], g["changed_at"]), (None, "2026-10-10T03:00:00.123456+00:00"))  # 분류 전은 null

    def case_received_event_carries_only_reported_facts(self):
        p = schemas.reaction_received_payload(
            inbox_id=uuid.uuid4(), reaction_id=uuid.uuid4(), media_id=uuid.uuid4(), decision_id=DECISION, type="IMPRESSION", occurred_at=NOW,
            received_at=NOW, subject_id=None, source="media", raw={"type": "IMPRESSION"},
        )
        self.assertNotIn("person_id", p)
        self.assertNotIn("audience", p)
        self.assertEqual(p["raw"], {"type": "IMPRESSION"})

    def case_change_events_carry_the_planned_fields_under_their_own_message_key(self):
        """정책·입찰·캠페인·광고그룹·광고 카테고리 이벤트(계획 docs/plans/00 §5.1): 필드는 표 그대로(사유·바꾼 사람·이름 없음), 대상 키는 종류별 대상.
        광고 카테고리는 원천 ID 가 판 행(O13) ID 가 아니라 (광고, 판) 문자열이다."""
        slot, policy, ad_group, bid, campaign, change = (uuid.uuid4() for _ in range(6))
        policy_fields = dict(slot_id=slot, bid_policy_id=policy, policy_version=1, floor_amount=100, base_amount=200, cap_amount=300,
                             impression_share=98, click_share=1, conversion_share=1, changed_at=NOW)
        change_fields = dict(change_id=change, change_type="STATUS_CHANGED", previous_value={"status": "ACTIVE"}, new_value={"status": "PAUSED"}, changed_at=NOW)
        cases = [  # (기록 함수, 값, 이벤트 이름, 원천 ID, 대상 키, payload 필드)
            (events.record_bid_policy_changed, policy_fields, "ad.bid_policy_changed", policy, slot,
             {"slot_id", "bid_policy_id", "policy_version", "floor_amount", "base_amount", "cap_amount", "impression_share", "click_share",
              "conversion_share", "changed_at", "change_source", "insight_id"}),
            (events.record_bid_added, dict(bid_id=bid, ad_group_id=ad_group, bid_version=2, amount=150, created_at=NOW), "ad.bid_added", bid, ad_group,
             {"bid_id", "ad_group_id", "bid_version", "amount", "created_at"}),
            (events.record_campaign_changed, dict(campaign_id=campaign, **change_fields), "ad.campaign_changed", change, campaign,
             {"campaign_id", "change_id", "change_type", "previous_value", "new_value", "changed_at"}),
            (events.record_ad_group_changed, dict(ad_group_id=ad_group, campaign_id=campaign, **change_fields), "ad.ad_group_changed", change, ad_group,
             {"ad_group_id", "campaign_id", "change_id", "change_type", "previous_value", "new_value", "changed_at"}),
            (events.record_ad_group_profile_changed, dict(ad_group_id=ad_group, version=3, category=12, weight_mlo=50, weight_aio=30, weight_skn=20, changed_at=NOW),
             "ad.ad_group_profile_changed", f"{ad_group}/3", ad_group, {"ad_group_id", "version", "category", "weight_mlo", "weight_aio", "weight_skn", "changed_at"}),
        ]
        for record, fields, name, source, key, names in cases:
            with self.subTest(name):
                event_id = record(now=NOW, **fields)
                row = EventOutbox.objects.get(pk=event_id)
                self.assertEqual((event_id, row.event_name, row.schema_version, row.aggregate_id), (schemas.event_id_of(name, source), name, 1, key))
                self.assertEqual(set(row.payload), names)
                json.dumps(row.payload)
        p = EventOutbox.objects.get(event_name="ad.bid_policy_changed").payload
        self.assertEqual((p["slot_id"], p["changed_at"], p["change_source"], p["insight_id"]), (str(slot), "2026-10-10T03:00:00.123456+00:00", "INITIAL", None))
        self.assertEqual(schemas.bid_policy_changed_payload(**{**policy_fields, "policy_version": 2})["change_source"], "MANUAL")  # 첫 버전만 INITIAL
        self.assertEqual(EventOutbox.objects.get(event_name="ad.ad_group_changed").payload["new_value"], {"status": "PAUSED"})  # 변경 로그 JSON 그대로

    def test_enqueue(self):
        self.run_cases()


class OutsideTransactionTests(Cases, TransactionTestCase):
    def case_refuses_to_write_outside_a_transaction(self):
        with self.assertRaises(events.OutsideTransaction):
            events.record_decision(now=NOW, **decision_fields())
        self.assertEqual(EventOutbox.objects.count(), 0)

    def case_rolls_back_with_the_business_data(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                events.record_decision(now=NOW, **decision_fields())
                raise RuntimeError("업무 데이터 저장 실패")
        self.assertEqual(EventOutbox.objects.count(), 0)  # 저장은 안 됐는데 이벤트만 남지 않는다

    def case_commits_with_the_business_data(self):
        with transaction.atomic():
            events.record_decision(now=NOW, **decision_fields())
        self.assertEqual(EventOutbox.objects.count(), 1)

    def case_outbox_error_other_than_duplicate_is_not_swallowed(self):
        """길이 초과 같은 오류가 INSERT IGNORE 처럼 경고로 삼켜지지 않는다."""
        with self.assertRaises(Exception):
            with transaction.atomic():
                enqueue("x" * 100, uuid.uuid4(), DECISION, {})
        self.assertEqual(EventOutbox.objects.count(), 0)

    def test_outside_transaction(self):
        self.run_cases()
