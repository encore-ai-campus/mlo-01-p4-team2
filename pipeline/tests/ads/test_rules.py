"""pipeline/silver/adapters/ads_rules.py — 광고 계약·내용 해시·tombstone ID(SL-8 계획 3판 §4.2). 표준 라이브러리만(Spark 없이).

원본은 광고 서버 `ads_server/events/schemas.py`(빌더)·`api.py`(enqueue — ast, 리뷰 조건 C4)다. 실제 행은 bronze_values.json, 합성 행은 rows.MADE.
"""
import hashlib
import inspect
import json
import unittest
from datetime import date

from pipeline.common import hop_rules
from pipeline.silver.adapters import ads_rules as rules

from ads import rows  # 시험 실행기(unittest discover pipeline/tests)는 pipeline/tests 를 뿌리로 모듈을 찾는다

S = rows.schemas
# 손으로 만든 6열 JCS(키 UTF-16 순, 공백 없음, payload 는 해석한 객체의 JCS) — 실제 행 BID(e2e ad.bid_added)
BID_JCS = ('{"aggregate_id":"01a1b3e9-ec06-7492-b7ea-54b25cd53e80","created_at":"2026-11-07T02:51:15.415668Z",'
           '"event_id":"f56fc86c-0392-5d06-b175-21a67f1e846c","event_name":"ad.bid_added",'
           '"payload":{"ad_group_id":"01a1b3e9-ec06-7492-b7ea-54b25cd53e80","amount":310,"bid_id":"01a1b3ea-da5a-74f4-ba5c-97d4389a5acb",'
           '"bid_version":2,"created_at":"2026-11-07T02:51:15.415668+00:00"},"schema_version":1}')
BID_SHA = hashlib.sha256(BID_JCS.encode("utf-8")).hexdigest()


def builders():
    return {name: f for name, f in vars(S).items() if name.endswith("_payload") and callable(f)}


def built(f, **over):
    """빌더를 None 값으로 부른 페이로드(과금 budget_day 는 날짜, 판정 basis 는 빈 dict)."""
    params = inspect.signature(f).parameters
    kwargs = {p: None for p in params if params[p].default is inspect.Parameter.empty}
    kwargs.update({k: v for k, v in {"budget_day": date(2026, 11, 9), "basis": {}}.items() if k in kwargs})
    return f(**{**kwargs, **over})


def after_of(value):
    return json.loads(value)["after"]


def every_row():
    """실제 행 13 + 합성 행 9 의 (이름, key, value)."""
    yield from ((name, r["key"], r["value"]) for name, r in rows.REAL.items())
    for name, (kind, payload) in rows.MADE.items():
        yield (name, *rows.made(kind, payload))


class Source(unittest.TestCase):  # 원본과 같음(계획 §4.2)
    def test_pairs(self):
        pairs = {(kind, version) for kind, version in S.SCHEMA_VERSIONS.items()} | {(S.DECISION, S.DECISION_BEFORE_SEGMENTS)}
        self.assertEqual(set(rules.CONTRACTS), pairs)
        self.assertEqual(len(rules.CONTRACTS), 10)

    def test_builder_keys(self):
        made = builders()
        self.assertEqual(len(made), 9)  # 빌더 수 = 종류 수 — 새 종류의 빌더가 생기면 여기서 실패한다
        self.assertEqual(len({kind for kind, _ in rules.CONTRACTS}), len(made))
        for name, f in made.items():
            payloads = [built(f)] if f is not S.decision_payload else [built(f), built(f, segment_rule="segment/v1")]
            for payload in payloads:
                kind = next(k for k, _ in rules.CONTRACTS if k.removeprefix("ad.") + "_payload" == name)
                version = S.schema_version_of(kind, payload)
                with self.subTest(kind=kind, version=version):
                    self.assertEqual(set(payload), rules.CONTRACTS[(kind, version)][0])
        self.assertEqual([len(rules.CONTRACTS[(S.DECISION, v)][0]) for v in (2, 3)] + [len(rules.CONTRACTS[(S.SEGMENT_CHANGED, 1)][0])], [10, 13, 8])

    def test_target_keys_come_from_api(self):  # 리뷰 조건 C4 — 대상 키의 원본은 api.py 의 enqueue 셋째 인자
        self.assertEqual(set(rows.ENQUEUE), set(S.SCHEMA_VERSIONS))
        for (kind, version), (keys, target, stamp) in rules.CONTRACTS.items():
            with self.subTest(kind=kind, version=version):
                self.assertEqual(target, rows.ENQUEUE[kind][1])
                self.assertIn(target, keys)
                self.assertIn(stamp, keys)
                self.assertIn(rows.ENQUEUE[kind][0], keys)  # 합성 행이 event_id 원천 ID 를 페이로드에서 얻는다
        self.assertEqual(rows.ENQUEUE[S.SEGMENT_CHANGED], ("segment_version_id", "segment_id"))

    def test_real_rows_follow_the_table(self):  # 실제 행의 키 집합·대상 키(2026-11-07 lab 실데이터와 같았던 8쌍)
        seen = set()
        for name, r in rows.REAL.items():
            a = after_of(r["value"])
            payload = json.loads(a["payload"])
            with self.subTest(name=name):
                self.assertIsNone(rules.contract_reason(a["event_name"], a["schema_version"], a["aggregate_id"], payload))
                seen.add((a["event_name"], a["schema_version"]))
        self.assertEqual(seen, set(rules.CONTRACTS) - {(S.DECISION, 3), (S.SEGMENT_CHANGED, 1)})  # 실제 Bronze 에 없는 두 쌍(계획 §1a)


class Hash(unittest.TestCase):
    def test_fixed_value(self):
        a = after_of(rows.REAL["BID"]["value"])
        self.assertEqual(hop_rules.after_identity(a), BID_JCS)
        self.assertEqual(rules.adapter_result(rows.REAL["BID"]["value"], rows.REAL["BID"]["key"]), (None, BID_SHA, None))

    def test_every_row_uses_the_hop_rule(self):
        for name, key, value in every_row():
            with self.subTest(name=name):
                reason, sha, key_id = rules.adapter_result(value, key)
                self.assertEqual((reason, key_id), (None, None))
                self.assertEqual(sha, hashlib.sha256(hop_rules.after_identity(after_of(value)).encode("utf-8")).hexdigest())

    def test_copy_r_and_payload_spacing_keep_the_hash(self):
        r = rows.REAL["DECISION"]
        doc = rows.bronze_doc("DECISION", op="r", source={"lsn": None, "txId": None})
        self.assertEqual(rules.adapter_result(json.dumps(doc), r["key"])[1], rules.adapter_result(r["value"], r["key"])[1])
        compact = rows.bronze_doc("DECISION", payload=json.dumps(json.loads(after_of(r["value"])["payload"]), separators=(",", ":")))
        self.assertEqual(rules.adapter_result(json.dumps(compact), r["key"])[1], rules.adapter_result(r["value"], r["key"])[1])  # jsonb 표기와 무관
        moved = rows.bronze_doc("DECISION", created_at="2026-11-07T02:51:59.336704Z")
        self.assertNotEqual(rules.adapter_result(json.dumps(moved), r["key"])[1], rules.adapter_result(r["value"], r["key"])[1])  # 1µs → 다른 해시


class Tombstone(unittest.TestCase):
    def test_key_event_id(self):
        e2e = rows.REAL["DECISION"]["key"]
        event_id = after_of(rows.REAL["DECISION"]["value"])["event_id"]
        upper = e2e.replace(event_id, event_id.upper())
        cases = [(e2e, event_id), (upper, event_id),  # 대문자 키는 hop 처럼 소문자로(리뷰 조건 C3)
                 (rows.REAL["LAB_OLD_KEY"]["key"], None),  # E-R1 C 전 키(aggregate_id)
                 (e2e.replace(event_id, "x"), None), (None, None), ('{"payload": {"event_id": 7}}', None), ("[" * 100000, None), ("not json", None)]
        for key, expected in cases:
            with self.subTest(key=None if key is None else key[:60]):
                self.assertEqual(rules.key_event_id(key), expected)
                self.assertEqual(rules.adapter_result(None, key), ("value 가 없다", None, expected))


class Contract(unittest.TestCase):
    def reason(self, name="DECISION", *, version=..., aggregate=..., payload=..., kind=...):
        a = after_of(rows.REAL[name]["value"]) if name in rows.REAL else None
        if a is None:
            event_name, made = rows.MADE[name]
            a = after_of(rows.made(event_name, made)[1])
        data = json.loads(a["payload"])
        return rules.contract_reason(a["event_name"] if kind is ... else kind, a["schema_version"] if version is ... else version,
                                     a["aggregate_id"] if aggregate is ... else aggregate, data if payload is ... else payload(data))

    def test_one_reason_each(self):
        cases = [
            (dict(payload=lambda d: {k: v for k, v in d.items() if k != "slot_id"}), 'payload 필드가 계약과 다르다(빠짐 ["slot_id"], 더 있음 [])'),
            (dict(payload=lambda d: {**d, "노트": 1}), 'payload 필드가 계약과 다르다(빠짐 [], 더 있음 ["노트"])'),
            (dict(aggregate="01a1b3eb-85f5-704b-9de3-9f5f4098b4ec"), "aggregate_id 가 payload.decision_id 와 다르다"),
            (dict(payload=lambda d: [d]), "payload 가 객체가 아니다"),
            (dict(version=1), "계약 표에 없는 (종류, 판)"),
            (dict(version=True), "계약 표에 없는 (종류, 판)"),  # True == 1 이지만 판이 아니다
            (dict(version=2.0), "계약 표에 없는 (종류, 판)"),
            (dict(kind="ad.unknown", version=1), "계약 표에 없는 (종류, 판)"),
            (dict(version=3), 'payload 필드가 계약과 다르다(빠짐 ["segment_active", "segment_id", "segment_rule"], 더 있음 [])'),  # 2판 모양에 판 3
        ]
        for kwargs, expected in cases:
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.reason(**kwargs), expected)
        self.assertEqual(self.reason("D3_ON", version=2), 'payload 필드가 계약과 다르다(빠짐 [], 더 있음 ["segment_active", "segment_id", "segment_rule"])')
        self.assertEqual(self.reason("SEG_FIRST", aggregate=rows.MADE["SEG_FIRST"][1]["segment_version_id"]), "aggregate_id 가 payload.segment_id 와 다르다")
        for name in rows.MADE:  # 합성 행은 모두 계약에 맞는다(대상 키 = api.py, 판 = schema_version_of)
            with self.subTest(name=name):
                self.assertIsNone(self.reason(name))

    def test_contract_reason_reaches_the_udf(self):
        doc = rows.bronze_doc("DECISION", aggregate_id="01a1b3eb-85f5-704b-9de3-9f5f4098b4ec")
        self.assertEqual(rules.adapter_result(json.dumps(doc), rows.REAL["DECISION"]["key"]), ("aggregate_id 가 payload.decision_id 와 다르다", None, None))


class NoException(unittest.TestCase):  # 리뷰 조건 C1 — 어떤 입력에도 예외 없음(Spark 는 오류로 간 행에도 UDF 를 부른다)
    def test_inputs(self):
        good = json.loads(rows.REAL["DECISION"]["value"])
        deep = "[" * 100000 + "]" * 100000

        def with_after(after):
            return json.dumps({**good, "after": after})

        values = [None, "", "not json", "[1]", "1", "null", '{"after": null}', '{"after": []}', with_after("x"), with_after({}),
                  with_after({**good["after"], "payload": 1}), with_after({**good["after"], "payload": "not json"}),
                  with_after({**good["after"], "payload": deep}), with_after({**good["after"], "payload": '{"a": NaN}'}),
                  with_after({**good["after"], "payload": '{"a": 1e999}'}), with_after({**good["after"], "payload": "[]"}),
                  with_after({**good["after"], "schema_version": None}), with_after({**good["after"], "event_name": ["x"]}),
                  '{"after": ' + deep + "}", b"bytes"]
        keys = [None, "", "not json", deep, '{"payload": []}', 7]
        for value in values:
            for key in keys:
                with self.subTest(value=repr(value)[:50], key=repr(key)[:30]):
                    reason, sha, key_id = rules.adapter_result(value, key)
                    self.assertIsNotNone(reason)
                    self.assertIsNone(sha)


if __name__ == "__main__":
    unittest.main()
