"""Step 4-3c: 구간 대조(`verify_hops`) — 분류(일치·확정 불일치·중계 지연 후보·열린 창·비교 불가), 세대 사슬, 폴더 밖 고정점."""
import io
import json
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TransactionTestCase, tag

from events import hop_rules, hopcheck, ndjson, store
from events.models import EventOutbox
from events.relay import relay_batch
from records import api as records
from tests.support.factories import NOW, SUBJECT, World
from tests.support.mongo_support import MongoDatabaseMixin
from tests.support.cases import Cases

W = "2026-10-07T00:00:00Z"
KEY = (W, "ad.decision")


def line(count=1, ids="i", content="c", unsent=None, window=W, name="ad.decision"):
    base = {"window_start": window, "window_end": "x", "event_name": name, "count": count, "ids_sha256": ids, "content_sha256": content}
    if unsent is not None:
        base["unsent"] = unsent
    return base


def hop(name, *lines, open_windows=(), not_comparable=(), since=None):
    checkpoint = {"open_windows": list(open_windows), "not_comparable_windows": list(not_comparable), "since": since}
    return hopcheck.Hop(name, {"checkpoint": checkpoint}, {(l["window_start"], l["event_name"]): l for l in lines})


def statuses(rows):
    return [(r["status"], r["fields"]) for r in rows]


@tag("security")
class ClassificationTests(Cases, SimpleTestCase):
    def case_equal_summaries_match(self):
        self.assertEqual(statuses(hopcheck.compare(hop("hop_source", line()), hop("hop_outbox", line(unsent=0)))), [("match", [])])

    def case_each_differing_field_is_named(self):
        for override, field in (({"count": 2}, "count"), ({"ids": "other"}, "ids_sha256"), ({"content": "other"}, "content_sha256")):
            (row,) = hopcheck.compare(hop("hop_source", line()), hop("hop_outbox", line(unsent=0, **override)))
            self.assertEqual((row["status"], row["fields"]), ("mismatch", [field]))

    def case_a_missing_line_is_a_confirmed_mismatch_without_unsent(self):
        (row,) = hopcheck.compare(hop("hop_outbox", line(unsent=0)), hop("hop_mongo"))
        self.assertEqual((row["status"], row["fields"]), ("mismatch", ["line_missing_in_b"]))
        (row,) = hopcheck.compare(hop("hop_mongo"), hop("hop_outbox", line(unsent=0)))
        self.assertEqual((row["status"], row["fields"]), ("mismatch", ["line_missing_in_a"]))

    def case_mongodb_behind_with_unsent_rows_is_a_relay_lag_candidate_not_a_failure(self):
        (row,) = hopcheck.compare(hop("hop_outbox", line(count=5, unsent=2)), hop("hop_mongo", line(count=3, ids="x", content="y")))
        self.assertEqual(row["status"], "relay_lag_candidate")
        (row,) = hopcheck.compare(hop("hop_outbox", line(count=5, unsent=5)), hop("hop_mongo"))  # 줄이 아예 없는 창도(아직 하나도 못 보냄: 부족 5 = 미전송 5)
        self.assertEqual(row["status"], "relay_lag_candidate")

    def case_lag_needs_unsent_rows_and_a_smaller_downstream(self):
        (row,) = hopcheck.compare(hop("hop_outbox", line(count=5, unsent=0)), hop("hop_mongo", line(count=3)))
        self.assertEqual(row["status"], "mismatch")  # 미전송이 없는데 적다 = MongoDB 가 잃었다
        (row,) = hopcheck.compare(hop("hop_outbox", line(count=3, unsent=2)), hop("hop_mongo", line(count=3, content="z")))
        self.assertEqual(row["status"], "mismatch")  # 건수가 같은데 내용이 다르다 = 지연이 아니다
        (row,) = hopcheck.compare(hop("hop_outbox", line(count=3, unsent=2)), hop("hop_mongo", line(count=4)))
        self.assertEqual(row["status"], "mismatch")  # MongoDB 가 더 많다

    def case_source_to_mongodb_uses_the_unsent_counts_of_the_outbox_hop(self):
        source, outbox, mongo = hop("hop_source", line(count=5)), hop("hop_outbox", line(count=5, unsent=5)), hop("hop_mongo")
        self.assertEqual(hopcheck.compare(source, mongo, unsent_from=outbox)[0]["status"], "relay_lag_candidate")
        self.assertEqual(hopcheck.compare(source, mongo)[0]["status"], "mismatch")  # 근거(outbox)가 없으면 확정 불일치로 둔다

    def case_source_to_outbox_never_counts_as_lag(self):
        (row,) = hopcheck.compare(hop("hop_source", line(count=5)), hop("hop_outbox", line(count=3, unsent=2)))
        self.assertEqual(row["status"], "mismatch")  # 같은 스냅샷이라 지연이 아니다

    def case_open_windows_and_not_comparable_windows_are_not_compared(self):
        rows = hopcheck.compare(hop("hop_source", line()), hop("hop_outbox", open_windows=[W]))
        self.assertEqual([r["status"] for r in rows], ["open_window"])
        rows = hopcheck.compare(hop("hop_source", line()), hop("hop_outbox", not_comparable=[W]))
        self.assertEqual([r["status"] for r in rows], ["not_comparable"])
        # 줄이 없는 열린 창도 기록으로 남는다
        rows = hopcheck.compare(hop("hop_source"), hop("hop_mongo", open_windows=["2026-10-10T00:00:00Z"]))
        self.assertEqual([(r["status"], r["window_start"]) for r in rows], [("open_window", "2026-10-10T00:00:00Z")])

    def case_only_manifests_with_the_same_range_start_are_compared(self):
        """설계 01 S1 (가): 범위 시작(checkpoint.since)이 다르면 한쪽의 옛 창이 모두 '빠짐'으로 보이므로 대조하지 않고 실패한다. 같으면 그 앞 창은 보지 않는다."""
        later, kept = "2026-10-07T05:00:00Z", "2026-10-07T06:00:00Z"
        for a_since, b_since in ((None, later), (later, None), (later, "2026-10-07T06:00:00Z")):
            with self.subTest(a=a_since, b=b_since), self.assertRaises(hopcheck.HopInputError):
                hopcheck.compare(hop("hop_outbox", line(unsent=0), since=a_since), hop("hop_mongo", line(), since=b_since))
        a = hop("hop_outbox", line(unsent=0), line(unsent=0, window=kept), open_windows=["2026-10-07T01:00:00Z", "2026-10-07T07:00:00Z"], since=later)  # W(00시) 줄·01시 열린 창은 범위 앞
        b = hop("hop_mongo", line(window=kept), since=later)
        self.assertEqual([(r["window_start"], r["status"]) for r in hopcheck.compare(a, b)], [(kept, "match"), ("2026-10-07T07:00:00Z", "open_window")])

    def test_classification(self):
        self.run_cases()


@tag("security")
class LagExplanationTests(Cases, SimpleTestCase):
    """검토 P2-1: 미전송 하나가 전송 완료 이벤트의 유실을 가리면 안 된다 — 부족분이 미전송 건수로 설명될 때만 지연 후보다."""

    def classify(self, outbox_count, unsent, mongo_count):
        rows = hopcheck.compare(hop("hop_outbox", line(count=outbox_count, unsent=unsent)), hop("hop_mongo", *([line(count=mongo_count)] if mongo_count else [])))
        return rows[0]["status"]

    def case_the_shortfall_must_fit_in_the_unsent_rows(self):
        self.assertEqual(self.classify(3, 3, 0), "relay_lag_candidate")  # 셋 다 아직 못 보냈다
        self.assertEqual(self.classify(3, 2, 1), "relay_lag_candidate")  # 부족 2 = 미전송 2
        self.assertEqual(self.classify(3, 1, 2), "relay_lag_candidate")  # 부족 1 = 미전송 1
        self.assertEqual(self.classify(3, 1, 0), "mismatch")  # 검토 재현: 부족 3 > 미전송 1 — 적어도 두 건은 확정 유실
        self.assertEqual(self.classify(3, 2, 0), "mismatch")  # 부족 3 > 미전송 2
        self.assertEqual(self.classify(5, 1, 3), "mismatch")  # 부족 2 > 미전송 1
        self.assertEqual(self.classify(3, 0, 2), "mismatch")  # 미전송이 없는데 적다

    def case_source_to_mongodb_uses_the_same_rule_with_the_outbox_unsent_counts(self):
        source, outbox = hop("hop_source", line(count=3)), hop("hop_outbox", line(count=3, unsent=1))
        self.assertEqual(hopcheck.compare(source, hop("hop_mongo"), unsent_from=outbox)[0]["status"], "mismatch")
        outbox = hop("hop_outbox", line(count=3, unsent=3))
        self.assertEqual(hopcheck.compare(source, hop("hop_mongo"), unsent_from=outbox)[0]["status"], "relay_lag_candidate")

    def case_the_fields_still_say_what_differs(self):
        (row,) = hopcheck.compare(hop("hop_outbox", line(count=3, unsent=1)), hop("hop_mongo"))
        self.assertEqual((row["status"], row["fields"]), ("mismatch", ["line_missing_in_b"]))

    def test_lag_explanation(self):
        self.run_cases()


UTC = timezone.utc
SOURCE_DB = "ads_test_source"  # 파이프라인 잡에 설정한 원천 DB 이름
T5 = datetime(2026, 10, 7, 5, 10, 0, 123456, tzinfo=UTC)  # 05시 창(마이크로초까지 — Debezium created_at)
LATER = datetime(2026, 10, 8, tzinfo=UTC)  # 아래 창이 모두 닫힌 '지금'


def after(i, at=T5, name="ad.decision", payload=None, aggregate="agg-1"):
    """outbox 봉투 6열(Debezium after — payload 는 DB 의 JSON 원문 문자열, created_at 은 마이크로초 UTC 문자열)."""
    return {"event_id": str(uuid.UUID(int=i)), "event_name": name, "schema_version": 1, "aggregate_id": aggregate,
            "payload": json.dumps({"n": i}) if payload is None else payload, "created_at": at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")}


def change(af, op="c", db=SOURCE_DB, tombstone=False, keyless=False):
    """Bronze 행 하나의 (key, value): 키는 스키마를 담은 JsonConverter 모양, value 는 Debezium 원본 봉투(DELETE·TRUNCATE 는 after 없음, tombstone 은 value 없음)."""
    key = None if keyless else json.dumps({"schema": {"type": "struct"}, "payload": {"event_id": af["event_id"]}})
    if tombstone:
        return key, None
    return key, json.dumps({"before": None, "after": None if op in ("d", "t") else af, "op": op, "source": {"db": db, "schema": "public", "table": "events_outbox"}})


def outbox_hop(afters, unsent=0):
    docs = [hop_rules.normal_document(a["event_id"], a["event_name"], a["schema_version"], datetime.fromisoformat(a["created_at"]), json.loads(a["payload"])) for a in afters]
    lines, open_windows = hop_rules.summarize(docs, LATER, 120, size=hop_rules.HOUR)
    checkpoint = {"window": "utc-hour", "open_windows": [hop_rules.format_time(w) for w in open_windows]}
    return hopcheck.Hop("hop_outbox", {"source_kind": "hop_outbox", "checkpoint": checkpoint}, {(l["window_start"], l["event_name"]): l | {"unsent": unsent} for l in lines})


def fold(changes):
    """Bronze 행 목록(offset 0, 1, …)을 공용 규칙으로 요약한다. 반환: (줄 사전, 열린 창, checkpoint 필드)."""
    lines, open_windows, extra = hop_rules.summarize_changes([(k, v, 0, offset) for offset, (k, v) in enumerate(changes)], SOURCE_DB, LATER, 120, size=hop_rules.HOUR)
    return {(l["window_start"][11:13], l["event_name"]): l for l in lines}, [hop_rules.format_time(w) for w in open_windows], extra


def pipeline_hop(changes, **checkpoint):
    lines, open_windows, extra = hop_rules.summarize_changes([(k, v, 0, offset) for offset, (k, v) in enumerate(changes)], SOURCE_DB, LATER, 120, size=hop_rules.HOUR)
    manifest = {"source_kind": "hop_pipeline", "checkpoint": {"window": "utc-hour", "open_windows": [hop_rules.format_time(w) for w in open_windows],
                                                              "rule_sha256": hop_rules.rule_sha256(), **extra, **checkpoint}}
    return hopcheck.Hop("pipeline", manifest, {(l["window_start"], l["event_name"]): l for l in lines})


def pair(outbox, pipeline, **kw):
    return [(r["window_start"][11:13], r["event_name"], r["status"], r["fields"]) for r in hopcheck.compare(outbox, pipeline, **kw)]


@tag("security")
class PipelinePairTests(Cases, SimpleTestCase):
    """DRD §5.6 [P1-3·P2-1]: Bronze 행 → hop_pipeline 줄(공용 규칙 `summarize_changes`)과 outbox → 파이프라인 대조. C4 탐지(누락·내용 변경·중복)와 DRD 0.67 의 경우들."""

    def case_the_same_events_match_with_zero_counts(self):
        afters = [after(1), after(2), after(3, name="ad.charge")]
        lines, open_windows, extra = fold([change(a) for a in afters])
        self.assertEqual({k: (l["count"], l["duplicates"], l["conflicts"], l["not_insert"]) for k, l in lines.items()},
                         {("05", "ad.decision"): (2, 0, 0, 0), ("05", "ad.charge"): (1, 0, 0, 0)})
        self.assertEqual((open_windows, extra["not_insert_unplaced"], extra["foreign_source_rows"]), ([], 0, 0))
        self.assertEqual(pair(outbox_hop(afters), pipeline_hop([change(a) for a in afters])), [("05", "ad.charge", "match", []), ("05", "ad.decision", "match", [])])

    def case_c4_detection_loss_content_change_and_duplicates(self):
        afters = [after(1), after(2)]
        lost = pair(outbox_hop(afters), pipeline_hop([change(after(1))]))
        self.assertEqual(lost, [("05", "ad.decision", "mismatch", ["count", "ids_sha256", "content_sha256"])])
        changed = pair(outbox_hop(afters), pipeline_hop([change(after(1)), change(after(2, payload='{"n": 99}'))]))
        self.assertEqual(changed, [("05", "ad.decision", "mismatch", ["content_sha256"])])
        replay = [change(after(1)), change(after(2)), change(after(2), op="r")]  # 재발행 op=r 은 c 와 같은 문서 — 중복은 match 를 막지 않는다
        lines, _, extra = fold(replay)
        self.assertEqual(lines[("05", "ad.decision")]["duplicates"], 1)
        self.assertEqual(extra["duplicate_examples"], [{"event_id": after(2)["event_id"], "partition": 0, "offset": 2}])
        self.assertEqual(pair(outbox_hop(afters), pipeline_hop(replay)), [("05", "ad.decision", "match", [])])

    def case_payload_notation_is_not_a_conflict(self):
        """payload 는 해석한 값으로 비교한다 — 공백·키 순서·1 과 1.0 은 같은 내용이다(DRD §5.6 (5))."""
        lines, _, _ = fold([change(after(2, payload='{"n": 2, "m": "x"}')), change(after(2, payload='{ "m":"x","n":2.0 }'), op="r")])
        self.assertEqual((lines[("05", "ad.decision")]["duplicates"], lines[("05", "ad.decision")]["conflicts"]), (1, 0))

    def case_conflicting_copies_count_on_every_line_they_land_on(self):
        """충돌 열에 created_at 이 있어 사본이 다른 창에 놓인다 — 그 event_id 는 건수·해시에서 빠지고 사본이 놓인 두 줄 모두 conflicts 1, count 0 줄도 낸다."""
        afters = [after(1), after(2)]
        copies = [change(after(1)), change(after(2)), change(after(2, at=T5 + timedelta(hours=1)), op="r")]
        lines, _, extra = fold(copies)
        self.assertEqual({k: (l["count"], l["conflicts"], l["duplicates"]) for k, l in lines.items()}, {("05", "ad.decision"): (1, 1, 0), ("06", "ad.decision"): (0, 1, 0)})
        self.assertEqual(lines[("06", "ad.decision")]["ids_sha256"], hop_rules.EMPTY_SHA256)
        self.assertEqual([(e["event_id"], e["offset"]) for e in extra["conflict_examples"]], [(after(2)["event_id"], 1), (after(2)["event_id"], 2)])
        self.assertEqual(pair(outbox_hop(afters), pipeline_hop(copies)),
                         [("05", "ad.decision", "mismatch", ["count", "ids_sha256", "content_sha256", "conflicts"]), ("06", "ad.decision", "mismatch", ["line_missing_in_a", "conflicts"])])

    def case_three_copies_with_one_different_are_a_conflict_not_a_duplicate(self):
        same, other = change(after(2), op="r"), change(after(2, aggregate="agg-2"), op="r")  # 둘은 같고 하나만 aggregate_id 가 다름
        for copies in ([change(after(2)), same, other], [change(after(2)), other, same]):  # 다른 사본이 끝에 와도, 가운데 와도(뒤의 같은 사본이 충돌을 지우지 않는다)
            with self.subTest(order=[json.loads(v)["after"]["aggregate_id"] for _, v in copies]):
                lines, _, _ = fold(copies)
                self.assertEqual({k: (l["count"], l["duplicates"], l["conflicts"]) for k, l in lines.items()}, {("05", "ad.decision"): (0, 0, 1)})

    def case_not_insert_counts_on_the_line_of_its_after(self):
        """op 가 c·r 가 아닌 행(u)은 자기 after 의 줄에 not_insert — 같은 event_id 의 c 행은 그대로 센다. 다른 줄이면 count 0 줄, 열린 창이면 줄에 넣지 않는다."""
        afters = [after(1), after(2)]
        same_line = [change(after(1)), change(after(2)), change(after(2), op="u")]
        lines, _, extra = fold(same_line)
        self.assertEqual((lines[("05", "ad.decision")]["count"], lines[("05", "ad.decision")]["not_insert"]), (2, 1))
        self.assertEqual(extra["not_insert_examples"], [{"event_id": after(2)["event_id"], "partition": 0, "offset": 2}])
        self.assertEqual(pair(outbox_hop(afters), pipeline_hop(same_line)), [("05", "ad.decision", "mismatch", ["not_insert"])])  # 건수·해시가 같아도
        other_line = same_line[:2] + [change(after(9, at=T5 + timedelta(hours=2)), op="u"), change(after(10, at=LATER - timedelta(seconds=30)), op="u")]
        lines, open_windows, _ = fold(other_line)
        self.assertEqual((lines[("07", "ad.decision")]["count"], lines[("07", "ad.decision")]["not_insert"]), (0, 1))
        self.assertEqual(open_windows, ["2026-10-07T23:00:00Z"])  # 아직 열린 창의 행은 줄에 넣지 않는다
        self.assertEqual(pair(outbox_hop(afters), pipeline_hop(other_line))[1:2], [("07", "ad.decision", "mismatch", ["line_missing_in_a", "not_insert"])])

    def case_since_drops_older_windows_of_every_count(self):
        """since 앞 창은 줄에 넣지 않는다 — 문서뿐 아니라 duplicates·conflicts·not_insert 만 남는 줄도(DRD §5.6 (6), 설계 01 S1)."""
        six = T5 + timedelta(hours=1)
        rows = [change(after(1)), change(after(1), op="r"), change(after(2)), change(after(2, aggregate="x"), op="r"), change(after(3), op="u"),  # 05시: 중복·충돌·not_insert
                change(after(4, at=six)), change(after(4, at=six), op="r"), change(after(5, at=six), op="u")]  # 06시: 문서 하나·중복·not_insert
        lines, open_windows, _ = hop_rules.summarize_changes([(k, v, 0, offset) for offset, (k, v) in enumerate(rows)], SOURCE_DB, LATER, 120,
                                                             since=datetime(2026, 10, 7, 6, tzinfo=UTC), size=hop_rules.HOUR)
        self.assertEqual([(l["window_start"], l["count"], l["duplicates"], l["conflicts"], l["not_insert"]) for l in lines], [("2026-10-07T06:00:00Z", 1, 1, 0, 1)])
        self.assertEqual(open_windows, [])

    def case_a_created_at_without_time_zone_stops_the_summary(self):
        """시간대 없는 created_at 은 지역 시각으로 읽혀 창이 조용히 어긋나므로, 형식이 깨진 after 로 보고 멈춘다(Debezium 은 늘 Z 를 붙인다)."""
        with self.assertRaisesRegex(ValueError, "시간대가 없다"):
            fold([change(after(1) | {"created_at": "2026-10-07T05:10:00.123456"})])

    def case_rows_that_cannot_be_placed_or_come_from_elsewhere_fail_the_pair(self):
        """DELETE(op d)·tombstone(value null)·TRUNCATE(op t, 키 없음)는 어느 줄에도 놓을 수 없고, 다른 DB 의 행은 다른 출처다 — checkpoint 에 세고 그 쌍은 대조하지 않는다."""
        base = [change(after(1))]
        for bad, field, event_id in ((change(after(2), op="d"), "not_insert_unplaced", after(2)["event_id"]), (change(after(2), tombstone=True), "not_insert_unplaced", after(2)["event_id"]),
                                     (change(after(2), op="t", keyless=True), "not_insert_unplaced", None), (change(after(2), db="other_db"), "foreign_source_rows", None)):
            with self.subTest(field=field, event_id=event_id):
                lines, _, extra = fold(base + [bad])
                self.assertEqual((extra["not_insert_unplaced"] + extra["foreign_source_rows"], extra[field]), (1, 1))
                self.assertEqual(lines[("05", "ad.decision")]["count"], 1)  # 정상 행은 그대로 센다
                if field == "not_insert_unplaced":
                    self.assertEqual(extra["not_insert_unplaced_examples"], [{"event_id": event_id, "partition": 0, "offset": 1}])
                with self.assertRaisesRegex(hopcheck.HopInputError, f"pipeline 은 대조하지 않는다: {field} 1건"):
                    hopcheck.compare(outbox_hop([after(1)]), pipeline_hop(base + [bad]), use_unsent=False)

    def case_a_different_rule_version_is_not_compared(self):
        """설계 01 S5: 파이프라인 사본의 규칙 파일이 광고 서버의 hop_rules.py 와 다르면 대조하지 않는다."""
        with self.assertRaisesRegex(hopcheck.HopInputError, "rule_sha256 0{64} 이 이 광고 서버의 공용 요약 규칙"):
            hopcheck.compare(outbox_hop([after(1)]), pipeline_hop([change(after(1))], rule_sha256="0" * 64), use_unsent=False)

    def case_unsent_rows_do_not_excuse_a_pipeline_shortfall(self):
        """설계 01 D11: CDC 는 sent_at 을 채우지 않으므로 outbox → 파이프라인은 미전송을 지연 근거로 쓰지 않는다(인자로 끈다)."""
        afters = [after(1), after(2)]
        self.assertEqual(pair(outbox_hop(afters, unsent=1), pipeline_hop([change(after(1))]))[0][2], "relay_lag_candidate")  # 끄지 않으면(기본) 지연으로 보인다
        self.assertEqual(pair(outbox_hop(afters, unsent=1), pipeline_hop([change(after(1))]), use_unsent=False)[0][2], "mismatch")
        short_with_update = pipeline_hop([change(after(1)), change(after(2), op="u")])  # 부족 1 + not_insert 1 — 기본값으로 불러도 원인이 지연 후보로 가려지지 않는다
        self.assertEqual(pair(outbox_hop(afters, unsent=1), short_with_update), [("05", "ad.decision", "mismatch", ["count", "ids_sha256", "content_sha256", "not_insert"])])

    def test_pipeline_pair(self):
        self.run_cases()


A64, B64 = "a" * 64, "b" * 64  # 형식이 맞는 해시


def hour_line(start="2026-10-07T05:00:00Z", end="2026-10-07T06:00:00Z", **kw):
    return line(window=start, ids=A64, content=B64, **kw) | {"window_end": end}


class InputValidationTests(Cases, SimpleTestCase):
    """검토 P2-3: 파일 체크섬이 맞는 것과 집계 범위가 같은 것은 별개다 — 대조 전에 창 시작·끝·크기와 계약 버전·구간 종류를 확인한다."""

    def publish(self, lines, kind="hop_mongo", window="utc-day", schema_version=1, hop_field="same", **extra):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, True)
        checkpoint = {"window": window, "open_windows": []}
        if hop_field == "same":
            checkpoint["hop"] = kind
        elif hop_field:
            checkpoint["hop"] = hop_field
        if "unresolved" in extra:
            checkpoint["unresolved_charges"] = extra["unresolved"]
        if "since" in extra:
            checkpoint["since"] = extra["since"]
        if "hidden" in extra:
            checkpoint |= {"not_comparable_windows": extra["not_comparable"], "not_comparable_unsent": extra["hidden"]}
        ndjson.write(directory, "h", lines, kind, schema_version, checkpoint=checkpoint)
        return ndjson.manifest_path(directory, "h")

    def day(self, **kw):
        return line(window="2026-10-07T00:00:00Z", **({"ids": A64, "content": B64} | kw)) | {"window_end": "2026-10-08T00:00:00Z"}

    def load(self, path, name="hop_mongo"):
        return hopcheck.load_hop(path, name, expected_kind=name if name != "pipeline" else None)

    def case_a_well_formed_input_loads(self):
        hop_loaded = self.load(self.publish([self.day()]))
        self.assertEqual((hop_loaded.window, len(hop_loaded.lines)), ("utc-day", 1))

    def case_the_window_end_must_match_the_declared_window_size(self):
        """검토 재현: 시작 시각·요약값은 그대로 두고 끝 시각을 한 시간 뒤로 바꾸고 checkpoint.window 를 utc-hour 로 — 일별과 시간별이 match 로 성공했다."""
        forged = line(window="2026-10-07T00:00:00Z", ids=A64, content=B64) | {"window_end": "2026-10-07T01:00:00Z"}
        with self.assertRaises(hopcheck.HopInputError) as raised:
            self.load(self.publish([forged], window="utc-day"))
        self.assertIn("창", str(raised.exception))
        self.load(self.publish([forged], window="utc-hour"))  # 선언한 크기와 맞으면 그 자체로는 올바른 시간별 manifest 다

    def case_daily_and_hourly_manifests_are_never_compared(self):
        daily = self.load(self.publish([self.day()]))
        hourly = self.load(self.publish([line(window="2026-10-07T00:00:00Z", ids=A64, content=B64) | {"window_end": "2026-10-07T01:00:00Z"}], window="utc-hour"), "pipeline")
        with self.assertRaises(hopcheck.HopInputError):
            hopcheck.compare(daily, hourly)

    def case_windows_must_be_aligned_to_the_utc_boundary(self):
        misaligned = line(window="2026-10-07T03:00:00Z", ids=A64, content=B64) | {"window_end": "2026-10-08T03:00:00Z"}
        with self.assertRaises(hopcheck.HopInputError):
            self.load(self.publish([misaligned]))
        half_hour = hour_line("2026-10-07T05:30:00Z", "2026-10-07T06:30:00Z")
        with self.assertRaises(hopcheck.HopInputError):
            self.load(self.publish([half_hour], window="utc-hour"))

    def case_an_unknown_window_kind_contract_version_or_hop_kind_is_refused(self):
        for kwargs in ({"window": "utc-week"}, {"window": None}, {"schema_version": 2}, {"kind": "hop_other"}, {"kind": "ledger_check"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(hopcheck.HopInputError):
                    self.load(self.publish([self.day()], **kwargs))

    def case_the_unresolved_charge_count_must_be_a_non_negative_integer(self):
        """원본 구간의 `unresolved_charges`(창에 놓을 수 없는 과금 원장 줄)는 줄 대조에 나오지 않고 이 값으로만 실패한다 — 형식이 틀린 값으로 0 처럼 지나가지 않는다."""
        self.assertEqual(self.load(self.publish([self.day()], kind="hop_source", unresolved=2), "hop_source").unresolved_charges, 2)
        for bad in ("1", -1, True, 1.5, None):
            with self.subTest(bad=bad), self.assertRaises(hopcheck.HopInputError):
                self.load(self.publish([self.day()], kind="hop_source", unresolved=bad), "hop_source")

    def case_the_unsent_counts_of_not_comparable_windows_must_be_well_formed(self):
        """outbox 구간의 `not_comparable_unsent` 는 원본 → MongoDB 부족을 지연으로 설명하는 근거다(버그 탐색 B5) — 형식이 틀리거나, 비교하는 창의 항목이거나, 중복이면 받지 않는다."""
        good = {"window_start": W, "event_name": "ad.decision", "unsent": 2}
        loaded = self.load(self.publish([], kind="hop_outbox", not_comparable=[W], hidden=[good]), "hop_outbox")
        self.assertEqual(loaded.not_comparable_unsent, {KEY: 2})
        broken = [5, ["x"], [{"event_name": "ad.decision", "unsent": 1}], [good, good], [dict(good, window_start="2026-10-08T00:00:00Z")]]
        broken += [[dict(good, unsent=bad)] for bad in ("1", -1, True, 1.5, None)]
        for hidden in broken:
            with self.subTest(hidden=hidden), self.assertRaises(hopcheck.HopInputError):
                self.load(self.publish([], kind="hop_outbox", not_comparable=[W], hidden=hidden), "hop_outbox")

    def case_the_range_start_must_be_a_utc_hour_or_null(self):
        """DRD §5.6: checkpoint.since 는 UTC 정시(YYYY-MM-DDTHH:00:00Z) 또는 null — 날짜만 쓴 옛 형식·분이 있는 시각·다른 표기·문자열이 아닌 값은 입력 실패다."""
        for since in (None, "2026-10-07T05:00:00Z"):
            self.assertEqual(self.load(self.publish([self.day()], since=since)).since, since)
        for since in ("2026-10-07", "2026-10-07T05:30:00Z", "2026-10-07T05:00:00+00:00", "2026-10-7T5:00:00Z", 5, ["2026-10-07T05:00:00Z"]):  # 0 을 채우지 않은 표기도
            with self.subTest(since=since), self.assertRaises(hopcheck.HopInputError):
                self.load(self.publish([self.day()], since=since))

    def case_a_pipeline_manifest_must_carry_its_counts_examples_and_rule_version(self):
        """DRD §5.6 "입력 검증" [P1-3]: hop_pipeline 의 줄마다 duplicates·conflicts·not_insert, checkpoint 의 두 건수·예시 넷·rule_sha256 은 필수다."""
        lines, open_windows, extra = hop_rules.summarize_changes([(*change(after(1)), 0, 0), (*change(after(1), op="r"), 0, 1)], SOURCE_DB, LATER, 120, size=hop_rules.HOUR)

        def publish(lines=lines, **checkpoint):
            directory = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, directory, True)
            full = {"window": "utc-hour", "open_windows": [], "rule_sha256": hop_rules.rule_sha256(), **extra, **checkpoint}
            ndjson.write(directory, "h", lines, "hop_pipeline", 1, checkpoint={k: v for k, v in full.items() if v is not ...})
            return ndjson.manifest_path(directory, "h")
        loaded = self.load(publish(), "pipeline")
        self.assertEqual(next(iter(loaded.lines.values()))["duplicates"], 1)  # 공용 규칙의 출력이 계약 검사를 통과한다
        bad_lines = [[{k: v for k, v in lines[0].items() if k != field}] for field in hop_rules.PIPELINE_COUNTS] + [[lines[0] | {"conflicts": True}], [lines[0] | {"not_insert": -1}]]
        bad_checkpoints = [{"not_insert_unplaced": ...}, {"foreign_source_rows": "0"}, {"rule_sha256": ...}, {"rule_sha256": "A" * 64}, {"duplicate_examples": ...},
                           {"conflict_examples": [{"event_id": 1, "partition": 0, "offset": 0}]}, {"not_insert_examples": [{"event_id": None, "partition": -1, "offset": 0}]},
                           {"not_insert_unplaced_examples": [{"event_id": None, "partition": 0, "offset": 0}] * 11},
                           {"duplicate_examples": [{"partition": 0, "offset": 0}]}, {"conflict_examples": [{"event_id": None, "partition": 0, "offset": 0, "topic": "t"}]}]  # 키는 정확히 셋
        for kw in [{"lines": l} for l in bad_lines] + bad_checkpoints:
            with self.subTest(kw=kw), self.assertRaises(hopcheck.HopInputError):
                self.load(publish(**kw), "pipeline")

    def case_malformed_checkpoints_are_refused_not_raised(self):
        """리팩터 감사 D3: checkpoint 가 객체가 아니거나 창 크기가 문자열이 아니거나 창 목록·과금 예시가 문자열 목록이 아니면 날것의 예외(대조 전체가 멈춘다)가 아니라 `HopInputError` 다."""
        for checkpoint in ([1], {"window": "utc-day", "open_windows": 5}, {"window": "utc-day", "open_windows": [[1]]}, {"window": "utc-day", "not_comparable_windows": "W"},
                           {"window": []}, {"window": "utc-day", "unresolved_charges": 1, "unresolved_charge_examples": [1]}):
            directory = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, directory, True)
            ndjson.write(directory, "h", [], "hop_mongo", 1, checkpoint=checkpoint)
            with self.subTest(checkpoint=checkpoint), self.assertRaises(hopcheck.HopInputError):
                self.load(ndjson.manifest_path(directory, "h"))

    def case_the_file_must_be_the_hop_it_is_loaded_as(self):
        outbox_file = self.publish([self.day(unsent=0)], kind="hop_outbox")
        with self.assertRaises(hopcheck.HopInputError):
            self.load(outbox_file, "hop_mongo")  # 구간 종류가 다른 파일을 바꿔 끼울 수 없다
        with self.assertRaises(hopcheck.HopInputError):
            self.load(self.publish([self.day()], hop_field="hop_source"), "hop_mongo")  # checkpoint 의 구간이 다르다

    def case_a_duplicate_summary_key_is_refused_not_overwritten(self):
        with self.assertRaises(hopcheck.HopInputError) as raised:
            self.load(self.publish([self.day(), self.day(content="c" * 64)]))
        self.assertIn("중복", str(raised.exception))

    def case_malformed_lines_are_refused(self):
        for broken in ({"count": -1}, {"count": True}, {"count": "1"}, {"ids_sha256": "xyz"}, {"content_sha256": "A" * 64}, {"window_start": "어제"}):
            with self.subTest(broken=broken):
                with self.assertRaises(hopcheck.HopInputError):
                    self.load(self.publish([self.day() | broken]))
        missing = self.day()
        del missing["ids_sha256"]
        with self.assertRaises(hopcheck.HopInputError):
            self.load(self.publish([missing]))

    def test_input_validation(self):
        self.run_cases()


@tag("security")
class VerifyHopsCommandTests(Cases, MongoDatabaseMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        store.ensure()
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        w = World()
        decision = records.decide(w.slot.media_id, w.slot.slot_id, subject_id=SUBJECT, now=NOW, rng=Random(1))
        view, _ = records.receive_reaction(w.slot.media_id, decision.decision_id, uuid.uuid4(), "IMPRESSION", NOW, {"type": "IMPRESSION"}, now=NOW)
        records.process_reaction(view.inbox_id, now=NOW + timedelta(seconds=7))

    def export(self):
        # 시험 데이터의 날짜는 고정(2026-10-07)이다 — 실제 지금으로 내보내면 보존 기준(지금 − 7일)이 그 창을 넘는 날부터 대조 제외가 된다(6-14d)
        call_command("export_hop_manifests", "--out", str(self.dir / "hops"), "--as-of", (NOW + timedelta(days=3)).isoformat(), stdout=io.StringIO())

    def verify(self, *flags):
        out = io.StringIO()
        try:
            call_command("verify_hops", "--dir", str(self.dir / "hops"), *flags, stdout=out)
            return True, out.getvalue()
        except CommandError as error:
            return False, out.getvalue() + str(error)

    def lines(self, hop_name):
        manifest = ndjson.read_manifest(ndjson.manifest_path(self.dir / "hops", hop_name))
        return [json.loads(l) for l in (self.dir / "hops" / manifest["file"]).read_text().splitlines()]

    def pipeline(self, lines, **checkpoint):
        """계약 모양의 파이프라인 manifest(DRD §5.6 [P1-3] — 줄의 세 건수·checkpoint 필수 필드·이 광고 서버의 rule_sha256)를 쓰고 경로를 돌려준다."""
        full = {"open_windows": [], "window": "utc-day", "rule_sha256": hop_rules.rule_sha256(), "not_insert_unplaced": 0, "foreign_source_rows": 0,
                **{field: [] for field in hop_rules.EXAMPLE_FIELDS}, **checkpoint}
        ndjson.write(self.dir / "pipeline", "pipeline", [{"duplicates": 0, "conflicts": 0, "not_insert": 0} | l for l in lines], "hop_pipeline", 1, checkpoint=full)
        return str(ndjson.manifest_path(self.dir / "pipeline", "pipeline"))

    def case_everything_matches_after_the_relay(self):
        relay_batch()
        self.export()
        ok, text = self.verify("--out", str(self.dir / "report"))
        self.assertTrue(ok, text)
        self.assertIn("'match': 24", text)  # 세 쌍 × 8줄(결정·접수·판정·과금·정책·입찰·캠페인 변경·광고그룹 변경)
        self.assertEqual([l["unsent"] for l in self.lines("hop_outbox")], [0] * 8)  # 중계 뒤에는 미전송이 없다(일치 판정은 unsent 를 보지 않는다)
        manifest = ndjson.read_manifest(ndjson.manifest_path(self.dir / "report", "hop_report"))
        self.assertEqual((ndjson.verify(ndjson.manifest_path(self.dir / "report", "hop_report")), manifest["checkpoint"]["counts"], manifest["checkpoint"]["errors"]), ([], {"match": 24}, []))

    def case_before_the_relay_it_is_a_lag_candidate_and_the_command_still_succeeds(self):
        self.export()
        ok, text = self.verify()
        self.assertTrue(ok, text)
        self.assertIn("relay_lag_candidate", text)
        self.assertIn("LAG", text)
        relay_batch()
        self.export()  # 다시 확인 = 다시 낸 manifest
        ok, text = self.verify()
        self.assertTrue(ok, text)
        self.assertNotIn("LAG", text)

    def case_a_tampered_outbox_event_is_a_confirmed_mismatch(self):
        relay_batch()
        row = EventOutbox.objects.get(event_name="ad.charge")
        payload = row.payload
        payload["charged_amount"] = 999
        EventOutbox.objects.filter(pk=row.pk).update(payload=payload)
        self.export()
        source, outbox, mongo = ({l["event_name"]: l for l in self.lines(h)}["ad.charge"] for h in ("hop_source", "hop_outbox", "hop_mongo"))
        self.assertEqual(source["ids_sha256"], outbox["ids_sha256"])  # manifest 만으로도 보인다: 존재는 같고
        self.assertNotEqual(source["content_sha256"], outbox["content_sha256"])  # 내용만 다르다
        self.assertEqual(source["content_sha256"], mongo["content_sha256"])
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("content_sha256", text)
        self.assertIn("hop_source->hop_outbox", text)
        self.assertIn("hop_outbox->hop_mongo", text)

    def case_an_event_lost_by_mongodb_after_it_was_sent_is_a_confirmed_mismatch(self):
        relay_batch()
        store.collection().delete_one({"event_name": "ad.reaction"})
        self.export()
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("FAIL hop_outbox->hop_mongo", text)

    def case_a_broken_generation_chain_is_reported(self):
        self.export()
        self.export()
        self.export()
        path = ndjson.manifest_path(self.dir / "hops", "hop_source")
        previous = ndjson.read_manifest(path)["previous_sha256"]
        (self.dir / "hops" / f"hop_source.manifest.{previous[:16]}.json").unlink()
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("세대 사슬", text)

    def case_rewriting_a_manifest_outside_the_export_is_caught_by_the_audit_anchor(self):
        """폴더에 쓸 수 있는 사람이 그럴듯한 새 manifest 를 직접 게시해도, 감사 로그의 고정점과 달라 드러난다."""
        relay_batch()
        self.export()
        ndjson.write(self.dir / "hops", "hop_source", [], "hop_source", 1, checkpoint={"hop": "hop_source", "window": "utc-day", "open_windows": []}, chain=True)  # (모양은 올바르지만 감사 로그에 남지 않는 게시)
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("고정점", text)
        self.assertNotIn("고정점", self.verify("--no-anchor")[1])  # 고정점 확인을 끄면 이 경고는 없다(다른 환경의 폴더용)

    def case_without_any_anchor_the_check_asks_for_no_anchor(self):
        relay_batch()
        self.export()
        from foundation.models import AuditLog

        AuditLog.objects.filter(action="events.hop_manifests_exported")._raw_delete(AuditLog.objects.db)  # (시험에서만: 추가 전용 규칙을 우회해 고정점이 없는 상태를 만든다)
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("--no-anchor", text)
        self.assertTrue(self.verify("--no-anchor")[0])

    def case_a_modified_data_file_fails_the_input_check(self):
        relay_batch()
        self.export()
        manifest = ndjson.read_manifest(ndjson.manifest_path(self.dir / "hops", "hop_mongo"))
        data = self.dir / "hops" / manifest["file"]
        data.write_bytes(data.read_bytes().replace(b'"count":1', b'"count":7'))
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("hop_mongo", text)
        self.assertIn("검증 실패", text)

    def case_a_pipeline_manifest_is_compared_with_the_outbox_and_mongodb_hops(self):
        """DRD §5.6 [P1-3]: 파이프라인 manifest 는 outbox 구간과 대조하고, 로컬 병행 기간에는 MongoDB 구간과도 대조한다."""
        relay_batch()
        self.export()
        mongo_lines = self.lines("hop_mongo")
        ok, text = self.verify("--pipeline", self.pipeline(mongo_lines))
        self.assertTrue(ok, text)
        self.assertIn("'match': 40", text)  # 다섯 쌍 × 8줄
        bad = [dict(l, content_sha256="0" * 64) if l["event_name"] == "ad.charge" else l for l in mongo_lines]
        ok, text = self.verify("--pipeline", self.pipeline(bad))
        self.assertFalse(ok)
        for pair in ("hop_outbox->pipeline", "hop_mongo->pipeline"):
            self.assertIn(f"FAIL {pair} ", text)
        self.assertIn("content_sha256", text)

    def case_a_pipeline_manifest_with_another_range_start_is_not_compared(self):
        """설계 01 S1 (가): 파이프라인 manifest 의 범위 시작이 광고 서버 manifest 와 다르면 그 쌍은 대조하지 않고 실패한다(옛 창이 '빠짐'으로 쏟아지지 않게)."""
        relay_batch()
        self.export()
        ok, text = self.verify("--pipeline", self.pipeline(self.lines("hop_mongo"), since="2026-10-07T00:00:00Z"))
        self.assertFalse(ok)
        self.assertIn("FAIL pipeline: 범위(checkpoint.since)가 다른 manifest 는 대조하지 않는다: hop_outbox=None, pipeline=2026-10-07T00:00:00Z", text)
        self.assertEqual(text.count("FAIL pipeline:"), 1)  # 두 쌍에 같은 실패라 한 번만
        self.assertNotIn("->pipeline", text)  # 줄 대조를 하지 않았다

    def case_rows_the_pipeline_could_not_place_fail_the_pair(self):
        """DRD §5.6 [P1-3]: 놓을 수 없는 행(DELETE·tombstone·TRUNCATE)이나 다른 출처 행이 있으면 커넥터 설정 이상이라 그 쌍은 대조하지 않고 실패한다."""
        relay_batch()
        self.export()
        for field in ("not_insert_unplaced", "foreign_source_rows"):
            ok, text = self.verify("--pipeline", self.pipeline(self.lines("hop_mongo"), **{field: 1}))
            self.assertFalse(ok, field)
            self.assertIn(f"FAIL pipeline: pipeline 은 대조하지 않는다: {field} 1건(커넥터 설정 이상", text)
            self.assertNotIn("->pipeline", text)

    def case_a_pipeline_shortfall_is_not_excused_by_unsent_outbox_rows(self):
        """버그 탐색 B4: outbox 미전송은 MongoDB 에도 아직 없으므로 파이프라인이 MongoDB 보다 적은 이유가 될 수 없다 — 확정 불일치."""
        relay_batch()
        row = EventOutbox.objects.filter(event_name="ad.decision").first()
        EventOutbox.objects.filter(pk=row.pk).update(sent_at=None)  # MongoDB 에는 이미 있지만 outbox 로는 미전송 1
        self.export()
        short = [dict(l, count=l["count"] - 1, ids_sha256="0" * 64) if l["event_name"] == "ad.decision" else l for l in self.lines("hop_mongo")]
        ok, text = self.verify("--pipeline", self.pipeline(short))
        self.assertFalse(ok)
        self.assertIn("FAIL hop_mongo->pipeline", text)
        self.assertIn("FAIL hop_outbox->pipeline", text)  # outbox → 파이프라인도 미전송을 지연 근거로 쓰지 않는다(CDC 는 sent_at 을 채우지 않는다, D11)

    def case_a_missing_hop_manifest_fails(self):
        self.export()
        ndjson.manifest_path(self.dir / "hops", "hop_outbox").unlink()
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("hop_outbox", text)

    def test_verify_hops_command(self):
        self.run_cases()
