"""pipeline/silver/adapters/metaverse.py — Spark 시험: 메타버스 어댑터(판정 순서 ①~⑤·꺼낸 값·내용 해시)와 공통 틀 확인(check_adapted).

입력은 리허설 Bronze 실제 value(`bronze_values.json`, MD-2 본 실행)와 그것을 바꾼 것이다(15 §5.8).
"""
import json
import unittest
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from pyspark.sql import SparkSession, functions as F

from pipeline.silver import contracts, framework
from pipeline.silver.adapters import metaverse, metaverse_rules as rules

T0 = datetime(2026, 11, 4, 9, 39, 0, 7000, tzinfo=timezone.utc)
TOPIC = "metaverse.outbox.events"
LAB = {"db": "metaverse_lab", "schema": "public", "table": "eventing_eventoutbox"}
REAL = json.loads((Path(__file__).resolve().parent / "bronze_values.json").read_text(encoding="utf-8"))
SHA = "c07d33f2095085f28c5be70a1cecef9a162f613e47dab9558f6cd88b92deb8dc"  # test_rules 의 고정값(POINT_C)
OTHER = "4a4827af-3c42-442d-8cfa-e0d406912720"
DEEP = "__deep__"  # changed() 가 이 자리에 배열 1,000단을 넣는다(바깥 객체를 더하면 합계 1,001단 이상) — strict 는 통과하고, Spark try_parse_json 은 합계 1,001단부터 null(1,000단까지 해석)
NESTED_1000 = ("[" * 1000 + "]" * 1000).encode()


def setUpModule():
    global spark
    spark = SparkSession.builder.getOrCreate()


def changed(name="POINT_C", *, op=None, source=None, after=..., manifest=None, raw=None, payload=None, top=None, **fields):
    """실제 value 를 바꿔 바이트로. fields 는 after 열, raw 는 raw_data 객체 고치기, manifest 는 payload_json.raw_manifest 고치기,
    payload 는 payload_json 최상위 키 더하기, top 은 봉투 최상위 키 고치기. 값이 DEEP 이면 배열 1,000단이 된다(바깥 객체와 합계 1,001단 이상)."""
    doc = json.loads(REAL[name])
    doc["op"] = op or doc["op"]
    doc["source"].update(source or {})
    doc.update(top or {})
    if raw is not None:
        data = {**json.loads(doc["after"]["raw_data"]), **raw}
        doc["after"]["raw_data"] = json.dumps({k: v for k, v in data.items() if v is not ...})
    if manifest is not None or payload is not None:
        envelope = json.loads(doc["after"]["payload_json"])
        envelope["raw_manifest"].update(manifest or {})
        envelope.update(payload or {})
        doc["after"]["payload_json"] = json.dumps(envelope)
    doc["after"].update(fields)
    if after is None:
        doc["after"] = None
    value = json.dumps(doc).encode("utf-8")  # 안쪽 JSON 문자열의 자리는 \" 로 이스케이프돼 있다(괄호는 이스케이프가 필요 없음)
    return value.replace(f'\\"{DEEP}\\"'.encode(), NESTED_1000).replace(f'"{DEEP}"'.encode(), NESTED_1000)


def bronze(values):
    rows = [(b"k", None if v is None else bytearray(v), [], TOPIC, 0, i, T0, 0, T0, "bronze_ads_outbox@b4b69424d588", date(2026, 11, 4))
            for i, v in enumerate(values)]
    return spark.createDataFrame(rows, contracts.BRONZE_SCHEMA)


def old_name(name="POINT_C"):
    """MVR-3 전 이름: after 의 outbox_uuid 를 event_uuid 로(나머지 바이트 뜻은 그대로)."""
    doc = json.loads(REAL[name])
    doc["after"] = {("event_uuid" if key == "outbox_uuid" else key): value for key, value in doc["after"].items()}
    return json.dumps(doc).encode("utf-8")


POINT_ID = "0bd85828-86a1-4fea-9db9-fd2a27f8aeb8"
INPUT = [  # (Bronze value, 기대 — None 이면 정상, 아니면 (코드, 사유에 든 말, event_id))
    (REAL["POINT_C"].encode(), None),  # 0
    (REAL["POINT_R"].encode(), None),  # 1 재발행 사본(op r, lsn·txId null)
    (REAL["FILL"].encode(), None),  # 2 quiz_answered 1.1 FILL_BLANK(choice_uuid null)
    (REAL["MOVE"].encode(), None),  # 3 실수 x·y
    (REAL["CONTENT"].encode(), None),  # 4 zone_uuid 있음
    (None, ("not_insert", "tombstone", None)),  # 5 ①
    (b'{"op":"c"', ("decode", "JSON 문서가 아니다", None)),  # 6 ②
    (changed(source={"table": "auth_user"}, raw_data="not json"), ("foreign_source", "다른 출처: metaverse_lab.public.auth_user", POINT_ID)),  # 7 ③이 ④보다 먼저
    (changed(source={"db": "metaverse_prod"}), ("foreign_source", "다른 출처: metaverse_prod.public.eventing_eventoutbox", POINT_ID)),  # 8
    (changed(op="u"), ("not_insert", "op u", POINT_ID)),  # 9 ③ op
    (changed(after=None), ("not_insert", "after 가 없다", None)),  # 10 ③ after
    (changed(raw_data='{"delta": 20, "delta": 21}'), ("decode", "after.raw_data", POINT_ID)),  # 11 ④ 겹친 키
    (changed(payload_json=None), ("contract", "payload_json 이 NULL", POINT_ID)),  # 12 ⑤ 15 §5.4
    (changed(manifest={"schema_version": "2.0"}), ("unknown_schema", '["point_granted","2.0"]', POINT_ID)),  # 13
    (changed(event_type="badge_earned"), ("unknown_schema", '["badge_earned","1.0"]', POINT_ID)),  # 14
    (changed(topic="raw.chat"), ("contract", "계약: topic", POINT_ID)),  # 15
    (changed(message_key=OTHER), ("contract", "계약: message_key", POINT_ID)),  # 16
    (changed(raw={"note": "x"}), ("contract", '계약: raw_data 필드가 계약과 다르다(빠짐 [], 더 있음 ["note"])', POINT_ID)),  # 17
    (changed(occurred_at="2026-11-04T05:55:38.1500351Z"), ("contract", "꺼낼 수 없는 열: occurred_at", POINT_ID)),  # 18 7자리
    (changed(outbox_uuid=POINT_ID.upper()), ("contract", "event_id 가 소문자 UUID 가 아니다", None)),  # 19
    (changed(retry_count="0"), ("contract", "꺼낼 수 없는 열: _retry_count", POINT_ID)),  # 20
    (changed(published_at="2026-11-04T05:56:00.1Z", retry_count=2), None),  # 21 배달 2열만 다름
    (changed(created_at="2026-11-04T05:55:38.151911Z"), None),  # 22 내용이 다름(1µs)
    (changed("FILL", raw={"question_type": ..., "choice_uuid": OTHER}, manifest={"schema_version": "1.0"}), None),  # 23 quiz_answered 1.0
    (REAL["POINT_C"].encode() + b" x", ("decode", "JSON 문서가 아니다", None)),  # 24 ② 뒤따르는 내용
    (changed(raw_data='{"x": NaN}'), ("decode", "after.raw_data", POINT_ID)),  # 25 ④
    (changed(raw={"delta": 0}), ("contract", "계약: raw_data.delta 값이 계약에 맞지 않는다", POINT_ID)),  # 26
    (changed(manifest={"schema_version": "2.0"}, raw={"user_uuid": ...}), ("unknown_schema", '["point_granted","2.0"]', POINT_ID)),  # 27 새 판은 꺼내기 실패보다 먼저
    (changed(top={"ts_ms": DEEP}), ("decode", "value: Spark 가 해석하지 못함", None)),  # 28 ② strict 는 통과, Spark 는 못 읽음(멈춤이 아니라 계속)
    (changed(raw={"delta": DEEP}), ("decode", "after.raw_data: Spark 가 해석하지 못함", POINT_ID)),  # 29 ④
    (changed(payload={"extra": DEEP}), ("decode", "after.payload_json: Spark 가 해석하지 못함", POINT_ID)),  # 30 ④
]
INPUT += [(old_name(), ("contract", "꺼낼 수 없는 열: event_id", None))]  # 31 MVR-3 전 이름(event_uuid) — g2 는 새 이름 행만 받는다(계획 26 결정 G (나))
KINDS = sorted(key for key in REAL if key.islower())  # 나머지 11종의 실제 행(키 = 종류) — 위 5행과 합쳐 15종마다 정상 1 이상(15 §2 SL-4 끝 조건)
INPUT += [(REAL[key].encode(), None) for key in KINDS]  # 32~42
POINT_ONLY = {"ledger_uuid": ..., "attempt_uuid": ..., "delta": ..., "balance_after": ...}  # POINT_C 의 raw_data 에서 point_granted 필드를 뺀다
V4, V5 = "3f1c2b8e-5d4a-4e7b-9c6d-2a1b0c9d8e7f", str(uuid.uuid5(uuid.NAMESPACE_URL, "playdata:learning:cs:multiple-choice"))  # 행 ID, 카탈로그 ID
# 새 7종(EO-2 14b0c1d, MVR-3b)은 POINT_C 를 고쳐 만든 행이다. 값은 test_rules.EXTRA 와 같은 모양.
# 실제 앱 행 확인 = T1'' g2 실행(앱 c802671 — Bronze 56행 = 정상 56·오류 0, 새 7종 19행), 증거 12_silver_count.json.
# 그 증거에는 봉투(행 값)가 없어 이 행들을 실제 행으로 바꾸지 않는다(계획 35 §1.5·D9).
NEW_KINDS = {
    "event_progress_completed": {"session_uuid": None, "progress_uuid": V4, "event_definition_uuid": V5, "event_kind": "EASTER_EGG",
                                 "completion_order": None},  # 접속 없음(비동기 완료)
    "game_play_started": {"play_uuid": V4, "game_uuid": V5, "event_definition_uuid": None},
    "game_play_ended": {"play_uuid": V4, "game_uuid": V5, "event_definition_uuid": None, "status": "FINISHED", "duration_ms": 61000},
    "manager_greeted": {"interaction_uuid": V4, "event_definition_uuid": V5, "manager_code": "manager-a"},
    "manager_poked": {"interaction_uuid": V4, "event_definition_uuid": V5, "manager_code": "manager-a", "poke_count": 2},
    "story_action_recorded": {"story_record_uuid": V4, "story_key": "pepero-2026-room1-dialogue", "action_key": "yeongseok-intro",
                              "action_kind": "npc_dialogue"},
    "learning_session_completed": {"learning_session_id": 1, "subject": "CS", "question_type": "MULTIPLE_CHOICE", "difficulty": "ADVANCED",
                                   "total_questions": 10, "correct_count": 10},
}
NEW_START = len(INPUT)  # 43~49
INPUT += [(changed(event_type=t, topic="raw.event", raw={**POINT_ONLY, **fields}, manifest={"event_type": t}), None) for t, fields in NEW_KINDS.items()]


def python_result(value):
    a = json.loads(value)["after"]
    version = json.loads(a["payload_json"])["raw_manifest"]["schema_version"]
    return rules.adapter_result(a["outbox_uuid"], a["event_type"], version, a["topic"], a["message_key"], a["raw_data"], a["payload_json"],
                                a["occurred_at"], a["created_at"])


class Adapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bronze = bronze([v for v, _ in INPUT])
        cls.ok, cls.errors = framework.check_adapted(cls.bronze, *metaverse.adapt(cls.bronze, LAB), metaverse.DOMAIN)
        cls.rows = {r["offset"]: r for r in cls.ok.collect()}

    def test_order_and_codes(self):
        found = {r["offset"]: (r["error_code"], r["reason"], r["event_id"]) for r in self.errors.collect()}
        self.assertEqual(sorted(found), [i for i, (_, expected) in enumerate(INPUT) if expected])
        self.assertEqual(sorted(self.rows), [i for i, (_, expected) in enumerate(INPUT) if not expected])
        for offset, (code, needle, event_id) in ((i, e) for i, (_, e) in enumerate(INPUT) if e):
            with self.subTest(offset=offset, code=code):
                self.assertEqual((found[offset][0], found[offset][2]), (code, event_id))
                self.assertIn(needle, found[offset][1])

    def test_every_kind_has_a_real_row(self):  # 새 7종은 만든 행(실제 앱 행 확인은 T1'' — NEW_KINDS 머리말)
        real = {value.encode() for value in REAL.values()}
        kinds = {self.rows[i]["event_type"] for i, (v, _) in enumerate(INPUT) if v in real}  # 실제 행은 모두 정상이어야 self.rows 에 있다
        made = [self.rows[i]["event_type"] for i in range(NEW_START, len(INPUT))]
        self.assertEqual(made, list(NEW_KINDS))
        self.assertEqual(sorted(kinds), sorted(set(rules.TOPIC_BY_TYPE) - set(NEW_KINDS)))
        self.assertEqual(len(KINDS), 11)
        for i, (v, _) in enumerate(INPUT):
            if v in real:
                with self.subTest(offset=i):
                    self.assertEqual(self.rows[i]["event_type"], json.loads(v)["after"]["event_type"])

    def test_values_come_out_unchanged(self):
        row, after = self.rows[0], json.loads(REAL["POINT_C"])["after"]
        self.assertEqual({name: row[name] for name, _, _ in contracts.ADAPTER_FIELDS}, {
            "event_id": POINT_ID, "domain": "metaverse", "event_type": "point_granted", "schema_version": "1.0",
            "occurred_at": datetime(2026, 11, 4, 5, 55, 38, 150035), "produced_at": datetime(2026, 11, 4, 5, 55, 38, 151910),  # 실행기 TZ=UTC
            "actor_user_uuid": "f8fcaae5-a894-4b8e-82f8-6dc083223f1e", "actor_subject_id": None, "actor_person_id": None, "session_uuid": OTHER,
            "aggregate_id": None, "payload": after["raw_data"], "envelope": after["payload_json"],
            "source_meta": '{"topic":"raw.points","message_key":"f8fcaae5-a894-4b8e-82f8-6dc083223f1e","published_at":null,"retry_count":0}',
            "src_lsn": 10135584184, "src_txid": 2562162, "first_op": "c", "content_sha256": SHA})
        self.assertEqual(self.rows[2]["schema_version"], "1.1")
        self.assertEqual(self.rows[23]["schema_version"], "1.0")
        self.assertEqual((self.rows[NEW_START]["event_type"], self.rows[NEW_START]["session_uuid"]), ("event_progress_completed", None))  # 접속 없는 정상 행
        self.assertEqual(json.loads(self.rows[21]["source_meta"]),
                         {"topic": "raw.points", "message_key": "f8fcaae5-a894-4b8e-82f8-6dc083223f1e", "published_at": "2026-11-04T05:56:00.1Z", "retry_count": 2})

    def test_content_hash(self):
        for offset in self.rows:  # UDF 경로(Spark 가 꺼낸 문자열) = 순수 함수 경로 — 인자 순서까지 맞음
            with self.subTest(offset=offset):
                self.assertEqual(self.rows[offset]["content_sha256"], python_result(INPUT[offset][0])[1])
        self.assertEqual([self.rows[i]["content_sha256"] for i in (0, 1, 21)], [SHA] * 3)  # 재발행 사본·배달 2열만 다른 사본 → 같은 해시(copies)
        self.assertEqual((self.rows[1]["first_op"], self.rows[1]["src_lsn"], self.rows[1]["src_txid"]), ("r", None, None))
        self.assertNotEqual(self.rows[22]["content_sha256"], SHA)  # created_at 1µs → 다른 해시(conflict 길)

    def test_settings(self):
        for settings in ({"db": "metaverse_lab", "schema": "public"}, None, {**LAB, "table": ""}, {**LAB, "db": 1},
                         {**LAB, "extra": "x"}, {**LAB, "table": " eventing_eventoutbox"}):  # 뒤 둘은 control.source_settings 만 거부한다(지운 _source 는 받았다)
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                metaverse.adapt(self.bronze, settings)
        ok, errors = metaverse.adapt(self.bronze.where(F.col("offset") == 0), {**LAB, "db": "metaverse"})
        self.assertEqual((ok.count(), [r["error_code"] for r in errors.collect()]), (0, ["foreign_source"]))


if __name__ == "__main__":
    unittest.main()
