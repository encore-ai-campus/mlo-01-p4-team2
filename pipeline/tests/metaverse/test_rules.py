"""pipeline/silver/adapters/metaverse_rules.py — 메타버스 계약(validate_raw_data 옮김)·내용 해시. 표준 라이브러리만(Spark 없이).

시험 값은 리허설 Bronze 실제 행(MD-2 본 실행, metaverse_lab)에서 가져왔다(15 §5.8).
"""
import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone

from pipeline.silver.adapters import metaverse_rules as rules

U = ["6211c9c3-3ae5-4856-b8b2-b2b493f8623c", "bb157f2b-9335-46ae-903b-d945617df4bb", "f6ca7b43-bc09-4c82-a7e4-f1a48bdc21f3",
     "8549763d-e235-481f-b189-43fdacf839e6", "e389216d-067b-4716-aebd-43173921983a", "b4845343-2701-481f-8cb3-1ce4290fea3f"]
BASE = {"user_uuid": U[0], "session_uuid": U[1], "class_group_uuid": U[2]}
EXTRA = {  # 종류마다 실제 행의 raw_data(공통 3필드 밖)
    "session_started": {"session_uuid": U[1]},
    "session_ended": {"end_reason": "LOGOUT"},
    "chat_sent": {"message_uuid": U[3], "channel_code": "GLOBAL", "floor_uuid": U[4]},
    "post_created": {"post_uuid": U[3], "revision": 1, "zone_uuid": None},
    "post_updated": {"post_uuid": U[3], "revision": 2, "zone_uuid": None},
    "post_deleted": {"post_uuid": U[3], "revision": 3, "zone_uuid": None},
    "post_read": {"post_uuid": U[3], "revision": 2, "zone_uuid": None},
    "comment_created": {"comment_uuid": U[4], "post_uuid": U[3], "parent_comment_uuid": None, "revision": 1},
    "comment_updated": {"comment_uuid": U[4], "post_uuid": U[3], "parent_comment_uuid": None, "revision": 2},
    "comment_deleted": {"comment_uuid": U[4], "post_uuid": U[3], "parent_comment_uuid": None, "revision": 3},
    "content_open_requested": {"content_uuid": U[3], "category_uuid": U[4], "domain": "LEARNING", "content_kind": "QUIZ", "zone_uuid": U[5],
                               "action": "OPEN_REQUESTED"},
    "quiz_answered": {"attempt_uuid": U[3], "question_uuid": U[4], "content_uuid": U[5], "question_type": "FILL_BLANK", "choice_uuid": None,
                      "is_correct": True},
    "point_granted": {"ledger_uuid": U[3], "attempt_uuid": U[4], "delta": 50, "balance_after": 50},
    "movement_observed": {"floor_uuid": U[4], "zone_uuid": None, "x": 501.5, "y": 896.5, "input_kind": "KEYBOARD"},
    "floor_transferred": {"from_floor_uuid": U[4], "to_floor_uuid": U[5], "zone_uuid": None},
}
VERSION = {"quiz_answered": "1.1"}
# 실제 Bronze 행(point_granted op c, MD-2 리허설)의 after — 해시 고정값의 입력
AFTER = {"outbox_uuid": "0bd85828-86a1-4fea-9db9-fd2a27f8aeb8", "event_type": "point_granted", "topic": "raw.points",
         "message_key": "f8fcaae5-a894-4b8e-82f8-6dc083223f1e",
         "raw_data": '{"delta": 20, "user_uuid": "f8fcaae5-a894-4b8e-82f8-6dc083223f1e", "ledger_uuid": "bb222fbb-b056-4dc1-882f-0fb7e84d60ca", '
                     '"attempt_uuid": "f77a77ab-066a-4390-bdba-e4205d64eee0", "session_uuid": "4a4827af-3c42-442d-8cfa-e0d406912720", '
                     '"balance_after": 20, "class_group_uuid": "11447094-0d6e-4a23-af95-046197f09176"}',
         "occurred_at": "2026-11-04T05:55:38.150035Z",
         "payload_json": '{"raw_data": {"delta": 20, "user_uuid": "f8fcaae5-a894-4b8e-82f8-6dc083223f1e", "ledger_uuid": "bb222fbb-b056-4dc1-882f-0fb7e84d60ca", '
                         '"attempt_uuid": "f77a77ab-066a-4390-bdba-e4205d64eee0", "session_uuid": "4a4827af-3c42-442d-8cfa-e0d406912720", '
                         '"balance_after": 20, "class_group_uuid": "11447094-0d6e-4a23-af95-046197f09176"}, '
                         '"outbox_uuid": "0bd85828-86a1-4fea-9db9-fd2a27f8aeb8", "raw_manifest": {"source": "platform_server_1", '
                         '"event_type": "point_granted", "fingerprint": "97daaf628928be23", "occurred_at": "2026-11-04T05:55:38.150Z", '
                         '"schema_version": "1.0", "fingerprint_algorithm": "xxh3_64"}}',
         "created_at": "2026-11-04T05:55:38.151910Z"}
SHA = "c07d33f2095085f28c5be70a1cecef9a162f613e47dab9558f6cd88b92deb8dc"  # 고정값 — MVR-1 이 같은 행에 같은 바이트를 내야 한다(15 §2.2). MVR-3a 에서 첫 열 이름이 outbox_uuid 로 바뀌어 다시 잼(전 값 f9b0cc28)


def raw(event_type, **changes):
    data = {**BASE, **EXTRA[event_type], **changes}
    return {key: value for key, value in data.items() if value is not ...}


def reason(event_type, data=None, *, version=None, topic=None, key=None):
    data = raw(event_type) if data is None else data
    key = key or (data.get("user_uuid") if isinstance(data, dict) else U[0])
    return rules.contract_reason(event_type, version or VERSION.get(event_type, "1.0"), topic or rules.TOPIC_BY_TYPE[event_type], key, data)


def result(**changes):
    after = {**AFTER, **changes}
    return rules.adapter_result(after["outbox_uuid"], after["event_type"], "1.0", after["topic"], after["message_key"], after["raw_data"],
                                after["payload_json"], after["occurred_at"], after["created_at"])


class Contract(unittest.TestCase):
    def test_every_published_kind_accepts_a_real_row(self):
        self.assertEqual(sorted(rules.TOPIC_BY_TYPE), sorted(EXTRA))
        for event_type in EXTRA:
            with self.subTest(event_type=event_type):
                self.assertIsNone(reason(event_type))

    def test_version_table(self):  # 지금 발행자의 판 15 + 수신측이 유지하는 quiz_answered 1.0(KAFKA_TOPICS.md:129)
        self.assertEqual(len(rules.FIELDS), 16)
        self.assertEqual({v for (t, v) in rules.FIELDS if t != "quiz_answered"}, {"1.0"})
        self.assertEqual(rules.FIELDS[("quiz_answered", "1.0")],
                         {"user_uuid", "session_uuid", "class_group_uuid", "attempt_uuid", "question_uuid", "content_uuid", "choice_uuid", "is_correct"})
        self.assertEqual(reason("post_created", version="1.1"), "계약 표에 없는 (종류, 판)")
        self.assertEqual(rules.raw_data_reason("unknown_kind", "1.0", dict(BASE)), "계약 표에 없는 (종류, 판)")

    def test_quiz_versions(self):
        multiple = raw("quiz_answered", question_type="MULTIPLE_CHOICE", choice_uuid=U[2])
        self.assertIsNone(reason("quiz_answered", multiple))
        self.assertIn("choice_uuid", reason("quiz_answered", raw("quiz_answered", question_type="MULTIPLE_CHOICE")))  # 객관식에 null
        self.assertEqual(reason("quiz_answered", raw("quiz_answered", choice_uuid=U[2])), "raw_data.choice_uuid 는 FILL_BLANK 에서 null 이어야 한다")
        v10 = raw("quiz_answered", question_type=..., choice_uuid=U[2])
        self.assertIsNone(reason("quiz_answered", v10, version="1.0"))
        self.assertIn("choice_uuid", reason("quiz_answered", raw("quiz_answered", question_type=...), version="1.0"))  # 1.0 은 null 불가
        self.assertIn("더 있음 [\"question_type\"]", reason("quiz_answered", multiple, version="1.0"))
        self.assertIn("빠짐 [\"question_type\"]", reason("quiz_answered", v10))  # 1.1 에 question_type 없음

    def test_nullable_fields(self):
        self.assertIsNone(reason("chat_sent", raw("chat_sent", floor_uuid=None)))
        self.assertIn("floor_uuid", reason("movement_observed", raw("movement_observed", floor_uuid=None)))  # chat_sent 만 null 허용
        self.assertIsNone(reason("post_created", raw("post_created", zone_uuid=U[5])))
        self.assertIsNone(reason("comment_created", raw("comment_created", parent_comment_uuid=U[5])))
        self.assertIn("user_uuid", reason("session_started", raw("session_started", user_uuid=None)))

    def test_value_rules(self):
        bad = [("post_created", "post_uuid", "1b4e28ba-2fa1-11d2-883f-0016d3cca427"),  # UUIDv1
               ("post_created", "post_uuid", U[3].upper()), ("post_created", "post_uuid", "{" + U[3] + "}"), ("post_created", "post_uuid", U[3].replace("-", "")),
               ("post_created", "post_uuid", 1), ("post_created", "revision", 0), ("post_created", "revision", True), ("post_created", "revision", "1"),
               ("post_created", "revision", 1.0), ("point_granted", "delta", 0), ("point_granted", "delta", 2**63), ("point_granted", "balance_after", -1),
               ("point_granted", "balance_after", 2**63), ("quiz_answered", "is_correct", 1), ("movement_observed", "x", -0.5),
               ("movement_observed", "x", True), ("movement_observed", "y", "1"), ("movement_observed", "y", 10**400), ("movement_observed", "y", float("inf")),
               ("session_ended", "end_reason", "logout"), ("chat_sent", "channel_code", None), ("content_open_requested", "action", "OPEN")]
        for event_type, key, value in bad:
            with self.subTest(event_type=event_type, key=key, value=value):
                self.assertEqual(reason(event_type, raw(event_type, **{key: value})), f"raw_data.{key} 값이 계약에 맞지 않는다")
        good = [("point_granted", "balance_after", 0), ("point_granted", "delta", 2**63 - 1), ("movement_observed", "x", 0), ("movement_observed", "y", 3)]
        for event_type, key, value in good:
            with self.subTest(event_type=event_type, key=key, value=value):
                self.assertIsNone(reason(event_type, raw(event_type, **{key: value})))

    def test_field_set(self):
        self.assertIn('더 있음 ["note"]', reason("post_read", raw("post_read", note="x")))
        self.assertIn('빠짐 ["revision"]', reason("post_read", raw("post_read", revision=...)))
        self.assertEqual(reason("post_read", ["not", "object"]), "raw_data 가 JSON 객체가 아니다")

    def test_topic_and_message_key(self):
        self.assertEqual(reason("post_read", topic="raw.chat"), "topic 이 그 종류의 앱 토픽이 아니다")
        self.assertEqual(reason("post_read", key=U[1]), "message_key 가 raw_data.user_uuid 와 다르다")


class Times(unittest.TestCase):
    def test_debezium_times_to_micros(self):
        for text, expected in [("2026-11-04T05:55:38.150035Z", "2026-11-04T05:55:38.150035Z"), ("2026-11-04T05:55:38.15Z", "2026-11-04T05:55:38.150000Z"),
                               ("2026-11-04T05:55:38Z", "2026-11-04T05:55:38.000000Z"), ("2024-02-29T23:59:59.000001Z", "2024-02-29T23:59:59.000001Z")]:
            with self.subTest(text=text):
                self.assertEqual(rules.utc_micros(rules.parse_utc(text)), expected)
        for text in ["2026-11-04T05:55:38.1500351Z", "2026-11-04T05:55:38+00:00", "2026-11-04 05:55:38Z", "2026-02-30T00:00:00Z",
                     "2026-11-04T05:55:38Z\n", "２026-11-04T05:55:38Z"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                rules.parse_utc(text)

    def test_aware_only(self):
        with self.assertRaises(ValueError):
            rules.utc_micros(datetime(2026, 11, 4, 5, 55, 38))
        kst = datetime(2026, 11, 4, 14, 55, 38, 150035, tzinfo=timezone(timedelta(hours=9)))
        self.assertEqual(rules.utc_micros(kst), "2026-11-04T05:55:38.150035Z")


class ContentHash(unittest.TestCase):
    def test_eight_columns_by_hand(self):  # 정수·ASCII 키뿐인 값이라 JCS = sort_keys·공백 없는 json.dumps
        content = {**{k: AFTER[k] for k in ("outbox_uuid", "event_type", "topic", "message_key", "occurred_at", "created_at")},
                   "raw_data": json.loads(AFTER["raw_data"]), "payload_json": json.loads(AFTER["payload_json"])}
        expected = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
        self.assertEqual(result(), (None, expected))
        self.assertEqual(rules.CONTENT_COLUMNS, ("outbox_uuid", "event_type", "topic", "message_key", "raw_data", "occurred_at", "payload_json", "created_at"))

    def test_pinned_value(self):
        self.assertEqual(result()[1], SHA)

    def test_same_content_same_hash(self):
        sha = result()[1]
        compact = json.dumps(json.loads(AFTER["raw_data"]), separators=(",", ":"), sort_keys=True)  # jsonb 출력과 공백·키 순서가 다름
        reordered = json.dumps(dict(reversed(list(json.loads(AFTER["payload_json"]).items()))))
        self.assertEqual(result(raw_data=compact, payload_json=reordered)[1], sha)
        self.assertEqual(result(occurred_at="2026-11-04T05:55:38.150035Z", created_at="2026-11-04T05:55:38.15191Z")[1], sha)  # 소수 끝 0 생략

    def test_any_content_change_changes_hash(self):
        sha = result()[1]
        changed = {"created_at": "2026-11-04T05:55:38.151911Z", "occurred_at": "2026-11-04T05:55:39.150035Z",
                   "payload_json": AFTER["payload_json"].replace("97daaf628928be23", "97daaf628928be24"),
                   "raw_data": AFTER["raw_data"].replace('"delta": 20', '"delta": 21').replace('"balance_after": 20', '"balance_after": 21'),
                   "outbox_uuid": "1bd85828-86a1-4fea-9db9-fd2a27f8aeb8"}
        for name, value in changed.items():
            with self.subTest(name=name):
                changed_reason, changed_sha = result(**{name: value})
                self.assertIsNone(changed_reason)
                self.assertNotEqual(changed_sha, sha)

    def test_never_raises(self):
        cases = [{"raw_data": None}, {"payload_json": None}, {"raw_data": "not json"}, {"raw_data": '{"x": NaN}'},
                 {"payload_json": '{"x": 1e999}'}, {"payload_json": "[" * 100000 + "]" * 100000}, {"created_at": "2026-11-04"},
                 {"raw_data": "[]"}, {"event_type": "unknown_kind"}]
        for case in cases:
            with self.subTest(case=str(case)[:40]):
                found_reason, found_sha = result(**case)
                self.assertTrue(found_reason)
                self.assertIsNone(found_sha)


if __name__ == "__main__":
    unittest.main()
