"""공용 요약 규칙의 원천별 설정(MVR-1, 계획 temp/review/MVR-1-plan.md 4판) — 표준 라이브러리만(Spark·Django 없이).

- 광고(`ADS`): 출력이 바꾸기 전(d7a5c72)과 바이트까지 같다 — `summarize_changes`·`summarize` 고정값(리터럴, 잰 방법은 Step 기록).
- 메타버스(`METAVERSE`): 내용 정체성이 Silver SL-4(`metaverse_rules.adapter_result`)와 바이트까지 같고(SL-4가 해시를 내는 행), 해석할 수 없는 행은 값 없이 멈추며(E1),
  계약 위반이지만 해석되는 행은 센다. 메타버스 요약 고정값은 MVR-2 DB 쪽이 맞출 기준이다.
사본(`pipeline.common.hop_rules`)을 시험한다 — 원본과 바이트가 같은지는 `test_shared_copies.py` 가 지킨다. `metaverse_rules` 와 같은 모듈 객체를 쓴다.
"""
import copy
import hashlib
import json
import traceback
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pipeline.common import hop_rules
from pipeline.silver.adapters import metaverse_rules

HERE = Path(__file__).resolve().parent
UTC = timezone.utc


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 광고 묶음(바꾸기 전 코드로 잰 고정값과 견준다)

ADS_NOW = datetime(2026, 11, 3, 12, 3, tzinfo=UTC)
ADS_CASES = (("hour", None), ("hour", datetime(2026, 11, 3, 9, tzinfo=UTC)), ("day", None), ("day", datetime(2026, 11, 2, tzinfo=UTC)))
AGG = "11111111-1111-7111-8111-111111111111"


def ads_row(offset, event_id, created, *, op="c", name="ad.decision", payload='{"a": 1}', table="events_outbox", db="lab", aggregate=AGG, tombstone=False, partition=0):
    key = json.dumps({"schema": {"type": "struct"}, "payload": {"event_id": event_id}})
    if tombstone:
        return key, None, partition, offset
    after = None if op in ("d", "t") else {"event_id": event_id, "event_name": name, "schema_version": 1, "aggregate_id": aggregate, "payload": payload, "created_at": created}
    return key, json.dumps({"before": None, "after": after, "source": {"db": db, "schema": "public", "table": table}, "op": op, "ts_ms": 0}), partition, offset


def ads_rows():
    e = [f"0000000{i:x}-0000-5000-8000-00000000000{i:x}" for i in range(16)]
    return [
        ads_row(0, e[1], "2026-11-02T10:15:00.123456Z"),
        ads_row(1, e[1], "2026-11-02T10:15:00.123456Z", op="r"),  # 같은 내용 — duplicates
        ads_row(2, e[2], "2026-11-02T10:20:00.5Z", payload='{"b": 1, "a": 2}'),
        ads_row(3, e[2], "2026-11-02T10:20:00.5Z", op="r", payload='{"a":2,"b":1.0}'),  # 공백·키 순서·1과 1.0 — 같은 내용
        ads_row(4, e[3], "2026-11-02T11:59:59.999999Z", name="ad.reaction"),
        ads_row(5, e[3], "2026-11-02T11:59:59.999999Z", name="ad.reaction", op="r", aggregate="22222222-2222-7222-8222-222222222222"),  # 충돌
        ads_row(6, e[4], "2026-11-02T10:30:00Z", op="u"),  # not_insert
        ads_row(7, e[5], None, tombstone=True),  # 놓을 수 없음
        ads_row(8, e[6], None, op="d"),  # 놓을 수 없음
        ads_row(9, e[7], "2026-11-02T10:31:00Z", table="other"),  # 다른 출처
        ads_row(10, e[8].upper(), "2026-11-03T09:30:00Z"),  # 소문자로 센다
        ads_row(11, e[9], "2026-11-03T08:30:00Z"),  # 시간 창 since 앞
        ads_row(12, e[10], "2026-11-03T12:01:00Z"),  # 열린 창
        ads_row(13, e[11], "2026-11-02T23:59:59.9995Z", name="ad.charge"),
        ads_row(14, e[12], "2026-11-02T10:00:00Z", name="ad.charge"),  # 창 시작 경계
        ads_row(15, e[13], "2026-11-02T10:40:00Z", op="r"),  # 재발행만
        ads_row(16, e[14], "2026-11-02T11:10:00Z", op="u"),
        ads_row(17, e[14], "2026-11-02T11:10:00Z"),  # 같은 ID의 u 와 c
        ads_row(18, e[15], "2026-11-01T05:00:00Z", partition=1),  # 하루 창 since 앞
        ads_row(19, "00000010-0000-5000-8000-000000000010", "2026-11-02T10:50:00.5Z"),
        ads_row(20, "00000010-0000-5000-8000-000000000010", "2026-11-02T10:50:00.500000Z", op="r"),  # 같은 시각의 다른 소수 표기 — 광고는 원문 비교라 충돌
    ]


def ads_outputs(module):
    """광고 묶음을 창 둘 × since 둘로 요약한 출력(줄·열린 창·checkpoint 추가 필드 — 예시 넷 포함)."""
    out = {}
    for window, since in ADS_CASES:
        lines, open_windows, extra = module.summarize_changes(ads_rows(), "lab", ADS_NOW, 120, since, module.WINDOWS[window])
        out[f"{window}/{since and since.isoformat()}"] = {"lines": lines, "open": [module.format_time(w) for w in open_windows], "extra": extra}
    return out


# ---------------------------------------------------------------- 내보내기 쪽 summarize 묶음(5필드 문서 — 광고 서버 hops.py·records/hops.py 가 쓰는 모양)

EXPORT_NOW = datetime(2026, 11, 3, 12, 2, tzinfo=UTC)  # 11시 창은 12:02 = 창 끝 + 여유 120초 — 닫힘 경계
EXPORT_CASES = (("hour", None), ("hour", datetime(2026, 11, 3, 11, tzinfo=UTC)), ("day", None), ("day", datetime(2026, 11, 2, tzinfo=UTC)))


def export_docs():
    def doc(event_id, produced, payload, name="ad.decision"):
        return {"event_id": event_id, "event_name": name, "schema_version": 1, "produced_at": produced, "payload": payload}
    return [
        doc("AAAAAAAA-0000-5000-8000-000000000001", "2026-11-03T11:00:00.000Z", {"k": "가"}),  # 소문자가 아닌 ID, 창 시작·since 경계
        doc("00000000-0000-5000-8000-000000000002", "2026-11-03T11:30:00.000Z", {"n": 2}),
        doc("00000000-0000-5000-8000-000000000002", "2026-11-03T11:45:00.000Z", {"n": 1}),  # 같은 ID 두 문서 — 들어온 순서가 해시에 들어간다
        doc("00000000-0000-5000-8000-000000000003", "2026-11-03T11:59:59.999Z", {"x": [1, 2.5, None]}),  # 창 끝 경계
        doc("00000000-0000-5000-8000-000000000004", "2026-11-03T12:00:00.000Z", {}),  # 열린 창
        doc("00000000-0000-5000-8000-000000000005", "2026-11-03T10:59:59.999Z", {"b": True}, name="ad.charge"),  # since 앞(시간)
        doc("00000000-0000-5000-8000-000000000006", "2026-11-02T09:00:00.000Z", {"z": 1e21}),
        doc("00000000-0000-5000-8000-000000000007", "2026-11-01T23:59:59.999Z", {"z": -0.0}),  # since 앞(하루)
    ]


def export_outputs(module):
    out = {}
    for window, since in EXPORT_CASES:
        lines, open_windows = module.summarize(export_docs(), EXPORT_NOW, 120, since, module.WINDOWS[window])
        out[f"{window}/{since and since.isoformat()}"] = {"lines": lines, "open": [module.format_time(w) for w in open_windows]}
    return out


# 바꾸기 전 코드(d7a5c72 의 hop_rules.py, sha256 a6f01f31…8b54)로 잰 값 — 잰 명령은 Step 기록(temp/review/MVR-1-check.md)
ADS_DIGESTS = {
    "hour/None": "1aa51332527e35894a8639ab704f71daa92efc5d00fbcc3122c769cf67cb709e",
    "hour/2026-11-03T09:00:00+00:00": "caa261b43c61726df60f6fa1b69bb83d18fb0beab03aa3d4d8a5185db56e2e9f",
    "day/None": "4d28e9a8851bdc994ed4bcec882d2f2f0b4ef8d8e06a3e7044c150ddeb9fb6b0",
    "day/2026-11-02T00:00:00+00:00": "01df3c9ccb02b725903a255d0fe49e4703b17ad07d72aa0e7e286df3edcf324a",
}
EXPORT_DIGESTS = {
    "hour/None": "a6d4a448262dd459c3653b44e03e8b07b37abbd9c968d476d17d7c5898636f8c",
    "hour/2026-11-03T11:00:00+00:00": "d0ef207d8e61bfe93cf87e86c940efc7a2287188783c2bd9be09ae5052c0489b",
    "day/None": "a55d6feb6813ef11a1475b019b424696bf5b28c9c14f7dd3a0787f42a8907149",
    "day/2026-11-02T00:00:00+00:00": "c3d3b89f44f18850f3ee5d4675741ddac92f01b0fc53f75d413abf2782c209f3",
}


# ---------------------------------------------------------------- 메타버스 묶음(리허설 Bronze 실제 행 16 — pipeline/tests/metaverse/bronze_values.json, 읽기만)

MV_DB = "metaverse_lab"
MV_NOW = datetime(2026, 11, 4, 8, tzinfo=UTC)
MV_CASES = (("hour", None), ("hour", datetime(2026, 11, 2, 4, tzinfo=UTC)), ("day", None))
REAL = json.loads((HERE.parent / "metaverse" / "bronze_values.json").read_text(encoding="utf-8"))


def mv_envelope(name="POINT_C", *, op=None, table="eventing_eventoutbox", db=MV_DB, **after):
    envelope = json.loads(REAL[name])
    envelope["after"].update(after)
    envelope["source"].update(db=db, table=table)
    if op is not None:
        envelope["op"] = op
    return envelope


def mv_row(offset, envelope, partition=0):
    outbox_uuid = (envelope.get("after") or {}).get("outbox_uuid", "00000000-0000-4000-8000-000000000000")
    key = json.dumps({"schema": {"type": "struct"}, "payload": {"outbox_uuid": outbox_uuid}})
    return key, json.dumps(envelope), partition, offset


def silver_hash(after):
    """Silver SL-4 의 실제 경로(`adapter_result` — Debezium 문자열 그대로 받음)가 내는 내용 해시(사유가 있으면 None)."""
    version = json.loads(after["payload_json"])["raw_manifest"]["schema_version"] if isinstance(after.get("payload_json"), str) else None
    reason, sha = metaverse_rules.adapter_result(after.get("outbox_uuid"), after.get("event_type"), version, after.get("topic"), after.get("message_key"),
                                                 after.get("raw_data"), after.get("payload_json"), after.get("occurred_at"), after.get("created_at"))
    return sha


def hop_hash(after):
    return hashlib.sha256(hop_rules.canonical_json(hop_rules._content(after, hop_rules.METAVERSE)).encode("utf-8")).hexdigest()


def mv_rows():
    """중복(c·r)·충돌(8열 다름)·배달 2열만 다른 사본(충돌 아님)·not_insert·tombstone(키 outbox_uuid)·다른 출처·열린 창·계약 위반(센다)·대문자 UUID."""
    rows = [mv_row(i, json.loads(REAL[name])) for i, name in enumerate(REAL)]  # 16(POINT_R 은 POINT_C 의 재발행 사본)
    n = len(rows)
    rows += [
        mv_row(n, mv_envelope("FILL", op="r", published_at="2026-11-02T04:16:21.000000Z", retry_count=3)),  # 배달 2열만 다름 — duplicates
        mv_row(n + 1, mv_envelope("MOVE", op="r", topic="raw.account")),  # 8열 다름 — 충돌
        mv_row(n + 2, mv_envelope("CONTENT", op="u")),  # not_insert
        ('{"schema": {}, "payload": {"outbox_uuid": "AAAAAAAA-0000-4000-8000-0000000000AA"}}', None, 1, 0),  # tombstone(키의 outbox_uuid 를 예시에)
        mv_row(n + 4, mv_envelope("chat_sent", table="events_outbox")),  # 다른 출처(표 이름)
        mv_row(n + 5, mv_envelope("chat_sent", outbox_uuid="11111111-1111-4111-8111-111111111111", created_at="2026-11-04T07:59:00Z")),  # 열린 창(시간)
        mv_row(n + 6, mv_envelope("post_read", outbox_uuid="22222222-2222-4222-8222-222222222222", topic="raw.wrong")),  # 계약 위반(토픽) — 센다
        mv_row(n + 7, mv_envelope("post_read", outbox_uuid="33333333-3333-4333-8333-33333333333A".upper())),  # 대문자 UUID — 정렬·ID 해시는 소문자
    ]
    return rows


def mv_outputs():
    out = {}
    for window, since in MV_CASES:
        lines, open_windows, extra = hop_rules.summarize_changes(mv_rows(), MV_DB, MV_NOW, 120, since, hop_rules.WINDOWS[window], source=hop_rules.METAVERSE)
        out[f"{window}/{since and since.isoformat()}"] = {"lines": lines, "open": [hop_rules.format_time(w) for w in open_windows], "extra": extra}
    return out


# 메타버스 요약 고정값 — MVR-2 의 DB 쪽(같은 8열 객체)이 맞출 기준. MVR-3a(첫 열 event_uuid → outbox_uuid) 코드로 다시 잰 값 —
# 옛 값(MVR-1)과는 이름을 맞추면 content_sha256 말고 같다(잰 방법은 Step 기록 temp/review/MVR-3a-check.md)
MV_DIGESTS = {
    "hour/None": "cccdcdb2c2cbb03cd4a3ea290891fbe61d145c525aec693655cba877121f841c",
    "hour/2026-11-02T04:00:00+00:00": "e105bdf168530096242b75e3956b458a4c43586962ee5b7f5ebe952d55ad3ba8",
    "day/None": "d94d75fde5550e862327fdd9b202095e8314978ba5e4f30348e67d2e89434aee",
}


class AdsUnchangedTests(unittest.TestCase):
    def test_summarize_changes_bytes_match_the_code_before_mvr1(self):
        self.assertEqual({case: digest(value) for case, value in ads_outputs(hop_rules).items()}, ADS_DIGESTS)

    def test_export_summarize_bytes_match_the_code_before_mvr1(self):
        self.assertEqual({case: digest(value) for case, value in export_outputs(hop_rules).items()}, EXPORT_DIGESTS)

    def test_source_is_keyword_only_and_defaults_to_ads(self):
        rows = ads_rows()
        with self.assertRaises(TypeError):
            hop_rules.summarize_changes(rows, "lab", ADS_NOW, 120, None, hop_rules.HOUR, hop_rules.ADS)
        self.assertEqual(hop_rules.summarize_changes(rows, "lab", ADS_NOW, 120, None, hop_rules.HOUR, source=dict(hop_rules.ADS)),
                         hop_rules.summarize_changes(rows, "lab", ADS_NOW, 120, None, hop_rules.HOUR))  # 설정은 값으로 읽는다(복사본도 같다)


    def test_copies_that_differ_only_in_id_case_are_one_event(self):
        """대문자 c 사본과 소문자 r 사본은 한 이벤트로 묶인다 — 묶기와 예시의 event_id 는 소문자(광고·메타버스 같음). 두 원천 모두 내용 정체성에는 ID 원문이 들어
        있어(광고 after_identity 6열·메타버스 내용 8열) 내용이 달라 충돌이다(바꾸기 전 광고 코드와 같음) — 소문자로 묶지 않으면 두 이벤트로 count 2 가 된다."""
        upper = "ABCDEF01-0000-5000-8000-0000000000AB"
        ads = [ads_row(0, upper, "2026-11-02T10:15:00Z"), ads_row(1, upper.lower(), "2026-11-02T10:15:00Z", op="r")]
        lines, _, extra = hop_rules.summarize_changes(ads, "lab", ADS_NOW, 120, size=hop_rules.HOUR)
        self.assertEqual([(line["count"], line["duplicates"], line["conflicts"]) for line in lines], [(0, 0, 1)])
        self.assertEqual(extra["conflict_examples"], [{"event_id": upper.lower(), "partition": 0, "offset": 0}, {"event_id": upper.lower(), "partition": 0, "offset": 1}])
        mv_upper = "abcdef01-0000-4000-8000-0000000000ab".upper()
        base = json.loads(REAL["POINT_C"])["after"]
        mv = [mv_row(0, mv_envelope(outbox_uuid=mv_upper)), mv_row(1, mv_envelope(op="r", outbox_uuid=mv_upper.lower()))]
        lines, _, extra = hop_rules.summarize_changes(mv, MV_DB, MV_NOW, 120, size=hop_rules.HOUR, source=hop_rules.METAVERSE)
        self.assertEqual([(line["count"], line["duplicates"], line["conflicts"]) for line in lines], [(0, 0, 1)])
        self.assertEqual(extra["conflict_examples"], [{"event_id": mv_upper.lower(), "partition": 0, "offset": 0}, {"event_id": mv_upper.lower(), "partition": 0, "offset": 1}])
        self.assertNotEqual(base["outbox_uuid"], mv_upper.lower())

    def test_malformed_documents_fail_where_they_did_before(self):
        """이름·ID 는 since·열린 창 판정 뒤에만 읽는다 — 형식이 깨진 5필드 문서의 실패 시점·메시지가 바꾸기 전과 같다(Reviewer MVR-1 코드 리뷰 작은 것 2)."""
        ok = {"event_id": "a", "event_name": "x", "schema_version": 1, "produced_at": "2026-11-03T11:10:00.000Z", "payload": {}}

        def run(*docs):
            try:
                lines, open_windows = hop_rules.summarize(list(docs), EXPORT_NOW, 120, datetime(2026, 11, 3, 11, tzinfo=UTC), hop_rules.HOUR)
                return "ok", len(lines), [hop_rules.format_time(w) for w in open_windows]
            except KeyError as error:
                return "KeyError", error.args[0]
        self.assertEqual(run(ok, {"event_id": "b", "schema_version": 1, "produced_at": "2026-11-03T12:00:00.000Z", "payload": {}}), ("ok", 1, ["2026-11-03T12:00:00Z"]))  # 열린 창
        self.assertEqual(run(ok, {"event_name": "x", "schema_version": 1, "produced_at": "2026-11-03T10:00:00.000Z", "payload": {}}), ("ok", 1, []))  # since 앞
        self.assertEqual(run(ok, {"event_name": "x", "schema_version": 1, "payload": {}}), ("KeyError", "produced_at"))
        self.assertEqual(run(ok, {"event_name": "x", "schema_version": 1, "produced_at": "2026-11-03T11:20:00.000Z", "payload": {}}), ("KeyError", "event_id"))


class MetaverseIdentityTests(unittest.TestCase):
    def test_every_real_row_hashes_like_silver(self):
        for name, value in REAL.items():
            with self.subTest(row=name):
                after = json.loads(value)["after"]
                self.assertIsNotNone(silver_hash(after))
                self.assertEqual(hop_hash(after), silver_hash(after))

    def test_made_rows_hash_like_silver(self):
        base = json.loads(REAL["POINT_C"])["after"]
        payload = json.loads(base["payload_json"])
        variants = {
            "소수 0자리": {"occurred_at": "2026-11-04T05:55:38Z", "created_at": "2026-11-04T05:55:38Z"},
            "소수 1자리": {"occurred_at": "2026-11-04T05:55:38.1Z"},
            "소수 3자리": {"created_at": "2026-11-04T05:55:38.150Z"},
            "소수 5자리": {"occurred_at": "2026-11-04T05:55:38.15003Z"},
            "JSON 공백·키 순서": {"raw_data": json.dumps(json.loads(base["raw_data"]), sort_keys=True, separators=(",", ":"))},
            "1 과 1.0·비 ASCII": {"payload_json": json.dumps(payload | {"extra": {"n": 1.0, "s": "가나 é"}}, ensure_ascii=False)},
            "대문자 UUID": {"outbox_uuid": base["outbox_uuid"].upper()},
        }
        for label, change in variants.items():
            with self.subTest(variant=label):
                after = base | change
                self.assertIsNotNone(silver_hash(after))
                self.assertEqual(hop_hash(after), silver_hash(after))
        self.assertEqual(hop_hash(base | {"payload_json": json.dumps(payload | {"extra": {"n": 1}})}),
                         hop_hash(base | {"payload_json": json.dumps(payload | {"extra": {"n": 1.0}})}))  # JCS 에서 1 과 1.0 은 같다

    def test_delivery_columns_do_not_change_the_identity(self):
        base = json.loads(REAL["FILL"])["after"]
        self.assertEqual(hop_hash(base), hop_hash(base | {"published_at": "2026-11-02T04:16:21Z", "retry_count": 3}))

    def test_rejected_times_are_rejected_on_both_sides(self):
        base = json.loads(REAL["POINT_C"])["after"]
        for bad in ("2026-11-04T05:55:38.150035+00:00", "2026-11-04T05:55:38.1500351Z", "2026-11-04T05:55:38.150035", "2026-02-30T05:55:38.150035Z"):
            for column in ("occurred_at", "created_at"):
                with self.subTest(time=bad, column=column):
                    self.assertIsNone(silver_hash(base | {column: bad}))
                    with self.assertRaises(ValueError):
                        hop_rules.summarize_changes([mv_row(0, mv_envelope(**{column: bad}))], MV_DB, MV_NOW, 120, size=hop_rules.HOUR, source=hop_rules.METAVERSE)


class MetaverseSummaryTests(unittest.TestCase):
    def test_metaverse_summary_bytes_are_pinned(self):
        self.assertEqual({case: digest(value) for case, value in mv_outputs().items()}, MV_DIGESTS)

    def test_metaverse_counts(self):
        lines, open_windows, extra = hop_rules.summarize_changes(mv_rows(), MV_DB, MV_NOW, 120, None, hop_rules.HOUR, source=hop_rules.METAVERSE)
        by = {(line["window_start"], line["event_name"]): line for line in lines}
        self.assertEqual(by[("2026-11-04T05:00:00Z", "point_granted")]["duplicates"], 1)  # POINT_C·POINT_R
        self.assertEqual(by[("2026-11-02T04:00:00Z", "quiz_answered")]["duplicates"], 1)  # 배달 2열만 다른 사본 — 충돌 아님
        self.assertEqual(by[("2026-11-02T04:00:00Z", "quiz_answered")]["conflicts"], 0)
        self.assertEqual(by[("2026-11-03T02:00:00Z", "movement_observed")]["conflicts"], 1)
        self.assertEqual(by[("2026-11-03T02:00:00Z", "movement_observed")]["count"], 0)
        self.assertEqual(by[("2026-11-02T04:00:00Z", "content_open_requested")]["not_insert"], 1)
        self.assertEqual(by[("2026-11-02T05:00:00Z", "post_read")]["count"], 3)  # 실제 1 + 계약 위반(토픽) 1 + 대문자 UUID 1 — 계약 위반도 센다
        self.assertEqual(open_windows, [datetime(2026, 11, 4, 7, tzinfo=UTC)])
        self.assertEqual((extra["not_insert_unplaced"], extra["foreign_source_rows"]), (1, 1))
        self.assertEqual(extra["not_insert_unplaced_examples"], [{"event_id": "aaaaaaaa-0000-4000-8000-0000000000aa", "partition": 1, "offset": 0}])

    def test_document_is_the_content_object_and_ids_are_lowercase(self):
        upper = mv_envelope("post_read", outbox_uuid="33333333-3333-4333-8333-33333333333A")
        lines, _, _ = hop_rules.summarize_changes([mv_row(0, upper)], MV_DB, MV_NOW, 120, size=hop_rules.HOUR, source=hop_rules.METAVERSE)
        after = upper["after"]
        self.assertEqual(lines[0]["ids_sha256"], hashlib.sha256(after["outbox_uuid"].lower().encode("utf-8")).hexdigest())
        self.assertEqual(lines[0]["content_sha256"], hashlib.sha256(hop_rules.canonical_json(hop_rules._content(after, hop_rules.METAVERSE)).encode("utf-8")).hexdigest())
        self.assertIn('"outbox_uuid":"33333333-3333-4333-8333-33333333333A"', hop_rules.canonical_json(hop_rules._content(after, hop_rules.METAVERSE)))  # 객체 안은 원문


class MetaverseStopTests(unittest.TestCase):
    """E1 (가): 해석할 수 없는 행은 요약하지 않고 멈춘다 — 메시지에 원천·event_id·위치·예외 종류, 값은 traceback 어디에도 없다."""

    def stop(self, envelope, offset=7, partition=2):
        with self.assertRaises(ValueError) as caught:
            hop_rules.summarize_changes([mv_row(offset, envelope, partition)], MV_DB, MV_NOW, 120, size=hop_rules.HOUR, source=hop_rules.METAVERSE)
        return caught.exception, "".join(traceback.format_exception(caught.exception))

    def test_each_unreadable_row_stops_without_values(self):
        base = json.loads(REAL["POINT_C"])["after"]
        payload = json.loads(base["payload_json"])
        deep = "[" * 1000 + "]" * 1000
        cases = {
            "NaN": ({"payload_json": json.dumps(payload).replace('"delta": 20', '"delta": NaN', 1)}, "ValueError"),
            "깊은 중첩": ({"raw_data": json.dumps(json.loads(base["raw_data"]) | {"deep": "__DEEP__"}).replace('"__DEEP__"', deep)}, "RecursionError"),
            "시각 모양": ({"occurred_at": "2026-11-04 05:55:38.150035+00:00"}, "ValueError"),
            "JSON 아님": ({"raw_data": "{user_uuid: " + base["message_key"]}, "JSONDecodeError"),
            "빈 문자열": ({"payload_json": ""}, "JSONDecodeError"),  # NULL 만 None 으로 센다(E3)
        }
        secrets = (base["message_key"], "2026-11-04 05:55:38.150035", "user_uuid", "__DEEP__", "NaN")
        for label, (change, kind) in cases.items():
            with self.subTest(case=label):
                envelope = mv_envelope(**change, outbox_uuid=base["outbox_uuid"].upper())  # 메시지의 event_id 는 소문자
                if label == "NaN":
                    self.assertIn("NaN", envelope["after"]["payload_json"])
                error, text = self.stop(envelope)
                self.assertIn("원천 METAVERSE", str(error))
                self.assertIn(f"event_id {base['outbox_uuid']}", str(error))
                self.assertIn("partition 2, offset 7", str(error))
                self.assertIn(f"사유 {kind}", str(error))
                self.assertIsNone(error.__cause__)
                self.assertTrue(error.__suppress_context__)
                for secret in secrets:
                    self.assertNotIn(secret, text)

    def test_missing_columns_stop_and_a_missing_id_is_reported_as_none(self):
        for column in ("topic", "raw_data", "occurred_at", "outbox_uuid"):
            with self.subTest(column=column):
                envelope = mv_envelope()
                del envelope["after"][column]
                error, text = self.stop(envelope)
                self.assertIn("사유 KeyError", str(error))
                if column == "outbox_uuid":
                    self.assertIn("event_id None", str(error))
                self.assertNotIn(envelope["after"]["message_key"], text)

    def test_null_json_columns_are_counted_as_json_null(self):
        """E3 (가)(사용자 2026-11-05): payload_json·raw_data 의 NULL 은 JSON null(None)로 센다 — 앱이 payload_json 을 NULL 로 넣을 수 있다. Silver 는 오류 표로 보낸다."""
        rows = [mv_row(0, mv_envelope("POINT_C", payload_json=None)),
                mv_row(1, mv_envelope("POINT_C", outbox_uuid="55555555-5555-4555-8555-555555555555", raw_data=None))]
        for row in rows:
            after = json.loads(row[1])["after"]
            self.assertIsNone(silver_hash(after))
        self.assertIsNone(hop_rules._content(json.loads(rows[0][1])["after"], hop_rules.METAVERSE)["payload_json"])
        self.assertIsNone(hop_rules._content(json.loads(rows[1][1])["after"], hop_rules.METAVERSE)["raw_data"])
        lines, _, _ = hop_rules.summarize_changes(rows, MV_DB, MV_NOW, 120, size=hop_rules.HOUR, source=hop_rules.METAVERSE)
        self.assertEqual([(line["event_name"], line["count"], line["conflicts"]) for line in lines], [("point_granted", 2, 0)])

    def test_non_string_json_values_stop(self):
        """문자열도 NULL 도 아닌 JSON 열 값은 형식이 깨진 것이다 — 값 없이 멈춘다(E1)."""
        for column, value in (("raw_data", 5), ("payload_json", 3.5), ("raw_data", ["x"])):
            with self.subTest(column=column, value=value):
                error, text = self.stop(mv_envelope(**{column: value}))
                self.assertIn("사유 TypeError", str(error))
                self.assertIsNone(error.__cause__)
                self.assertTrue(error.__suppress_context__)

    def test_contract_violations_are_counted_not_stopped(self):
        rows = [mv_row(0, mv_envelope("post_read", topic="raw.wrong")),
                mv_row(1, mv_envelope("chat_sent", message_key="44444444-4444-4444-8444-444444444444")),
                mv_row(2, mv_envelope("session_ended", payload_json=json.dumps(json.loads(json.loads(REAL["session_ended"])["after"]["payload_json"])
                                                                               | {"raw_manifest": {"schema_version": "9.9"}})))]
        for row in rows:
            self.assertIsNone(silver_hash(json.loads(row[1])["after"]))  # Silver 는 오류 표로 보낸다
        lines, _, _ = hop_rules.summarize_changes(rows, MV_DB, MV_NOW, 120, size=hop_rules.HOUR, source=hop_rules.METAVERSE)
        self.assertEqual(sum(line["count"] for line in lines), 3)


if __name__ == "__main__":
    unittest.main()
