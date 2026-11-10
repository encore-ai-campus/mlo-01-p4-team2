"""메타버스 창 대사 도구(`pipeline/reconcile/metaverse_hops.py`, MVR-2 — 계획 temp/review/MVR-2-plan.md 3판 §6 대사 시험) — 표준 라이브러리만(DB·S3 는 가짜).

- DB 행 = Bronze 행: 리허설 실제 행 16(`pipeline/tests/metaverse/bronze_values.json`, 읽기만)을 DB 모양(+09:00 시각·다른 jsonb 텍스트·UUID 객체)으로 바꿔도 같은 줄이다.
- 대조·판정·한 번의 실행(종료 코드·상태 파일·받기 순서)·질의 모양·비밀·층 규칙.
도움 함수(`mv_row`·`silver_hash`·`hop_hash`)는 MVR-1 시험의 것을 그대로 쓴다(모듈만 가져온다 — 그 시험 클래스를 이 모듈에 들이지 않음).
"""
import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.common import hop_rules, ndjson
from pipeline.reconcile import metaverse_hops as mh
from pipeline.tests.common import test_hop_rules_sources as mv
from pipeline.tests.test_layers import imports, violations

ROOT = Path(__file__).resolve().parents[3]
for folder in ("pipeline/common", "pipeline/bronze"):  # Bronze manifest 잡이 실제로 내는 manifest 로 대조한다
    if str(ROOT / folder) not in sys.path:
        sys.path.insert(0, str(ROOT / folder))
import hop_manifest_job as job  # noqa: E402

UTC, KST = timezone.utc, timezone(timedelta(hours=9))
DB = "metaverse_lab"
NOW = datetime(2026, 11, 4, 8, tzinfo=UTC)  # 11-04 07시 창까지 닫힘(07:00 + 1시간 + 120초 ≤ 08:00)
JOB_CONFIG = {"table": "s3a://lake/bronze/metaverse/outbox_events", "uri": "s3a://lake/manifests/hop_pipeline_metaverse/", "db": DB, "grace": 120,
              "source": job.SOURCES["METAVERSE"]}
ENVIRON = {"HOP_S3_ENDPOINT": "http://127.0.0.1:1", "HOP_S3_REGION": "ap-northeast-2", "HOP_S3_BUCKET": "lake", "HOP_S3_PREFIX": "manifests/hop_pipeline_metaverse/",
           "HOP_S3_ACCESS_KEY_FILE": "/없음/a", "HOP_S3_SECRET_KEY_FILE": "/없음/s", "HOP_GRACE_SECONDS": "120",
           "PGHOST": "127.0.0.1", "PGPORT": "5010", "PGDATABASE": DB, "PGUSER": "metaverse_app", "PGPASSFILE": "/없음/pgpass"}


# ---------------------------------------------------------------- 자료: Bronze 행과 같은 사건의 DB 행

def real_afters():
    """리허설 실제 행 16의 (이름, after). POINT_R 은 POINT_C 의 재발행 사본(op r) — DB 에는 한 행이다."""
    return [(name, json.loads(text)["after"]) for name, text in mv.REAL.items()]


def bronze_rows(afters=None):
    if afters is None:
        return [mv.mv_row(i, json.loads(mv.REAL[name])) for i, name in enumerate(mv.REAL)]
    return [mv.mv_row(i, mv.mv_envelope(**after)) for i, after in enumerate(afters)]


def jsonb_text(text):
    """앱이 넣은 JSON 과 키 순서·공백이 다른 jsonb 텍스트(해석 값은 같음)."""
    return None if text is None else json.dumps(json.loads(text), sort_keys=True, separators=(", ", ": "), ensure_ascii=False)


def db_row(after):
    """Debezium after(10열) → DB 에서 읽은 8열(`_COLUMNS` 순서): UUID 객체, +09:00 datetime, 다른 jsonb 텍스트."""
    def moment(text):
        return hop_rules._utc_text_time(text).astimezone(KST)
    return (uuid.UUID(after["outbox_uuid"]), after["event_type"], after["topic"], uuid.UUID(after["message_key"]), jsonb_text(after["raw_data"]),
            moment(after["occurred_at"]), jsonb_text(after["payload_json"]), moment(after["created_at"]))


def unique_db_rows():
    return [db_row(after) for name, after in real_afters() if name != "POINT_R"]


def strip(lines):
    return [{k: v for k, v in line.items() if k not in hop_rules.PIPELINE_COUNTS} for line in lines]


# ---------------------------------------------------------------- 가짜 DB·S3

class FakeConnection:
    """psycopg 연결 흉내: 속성·트랜잭션·문장을 `events` 에 차례로 남긴다. 질의는 SQL 의 갈래마다 술어(`published_at IS [NOT] NULL AND created_at >=|> %(since)s`)를
    읽어 그대로 거른다 — 갈래가 빠지거나 연산자가 바뀌면 결과가 달라진다."""
    BRANCH = re.compile(r"published_at IS (NOT )?NULL AND created_at (>=|>) %\(since\)s")

    def __init__(self, table, events, fail=None):
        self.table, self.events, self.fail = table, events, fail  # table: [(8열 행, published_at)]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.events.append("close")

    def __setattr__(self, name, value):
        if name in ("read_only", "isolation_level"):
            self.events.append(f"{name}={value}")
        object.__setattr__(self, name, value)

    @contextlib.contextmanager
    def transaction(self):
        self.events.append("begin")
        yield
        self.events.append("commit")

    def execute(self, sql, params=None):
        self.events.append("execute")
        if self.fail:
            raise self.fail
        if sql == "SELECT current_database()":
            return FakeResult([(DB,)])
        out = []
        for branch in sql.split(" UNION ALL "):
            match = self.BRANCH.search(branch)
            assert match, branch
            for row, published in self.table:
                ok = row[7] >= params["since"] if match[2] == ">=" else row[7] > params["since"]
                if ok and (published is not None) == bool(match[1]):
                    out.append(row)
        return FakeResult(out)


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


def fake_db(table, events=None, fail=None):
    events = [] if events is None else events
    return (lambda: FakeConnection(table, events, fail)), events


class FakeStore:
    """S3 흉내: 정해진 이름만 받는다. 없으면 None, 상한을 넘으면 InputError(진짜와 같음). 받은 이름을 `asked` 에 남긴다."""

    def __init__(self, files):
        self.files, self.asked = dict(files), []

    def get(self, name, limit):
        self.asked.append(name)
        body = self.files.get(name)
        if body is not None and len(body) > limit:
            raise mh.InputError(f"S3 파일이 상한({limit}바이트)보다 크다: {name}")
        return body


def published(window, rows, as_of, since, config=JOB_CONFIG):
    """Bronze manifest 잡의 summarize 로 만든 manifest 를 ndjson 으로 써서 파일 이름 → 바이트로(가짜 S3 에 올릴 것)."""
    plan = job.plan(window, as_of=as_of, since=since)
    lines, checkpoint, _ = job.summarize(iter(rows), plan, config, config["source"])
    with tempfile.TemporaryDirectory() as folder:
        ndjson.write(folder, f"hop_pipeline_{window}", lines, "hop_pipeline", hop_rules.HOP_SCHEMA_VERSION, checkpoint=checkpoint, now=plan["now"], chain=True)
        return {p.name: p.read_bytes() for p in Path(folder).iterdir() if not p.name.startswith(".")}


def table_of(rows, published_at=None):
    return [(row, published_at) for row in rows]


# ---------------------------------------------------------------- DB 쪽 = Bronze 쪽

class OutboxSideTests(unittest.TestCase):
    def test_db_rows_summarize_like_the_bronze_rows_of_the_same_events(self):
        db_rows, rows = unique_db_rows(), bronze_rows()
        for window, since in (("hour", datetime(2026, 11, 2, 4, tzinfo=UTC)), ("hour", datetime(2026, 11, 1, tzinfo=UTC)),
                              ("day", datetime(2026, 11, 2, tzinfo=UTC)), ("day", datetime(2026, 10, 28, tzinfo=UTC))):
            with self.subTest(window=window, since=since):
                size = hop_rules.WINDOWS[window]
                expected, expected_open, _ = hop_rules.summarize_changes(iter(rows), DB, NOW, 120, since, size, source=hop_rules.METAVERSE)
                lines, opened = mh.summarize_outbox(db_rows, DB, NOW, 120, since, size)
                self.assertEqual(json.dumps(lines, sort_keys=True), json.dumps(strip(expected), sort_keys=True))
                self.assertEqual(opened, expected_open)
                self.assertTrue(lines)

    def test_each_db_row_has_the_same_content_hash_as_its_bronze_row(self):
        """SL-4 가 해시를 내는 행은 SL-4 해시와, 그 밖의 행은 공용 규칙 해시와 같다(Reviewer C4)."""
        silver = 0
        for name, after in real_afters():
            with self.subTest(name=name):
                db_after = mh.outbox_after(db_row(after))
                self.assertEqual(mv.hop_hash(db_after), mv.hop_hash(after))
                if mv.silver_hash(after) is not None:
                    silver += 1
                    self.assertEqual(mv.silver_hash(db_after), mv.silver_hash(after))
        self.assertEqual(silver, 16)  # 리허설 실제 행은 모두 SL-4 해시가 있다(사유 0)

    def test_null_json_is_json_null_on_both_sides(self):
        """E3: payload_json 의 DB NULL 과 jsonb 'null' 은 둘 다 None 이고, Bronze 쪽 null 행과 같은 줄이다."""
        after = dict(real_afters()[2][1])  # FILL
        rows = bronze_rows([{**after, "payload_json": None}])
        expected = strip(hop_rules.summarize_changes(iter(rows), DB, NOW, 120, None, hop_rules.HOUR, source=hop_rules.METAVERSE)[0])
        for payload in (None, "null"):
            with self.subTest(payload=payload):
                row = db_row(after)
                row = row[:6] + (payload,) + row[7:]
                self.assertEqual(mh.summarize_outbox([row], DB, NOW, 120, None, hop_rules.HOUR)[0], expected)
                self.assertIsNone(mh.outbox_after(row)["payload_json"] and json.loads(mh.outbox_after(row)["payload_json"]))

    def test_two_db_rows_with_one_id_are_an_impossible_state(self):
        """PK 라 같은 outbox_uuid 가 둘일 수 없다 — 요약에 duplicates·conflicts 가 생기면 조용히 줄을 내지 않고 멈춘다."""
        rows = unique_db_rows()
        fill = next(row for row in rows if row[1] == "quiz_answered")  # 11-02 04시 — 닫힌 창
        changed = fill[:4] + (jsonb_text('{"x": 1}'),) + fill[5:]
        for extra, needle in ((fill, "duplicates"), (changed, "conflicts")):
            with self.subTest(needle=needle), self.assertRaisesRegex(mh.InputError, f"있을 수 없는 건수.*{needle}"):
                mh.summarize_outbox(rows + [extra], DB, NOW, 120, None, hop_rules.HOUR)

    def test_unreadable_db_rows_stop_without_values(self):
        after = dict(real_afters()[2][1])
        naive = db_row(after)
        naive = naive[:7] + (naive[7].replace(tzinfo=None),)
        deep = db_row(after)
        deep = deep[:4] + ("[" * 1000 + "]" * 1000,) + deep[5:]
        for row, kind in ((naive, "ValueError"), (deep, "ValueError")):
            with self.subTest(kind=kind), self.assertRaises(mh.InputError) as caught:
                mh.summarize_outbox([row], DB, NOW, 120, None, hop_rules.HOUR)
            message = str(caught.exception)
            self.assertIn("DB 쪽 행", message)
            self.assertNotIn(after["message_key"], message)
            self.assertNotIn("2026-11-02", message)
        with self.assertRaises(mh.InputError) as caught:
            mh.summarize_outbox([deep], DB, NOW, 120, None, hop_rules.HOUR)
        self.assertIn("원천 METAVERSE", str(caught.exception))
        self.assertIn(after["outbox_uuid"], str(caught.exception))

    def test_the_read_is_one_snapshot_both_branches_and_closed_before_summarizing(self):
        """두 갈래 술어가 모두 있고(Reviewer C5 (b)), 읽기 전용·REPEATABLE READ 한 트랜잭션에서 다 받은 뒤 닫는다(R4)."""
        self.assertIn("published_at IS NULL AND created_at >= %(since)s", mh.QUERY)
        self.assertIn("published_at IS NOT NULL AND created_at >= %(since)s", mh.QUERY)
        self.assertEqual(mh._COLUMNS.replace("::text", "").split(", "), list(mh.SOURCE["content"]))  # 읽는 열 = 공용 규칙의 내용 8열(이름·순서 — MVR-3a)
        rows = unique_db_rows()
        connect, events = fake_db(table_of(rows[:10]) + table_of(rows[10:], datetime(2026, 11, 4, tzinfo=UTC)))  # 둘째 갈래(발행된 행)에도 행
        since = datetime(2026, 11, 1, tzinfo=UTC)
        database, got = mh.read_outbox(connect, since)
        self.assertEqual(events, ["read_only=True", "isolation_level=3", "begin", "execute", "execute", "commit", "close"])
        self.assertEqual((database, len(got)), (DB, 15))
        with tempfile.TemporaryDirectory() as folder:
            mh.export(connect, Path(folder), "day", since, NOW, 120)
            self.assertEqual(events[-1], "close")  # 요약·쓰기 동안 DB 에 더 묻지 않는다
            lines = [json.loads(t) for t in (Path(folder) / ndjson.read_manifest(Path(folder) / "hop_outbox.manifest.json")["file"]).read_text().splitlines()]
        self.assertEqual(sum(line["count"] for line in lines), 15 - 1)  # 11-04 창은 08:00 에 열림(POINT 한 건)

    def test_a_row_exactly_at_since_is_read_in_both_branches(self):
        """since 경계(Reviewer C5 (a)): created_at = since 인 행은 들어간다(두 갈래 모두). 1µs 앞 행은 들어가도 규칙이 창 단위로 뺀다."""
        after = dict(real_afters()[4][1])  # CONTENT 2026-11-02T04:08:14Z
        since = datetime(2026, 11, 2, 4, tzinfo=UTC)
        row = db_row({**after, "created_at": "2026-11-02T04:00:00.000000Z"})
        before = db_row({**after, "outbox_uuid": str(uuid.uuid4()), "created_at": "2026-11-02T03:59:59.999999Z"})
        for published_at in (None, datetime(2026, 11, 3, tzinfo=UTC)):
            with self.subTest(published_at=published_at):
                connect, _ = fake_db([(row, published_at), (before, published_at)])
                _, got = mh.read_outbox(connect, since)
                self.assertEqual(got, [row])


# ---------------------------------------------------------------- 대조·판정

class CompareTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.folder))

    def hop(self, kind, lines, **checkpoint):
        folder = self.folder / f"{kind}-{len(list(self.folder.iterdir()))}"
        base = {"hop": kind, "window": "utc-hour", "since": "2026-11-02T04:00:00Z", "open_windows": []}
        if kind == "hop_pipeline":
            base |= {"not_insert_unplaced": 0, "foreign_source_rows": 0, "rule_sha256": hop_rules.rule_sha256(), **{f: [] for f in hop_rules.EXAMPLE_FIELDS}}
        ndjson.write(folder, kind, lines, kind, hop_rules.HOP_SCHEMA_VERSION, checkpoint=base | checkpoint, now=NOW)
        return mh.load(ndjson.manifest_path(folder, kind), kind)

    def pair(self, outbox_lines, pipeline_lines, **pipeline_checkpoint):
        return self.hop("hop_outbox", outbox_lines), self.hop("hop_pipeline", pipeline_lines, **pipeline_checkpoint)

    def lines(self):
        expected = hop_rules.summarize_changes(iter(bronze_rows()), DB, NOW, 120, datetime(2026, 11, 2, 4, tzinfo=UTC), hop_rules.HOUR, source=hop_rules.METAVERSE)[0]
        return strip(expected), expected

    def test_equal_lines_match_and_confirm_their_windows(self):
        outbox, pipeline = self.lines()
        rows = mh.compare(*self.pair(outbox, pipeline))
        self.assertEqual({r["status"] for r in rows}, {mh.MATCH})
        confirmed, unconfirmed = mh.judge(rows)
        self.assertEqual((len(confirmed), unconfirmed), (len({line["window_start"] for line in outbox}), []))

    def test_differences_are_mismatches_and_only_their_window_is_unconfirmed(self):
        outbox, pipeline = self.lines()
        windows = sorted({line["window_start"] for line in outbox})
        for change, fields in ((lambda l: l | {"count": l["count"] + 1}, ["count"]), (lambda l: l | {"ids_sha256": "0" * 64}, ["ids_sha256"]),
                               (lambda l: l | {"content_sha256": "1" * 64}, ["content_sha256"]), (lambda l: l | {"conflicts": 1}, ["conflicts"]),
                               (lambda l: l | {"not_insert": 2}, ["not_insert"])):
            with self.subTest(fields=fields):
                changed = [change(pipeline[0])] + pipeline[1:]
                rows = mh.compare(*self.pair(outbox, changed))
                bad = [r for r in rows if r["status"] == mh.MISMATCH]
                self.assertEqual([(r["window_start"], r["fields"]) for r in bad], [(pipeline[0]["window_start"], fields)])
                confirmed, unconfirmed = mh.judge(rows)
                self.assertEqual(unconfirmed, [pipeline[0]["window_start"]])
                self.assertEqual(len(confirmed), len(windows) - 1)

    def test_a_line_on_one_side_only_is_a_mismatch_in_both_directions(self):
        outbox, pipeline = self.lines()
        ghost = pipeline[0] | {"event_name": "ghost_event", "count": 0, "ids_sha256": hop_rules.EMPTY_SHA256, "content_sha256": hop_rules.EMPTY_SHA256, "duplicates": 1}
        for a, b, field in ((outbox, pipeline[1:], "line_missing_in_b"), (outbox, pipeline + [ghost], "line_missing_in_a")):
            with self.subTest(field=field):
                rows = mh.compare(*self.pair(a, b))
                self.assertEqual([r["fields"] for r in rows if r["status"] == mh.MISMATCH], [[field]])

    def test_a_window_open_on_one_side_is_not_judged(self):
        outbox, pipeline = self.lines()
        window = pipeline[0]["window_start"]
        changed = [line for line in pipeline if line["window_start"] != window]
        for o_open, p_open in (([window], []), ([], [window])):
            with self.subTest(outbox_open=o_open):
                rows = mh.compare(self.hop("hop_outbox", outbox if not o_open else [l for l in outbox if l["window_start"] != window], open_windows=o_open),
                                  self.hop("hop_pipeline", changed, open_windows=p_open))
                self.assertEqual({r["status"] for r in rows if r["window_start"] == window}, {mh.OPEN})
                self.assertNotIn(window, sum(mh.judge(rows), []))

    def test_pipeline_problems_stop_the_pair(self):
        outbox, pipeline = self.lines()
        for checkpoint, needle in (({"not_insert_unplaced": 1}, "not_insert_unplaced"), ({"foreign_source_rows": 16}, "foreign_source_rows"),
                                   ({"rule_sha256": "a" * 64}, "rule_sha256"), ({"since": "2026-11-02T05:00:00Z"}, "since"), ({"window": "utc-day"}, "창 크기")):
            with self.subTest(needle=needle), self.assertRaisesRegex(mh.InputError, needle):
                mh.compare(self.hop("hop_outbox", outbox), self.hop("hop_pipeline", [] if "window" in checkpoint else pipeline, **checkpoint))

    def test_manifest_level_contract_checks(self):
        """Reviewer C5 (d): source_kind·schema_version·checkpoint.hop·window·since 형식·checkpoint 모양 → 입력 실패."""
        folder = self.folder / "bad"
        good = {"hop": "hop_pipeline", "window": "utc-hour", "since": "2026-11-02T04:00:00Z", "open_windows": [], "not_insert_unplaced": 0,
                "foreign_source_rows": 0, "rule_sha256": hop_rules.rule_sha256(), **{f: [] for f in hop_rules.EXAMPLE_FIELDS}}
        cases = (("와야 하는 자리", {"kind": "hop_outbox"}), ("와야 하는 자리", {"kind": "hop_other"}), ("schema_version", {"version": 2}),
                 ("구간", {"checkpoint": good | {"hop": "hop_outbox"}}), ("창 크기", {"checkpoint": good | {"window": "utc-week"}}),
                 ("since", {"checkpoint": good | {"since": "2026-11-02T04:30:00Z"}}), ("since", {"checkpoint": good | {"since": "2026-11-2T04:00:00Z"}}),
                 ("since", {"checkpoint": good | {"since": "2026-11-02"}}), ("open_windows", {"checkpoint": good | {"open_windows": "x"}}),
                 ("foreign_source_rows", {"checkpoint": {k: v for k, v in good.items() if k != "foreign_source_rows"}}),
                 ("rule_sha256", {"checkpoint": good | {"rule_sha256": "A" * 64}}), ("duplicate_examples", {"checkpoint": good | {"duplicate_examples": [{"event_id": 1}]}}))
        for needle, case in cases:
            with self.subTest(needle=needle, case=case):
                name = case.get("kind", "hop_pipeline")
                ndjson.write(folder, name, [], name, case.get("version", 1), checkpoint=case.get("checkpoint", good), now=NOW)
                with self.assertRaisesRegex(mh.InputError, needle):
                    mh.load(ndjson.manifest_path(folder, name), "hop_pipeline")
        with self.assertRaisesRegex(mh.InputError, "checkpoint 가 객체가 아니다"):
            ndjson.write(folder, "hop_pipeline", [], "hop_pipeline", 1, checkpoint=None, now=NOW)
            mh.load(ndjson.manifest_path(folder, "hop_pipeline"), "hop_pipeline")

    def test_line_shape_errors(self):
        outbox, pipeline = self.lines()
        line = pipeline[0]
        for bad, needle in ((line | {"window_end": line["window_start"]}, "창 길이"), (line | {"window_start": "2026-11-02T04:30:00Z", "window_end": "2026-11-02T05:30:00Z"}, "UTC 경계"),
                            (line | {"event_name": ""}, "event_name"), (line | {"count": True}, "count"), (line | {"count": -1}, "count"),
                            (line | {"ids_sha256": "X" * 64}, "ids_sha256"), ({k: v for k, v in line.items() if k != "duplicates"}, "duplicates"),
                            ({k: v for k, v in line.items() if k != "content_sha256"}, "content_sha256")):
            with self.subTest(needle=needle), self.assertRaisesRegex(mh.InputError, needle):
                self.hop("hop_pipeline", [bad])
        with self.assertRaisesRegex(mh.InputError, "중복"):
            self.hop("hop_pipeline", [line, line])


# ---------------------------------------------------------------- 한 번의 실행

class RunTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.base))

    def run_once(self, files, table, now="2026-11-04T08:00:00+00:00", window="hour", environ=ENVIRON, fail=None, store=None):
        store = store or FakeStore(files)
        connect, events = fake_db(table, fail=fail)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = mh.main(["--window", window, "--dir", str(self.base), "--as-of", now], environ, lambda config: store, connect)
        state = json.loads((self.base / f"reconcile-{window}.json").read_text()) if (self.base / f"reconcile-{window}.json").exists() else None
        return rc, state, out.getvalue(), store, events

    def verdicts(self, window="hour"):
        pointer = ndjson.manifest_path(self.base / window / "verdict", "hop_verdict")
        manifest = ndjson.read_manifest(pointer)
        return manifest, [json.loads(t) for t in (pointer.parent / manifest["file"]).read_text().splitlines()]

    def test_ok_writes_state_report_and_a_verdict_generation(self):
        files = published("day", bronze_rows(), "2026-11-05T00:05:00Z", None)  # 기대 since(11-05 − 8일 = 10-28) 그대로
        rc, state, output, _, _ = self.run_once(files, table_of(unique_db_rows()), now="2026-11-05T00:17:00+00:00", window="day")
        self.assertEqual((rc, state["status"], state["stage"], state["mismatch_lines"], state["unconfirmed"]), (0, "ok", None, 0, 0))
        self.assertEqual(state["confirmed"], 3)  # 11-02·11-03·11-04
        self.assertEqual((state["pipeline_grace_seconds"], state["grace_seconds"], state["pipeline_since"]), (120, 120, "2026-10-28T00:00:00Z"))
        manifest, lines = self.verdicts("day")
        self.assertEqual([(l["window_start"], l["confirmed"]) for l in lines], [("2026-11-02T00:00:00Z", True), ("2026-11-03T00:00:00Z", True), ("2026-11-04T00:00:00Z", True)])
        self.assertEqual(manifest["checkpoint"]["inputs"], {"hop_outbox": state["outbox_manifest_sha256"], "pipeline": state["pipeline_manifest_sha256"]})
        self.assertEqual(manifest["checkpoint"]["rule_sha256"], hop_rules.rule_sha256())  # 판정 사슬에 적는 규칙 판(MVR-3a 리뷰 r1 기록 1 — M10)
        self.assertIn("reconcile metaverse day: ok rc=0", output)
        rc, state, _, _, _ = self.run_once(files, table_of(unique_db_rows()), now="2026-11-05T00:17:00+00:00", window="day")  # 다음 회차 — 사슬이 한 세대 늘어남
        manifest2, _ = self.verdicts("day")
        self.assertEqual((rc, manifest2["previous_sha256"]), (0, ndjson.manifest_digest(manifest)))
        self.assertEqual(ndjson.verify_chain(ndjson.manifest_path(self.base / "day" / "verdict", "hop_verdict")), [])

    def test_a_missing_outbox_row_is_a_mismatch_and_wins_over_late(self):
        files = published("hour", bronze_rows(), "2026-11-04T08:00:00Z", "2026-11-02T04:00:00Z")  # 기대(11-02 08시)보다 이른 since — 늦음이기도 하다
        rc, state, _, _, _ = self.run_once(files, table_of(unique_db_rows()[1:]))
        self.assertEqual((rc, state["status"], state["stage"]), (1, "mismatch", "verify"))
        self.assertEqual(state["mismatch_lines"], 1)
        _, lines = self.verdicts()
        self.assertEqual([l["window_start"] for l in lines if not l["confirmed"]], ["2026-11-04T05:00:00Z"])

    def test_an_early_since_is_late_after_comparing(self):
        files = published("hour", bronze_rows(), "2026-11-04T08:00:00Z", "2026-11-02T04:00:00Z")
        rc, state, _, _, _ = self.run_once(files, table_of(unique_db_rows()))
        self.assertEqual((rc, state["status"], state["stage"], state["late_by_seconds"]), (3, "late", "since", 4 * 3600))
        self.assertEqual((state["mismatch_lines"], state["unconfirmed"]), (0, 0))
        self.assertGreater(state["confirmed"], 0)

    def test_the_outbox_closes_windows_at_the_earlier_of_the_job_time_and_now(self):
        """min 의 반대쪽(Reviewer C5 (c)): 잡이 11-04 05시 창이 닫히기 전에 요약했다(그 창의 행이 Bronze 에 있어도 열린 창). DB 에는 그 창 행이 있다.
        as_of = min 이면 DB 쪽도 그 창을 열린 창으로 적어 판정하지 않는다. 지금으로 닫으면 거짓 불일치가 된다."""
        early = [r for r in bronze_rows() if json.loads(r[1])["after"]["created_at"] < "2026-11-04"]  # Bronze 에는 그 창 행이 아직 없다
        files = published("hour", early, "2026-11-04T06:01:00Z", "2026-11-02T04:00:00Z")
        rc, state, _, _, _ = self.run_once(files, table_of(unique_db_rows()))
        self.assertEqual((state["status"], state["mismatch_lines"]), ("late", 0))
        self.assertEqual(state["pipeline_as_of"], "2026-11-04T06:01:00+00:00")
        _, lines = self.verdicts()
        self.assertNotIn("2026-11-04T05:00:00Z", [l["window_start"] for l in lines])

    def test_a_job_clock_ahead_of_now_does_not_close_windows_early(self):
        files = published("hour", bronze_rows(), "2026-11-04T09:00:00Z", "2026-11-02T04:00:00Z")  # 잡 시계가 1시간 앞섬
        rc, state, _, _, _ = self.run_once(files, table_of(unique_db_rows()), now="2026-11-04T06:01:00+00:00")
        self.assertEqual(state["mismatch_lines"], 0)
        _, lines = self.verdicts()
        self.assertNotIn("2026-11-04T05:00:00Z", [l["window_start"] for l in lines])  # 이쪽 지금(06:01)으로는 아직 여유 안 — 열린 창

    def test_metaverse_rows_published_without_the_source_setting_fail_the_pair(self):
        """Planner 조건(fail closed 뒤 절반): 메타버스 잡이 HOP_SOURCE 를 빠뜨려 ADS 로 요약하면 대조가 rc 2 이고 확정이 없다."""
        files = published("hour", bronze_rows(), "2026-11-04T08:00:00Z", "2026-11-02T04:00:00Z", JOB_CONFIG | {"source": job.SOURCES["ADS"]})
        rc, state, _, _, _ = self.run_once(files, table_of(unique_db_rows()))
        self.assertEqual((rc, state["status"], state["stage"], state["confirmed"]), (2, "input_failed", "verify", 0))
        self.assertIn("foreign_source_rows 16건", state["message"])
        _, lines = self.verdicts()
        self.assertTrue(lines and not any(l["confirmed"] for l in lines))  # outbox 창을 모두 미확정으로

    def test_an_outbox_side_failure_leaves_no_verdict(self):
        """판정은 이 DB 의 outbox 와 이어질 때만 남긴다 — outbox manifest 를 읽지 못하면 기록하지 않는다(Reviewer 코드 리뷰 권고 1, 계획 §6 대사 5)."""
        files = published("hour", bronze_rows(), "2026-11-04T08:00:00Z", "2026-11-02T04:00:00Z")
        real_load = mh.load

        def load(path, kind):
            if kind == "hop_outbox":
                raise mh.InputError("hop_outbox: 검증 실패(시험)")
            return real_load(path, kind)

        mh.load = load
        try:
            rc, state, _, _, _ = self.run_once(files, table_of(unique_db_rows()))
        finally:
            mh.load = real_load
        self.assertEqual((rc, state["status"], state["stage"], state["verdict_manifest_sha256"], state["confirmed"]), (2, "input_failed", "verify", None, None))
        self.assertFalse((self.base / "hour" / "verdict").exists())

    def test_input_failures(self):
        good = published("hour", bronze_rows(), "2026-11-04T08:00:00Z", "2026-11-02T04:00:00Z")
        pointer = "hop_pipeline_hour.manifest.json"
        data = next(n for n in good if n.endswith(".ndjson"))
        manifest = json.loads(good[pointer])

        def with_pointer(**fields):
            return good | {pointer: json.dumps(manifest | fields).encode()}

        cases = (("포인터 없음", {k: v for k, v in good.items() if k != pointer}, None, "fetch"),
                 ("SHA-256 다름", with_pointer(sha256="0" * 64), None, "fetch"),
                 ("행 수 다름", with_pointer(rows=manifest["rows"] + 1), None, "fetch"),
                 ("포인터 상한", good | {pointer: b" " * (mh.POINTER_LIMIT + 1)}, None, "fetch"),
                 ("데이터 상한", good | {data: b"x" * (mh.DATA_LIMIT + 1)}, None, "fetch"),
                 ("DB 오류", good, ConnectionError("연결 끊김"), "export"),
                 ("statement_timeout", good, TimeoutError("canceling statement due to statement timeout"), "export"))
        for label, files, fail, stage in cases:
            with self.subTest(label):
                rc, state, _, _, _ = self.run_once(files, table_of(unique_db_rows()), fail=fail)
                self.assertEqual((rc, state["status"], state["stage"]), (2, "input_failed", stage))

    def test_a_failed_fetch_leaves_the_folder_as_it_was_and_never_asks_for_other_files(self):
        """Reviewer C3: 확인한 뒤에만 쓴다. 잘못된 file 이름(경로·다른 이름)은 S3 에 묻지 않는다."""
        good = published("hour", bronze_rows(), "2026-11-04T08:00:00Z", "2026-11-02T04:00:00Z")
        self.run_once(good, table_of(unique_db_rows()))
        folder = self.base / "hour" / "pipeline"
        before = {p.name: p.read_bytes() for p in folder.iterdir()}
        pointer = "hop_pipeline_hour.manifest.json"
        manifest = json.loads(good[pointer])
        for label, fields in (("SHA-256", {"sha256": "0" * 64}), ("행 수", {"rows": manifest["rows"] + 1}), ("경로", {"file": "../hop_pipeline_hour.0123456789abcdef.ndjson"}),
                              ("다른 이름", {"file": "hop_pipeline_day." + manifest["file"].split(".")[1] + ".ndjson"})):
            with self.subTest(label):
                rc, _, _, store, _ = self.run_once(good | {pointer: json.dumps(manifest | fields).encode()}, table_of(unique_db_rows()))
                self.assertEqual(rc, 2)
                self.assertEqual({p.name: p.read_bytes() for p in folder.iterdir()}, before)
                self.assertTrue(set(store.asked) <= {pointer, manifest["file"]}, store.asked)
        rc, _, _, _, _ = self.run_once(good | {pointer: b" " * (mh.POINTER_LIMIT + 1)}, table_of(unique_db_rows()))
        self.assertEqual(({p.name: p.read_bytes() for p in folder.iterdir()}, rc), (before, 2))

    def test_a_held_lock_skips_without_touching_the_state_file(self):
        import fcntl

        good = published("hour", bronze_rows(), "2026-11-04T08:00:00Z", "2026-11-02T04:00:00Z")
        self.run_once(good, table_of(unique_db_rows()))
        before = (self.base / "reconcile-hour.json").read_bytes()
        with open(self.base / ".reconcile-hour.lock", "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            rc, _, output, _, _ = self.run_once(good, table_of(unique_db_rows()))
        self.assertEqual((rc, (self.base / "reconcile-hour.json").read_bytes()), (75, before))
        self.assertIn("skipped", output)

    def test_missing_settings_and_bad_as_of(self):
        rc, state, _, _, _ = self.run_once({}, [], environ={k: v for k, v in ENVIRON.items() if k != "PGPASSFILE"})
        self.assertEqual((rc, state["stage"]), (2, "settings"))
        self.assertIn("PGPASSFILE", state["message"])
        for as_of in ("어제", "2026-11-04T08:00:00"):
            with self.subTest(as_of=as_of), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(mh.main(["--window", "hour", "--dir", str(self.base), "--as-of", as_of], ENVIRON, lambda c: FakeStore({}), fake_db([])[0]), 2)

    def test_secrets_are_read_from_files_and_never_printed(self):
        """진짜 S3Store: 키는 파일에서 읽고, 접속 실패(닫힌 포트)의 출력·상태 파일에 키 값이 없다."""
        secret = self.base / "secrets"
        secret.mkdir()
        (secret / "a").write_text("AKIAEXAMPLESECRET1\n")
        (secret / "s").write_text("wJalrXUtnFEMIEXAMPLESECRET2\n")
        environ = ENVIRON | {"HOP_S3_ACCESS_KEY_FILE": str(secret / "a"), "HOP_S3_SECRET_KEY_FILE": str(secret / "s")}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = mh.main(["--window", "hour", "--dir", str(self.base / "w"), "--as-of", "2026-11-04T08:00:00+00:00"], environ, mh.S3Store, fake_db([])[0])
        state = (self.base / "w" / "reconcile-hour.json").read_text()
        self.assertEqual(rc, 2)
        for value in ("AKIAEXAMPLESECRET1", "wJalrXUtnFEMIEXAMPLESECRET2"):
            self.assertNotIn(value, out.getvalue() + state)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = mh.main(["--window", "hour", "--dir", str(self.base / "m"), "--as-of", "2026-11-04T08:00:00+00:00"], ENVIRON, mh.S3Store, fake_db([])[0])
        self.assertEqual((rc, json.loads((self.base / "m" / "reconcile-hour.json").read_text())["stage"]), (2, "fetch"))  # 키 파일이 없으면 받기 전에 멈춘다


class LayerTests(unittest.TestCase):
    def test_reconcile_tools_use_only_stdlib_psycopg_boto3_and_pipeline(self):
        """광고 서버 import 금지는 `test_layers.py` 규칙 1(JOB_DIRS 의 reconcile)이 본다. 여기서는 그보다 좁은 허용 목록이다."""
        files = sorted((ROOT / "pipeline" / "reconcile").glob("*.py"))
        self.assertIn(ROOT / "pipeline" / "reconcile" / "metaverse_hops.py", files)
        allowed = set(sys.stdlib_module_names) | {"pipeline", "psycopg", "boto3", "botocore"}
        self.assertEqual(violations(files, lambda name, level: level == 0 and name in allowed), [])
        self.assertIn(("pipeline", 0), imports((ROOT / "pipeline" / "reconcile" / "metaverse_hops.py").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
