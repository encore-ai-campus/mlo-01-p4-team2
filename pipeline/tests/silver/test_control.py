"""pipeline/silver/control.py — 드라이버 쪽 설정·정체성 계산(Spark 없음): 넘김 목록, 멱등 appId·checkpoint 정체성, silver_release, (SL-7) 세대 경로·포인터 도구 인자."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.silver import control

TOPIC = "metaverse.outbox.events"


def ack_file(*lines, domain="metaverse"):
    return json.dumps({"domain": domain, "lines": list(lines)}, ensure_ascii=False)


POINT = {"topic": TOPIC, "partition": 0, "offset": 12, "error_code": "foreign_source", "reason": "커넥터 표 목록 오류", "incident": "INC-7",
         "acknowledged_by": "운영자", "acknowledged_at": "2026-11-04T06:00:00Z"}
RANGE = {"topic": TOPIC, "partition": 1, "offset_start": 10, "offset_end": 20, "row_count": 7, "error_code": "not_insert", "reason": "TRUNCATE",
         "incident": "INC-8", "acknowledged_by": "운영자", "acknowledged_at": "2026-11-04T06:00:00Z"}


class LoadAcknowledged(unittest.TestCase):
    def assert_rejected(self, data):
        with self.assertRaises(ValueError):
            control.load_acknowledged(data, "metaverse")

    def test_point_and_range_lines(self):
        point, span = control.load_acknowledged(ack_file(POINT, RANGE).encode("utf-8"), "metaverse")
        self.assertEqual((point["start"], point["end"], point["row_count"]), (12, 12, None))
        self.assertEqual((span["start"], span["end"], span["row_count"]), (10, 20, 7))
        self.assertEqual((point["incident"], span["reason"]), ("INC-7", "TRUNCATE"))
        self.assertEqual(control.load_acknowledged(ack_file(), "metaverse"), [])

    def test_file_shape(self):
        self.assert_rejected(ack_file(POINT, domain="ads"))
        self.assert_rejected(json.dumps({"domain": "metaverse", "lines": [], "x": 1}))
        self.assert_rejected(json.dumps({"domain": "metaverse", "lines": {}}))
        self.assert_rejected(json.dumps([POINT]))
        self.assert_rejected('{"domain": "metaverse", "domain": "metaverse", "lines": []}')
        self.assert_rejected(b'{"domain": "metaverse", "lines": [], "x": "\xff"}')

    def test_line_keys_and_values(self):
        bad = [POINT | {"offset_start": 1}, {k: v for k, v in POINT.items() if k != "incident"}, POINT | {"error_code": "decode"},
               POINT | {"reason": " "}, POINT | {"acknowledged_by": 1}, POINT | {"acknowledged_at": "2026-1-4T6:0:0Z"},
               POINT | {"acknowledged_at": "2026-11-04 06:00:00"}, POINT | {"acknowledged_at": None},
               POINT | {"partition": -1}, POINT | {"offset": True}, POINT | {"offset": 1.0}, RANGE | {"offset_start": 21}, RANGE | {"row_count": 0}, "줄"]
        for line in bad:
            with self.subTest(line=line):
                self.assert_rejected(ack_file(line))

    def test_overlapping_lines(self):
        self.assert_rejected(ack_file(RANGE, RANGE | {"offset_start": 20, "offset_end": 30}))
        self.assert_rejected(ack_file(RANGE, POINT | {"partition": 1, "offset": 15}))
        lines = control.load_acknowledged(ack_file(RANGE, RANGE | {"partition": 2}, RANGE | {"offset_start": 21, "offset_end": 30},
                                                   RANGE | {"topic": "metaverse.outbox.events.g2"}), "metaverse")
        self.assertEqual(len(lines), 4)


GHOST = {"event_id": "0aabe9c8-6b77-4b20-9e52-3360a8f0b86c", "reason": "PITR 뒤 outbox 에 없음", "incident": "INC-9", "listed_by": "운영자",
         "listed_at": "2026-11-09T06:00:00Z"}


class LoadQuarantine(unittest.TestCase):  # SL-9 — 계획 38 §2.1 목록 파일
    def assert_rejected(self, data):
        with self.assertRaises(ValueError):
            control.load_quarantine(data, "metaverse")

    def test_lines(self):
        other = GHOST | {"event_id": "3f0e6a52-1b7c-4d1e-9a3b-5c6d7e8f9a0b"}
        self.assertEqual(control.load_quarantine(ack_file(GHOST, other).encode("utf-8"), "metaverse"), [GHOST, other])
        self.assertEqual(control.load_quarantine(ack_file(), "metaverse"), [])
        shipped = Path(control.__file__).resolve().parent / "quarantine" / "metaverse.json"  # 래퍼가 넘기는 파일 — 처음에는 빈 목록
        self.assertEqual(control.load_quarantine(shipped.read_bytes(), "metaverse"), [])

    def test_file_shape(self):
        self.assert_rejected(ack_file(GHOST, domain="ads"))
        self.assert_rejected(json.dumps({"domain": "metaverse", "lines": [], "x": 1}))
        self.assert_rejected(json.dumps({"domain": "metaverse", "lines": {}}))
        self.assert_rejected(json.dumps([GHOST]))
        self.assert_rejected('{"domain": "metaverse", "domain": "metaverse", "lines": []}')
        self.assert_rejected(b'{"domain": "metaverse", "lines": [], "x": "\xff"}')

    def test_line_keys_and_values(self):
        bad = [GHOST | {"x": 1}, {k: v for k, v in GHOST.items() if k != "incident"}, GHOST | {"event_id": GHOST["event_id"].upper()},
               GHOST | {"event_id": GHOST["event_id"] + "\n"}, GHOST | {"event_id": "0aabe9c86b774b209e523360a8f0b86c"}, GHOST | {"event_id": 1},
               GHOST | {"reason": ""}, GHOST | {"incident": None}, GHOST | {"listed_by": 1}, GHOST | {"listed_at": "2026-11-9T6:0:0Z"},
               GHOST | {"listed_at": "2026-11-09 06:00:00"}, GHOST | {"listed_at": "2026-11-09T06:00:00+00:00"}, "줄"]
        for line in bad:
            with self.subTest(line=line):
                self.assert_rejected(ack_file(line))
        self.assert_rejected(ack_file(GHOST, GHOST | {"reason": "다른 이유"}))  # 같은 event_id 두 줄


class TxnIds(unittest.TestCase):
    CK = "3f0e6a52-1b7c-4d1e-9a3b-5c6d7e8f9a0b"

    def test_app_id(self):
        self.assertEqual(control.txn_app_id("metaverse", 1, "events", self.CK), f"silver:metaverse:g1:events:{self.CK}")
        self.assertEqual(control.txn_app_id("ads", 12, "stops", self.CK), f"silver:ads:g12:stops:{self.CK}")
        self.assertEqual(control.TXN_TABLES, ("events", "errors", "stops", "quarantine"))
        for args in (("meta:verse", 1, "events", self.CK), ("metaverse", 0, "events", self.CK), ("metaverse", True, "events", self.CK),
                     ("metaverse", "1", "events", self.CK), ("metaverse", 1, "gold", self.CK), ("metaverse", 1, "events", "a:b"),
                     ("Metaverse", 1, "events", self.CK), ("metaverse", 1, "events", "")):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    control.txn_app_id(*args)

    def test_foreign_checkpoints(self):
        mine = [control.txn_app_id("metaverse", 1, table, self.CK) for table in control.TXN_TABLES]
        others = ["silver:metaverse:g10:events:old", "silver:metaverse:g2:events:old", "silver:ads:g1:events:old", "sl2:e12", "silver:meta:g1:events:old"]
        self.assertEqual(control.foreign_checkpoints(mine + others, "metaverse", 1, self.CK), [])
        stale = ["silver:metaverse:g1:events:ck2", "silver:metaverse:g1:stops:ck1", "silver:metaverse:g1:errors:ck1"]
        self.assertEqual(control.foreign_checkpoints(mine + others + stale, "metaverse", 1, self.CK), sorted(stale))


class StartCheck(unittest.TestCase):  # SL-3c 결정 S2 (가) — 시작 검사 ①~④
    CK, OLD = "3f0e6a52-1b7c-4d1e-9a3b-5c6d7e8f9a0b", "0aabe9c8-6b77-4b20-9e52-3360a8f0b86c"

    def check(self, silver_ids=(), error_ids=(), quarantine_ids=(), *, checkpoint=None, finished=False):
        control.start_check(silver_ids, error_ids, quarantine_ids, domain="metaverse", generation=1, checkpoint=checkpoint, finished=finished)

    def ids(self, table, checkpoint=None):
        return [control.txn_app_id("metaverse", 1, table, checkpoint or self.CK)]

    def test_new_start_and_normal_restart(self):
        self.check()  # ① 처음
        self.check(["silver:metaverse:g2:events:x", "silver:ads:g1:events:x"], checkpoint=None)  # ① 다른 세대·원천 기록은 상관없음
        self.check(self.ids("events"), self.ids("errors") + self.ids("stops"), checkpoint=self.CK, finished=True)  # 다시 시작
        self.check((), self.ids("stops"), checkpoint=self.CK, finished=False)  # 첫 배치가 멈춘 뒤(끝난 배치 없음)
        self.check((), (), checkpoint=self.CK, finished=False)  # checkpoint 만 만들어지고 배치 전에 죽음
        self.check(self.ids("events"), self.ids("errors"), self.ids("quarantine"), checkpoint=self.CK, finished=True)  # (SL-9) 격리 기록 있음
        self.check(self.ids("events"), self.ids("errors"), (), checkpoint=self.CK, finished=True)  # 격리 기록 없음 — ④에 들지 않는다(D3 (가))

    def test_stops(self):
        cases = [
            ("checkpoint 가 없는데", dict(silver_ids=self.ids("events"))),  # ② checkpoint 를 지움
            ("checkpoint 가 없는데", dict(error_ids=self.ids("stops"))),  # ② 멈춘 기록만 있어도
            ("checkpoint 가 없는데", dict(finished=True)),  # metadata 없이 commits 만 있음(깨진 checkpoint)
            ("다른 checkpoint", dict(silver_ids=self.ids("events", self.OLD) + self.ids("events"), checkpoint=self.CK, finished=True)),  # ③
            ("다른 checkpoint", dict(error_ids=self.ids("errors", self.OLD), checkpoint=self.CK)),  # ③ 오류 표에만
            ("Silver 표에 그 기록이 없다", dict(error_ids=self.ids("errors"), checkpoint=self.CK, finished=True)),  # ④ Silver 표를 지움
            ("오류 표에 그 기록이 없다", dict(silver_ids=self.ids("events"), checkpoint=self.CK, finished=True)),  # ④ 오류 표를 지움(SL-3c 리뷰 R1)
            ("Silver·오류 표에 그 기록이 없다", dict(silver_ids=self.ids("stops"), error_ids=self.ids("stops"), checkpoint=self.CK, finished=True)),  # ④ events·errors appId 만 센다
            ("checkpoint 가 없는데", dict(quarantine_ids=self.ids("quarantine"))),  # (SL-9) ② 격리 표 기록만 있어도
            ("다른 checkpoint", dict(quarantine_ids=self.ids("quarantine", self.OLD), checkpoint=self.CK)),  # (SL-9) ③ 격리 표에만
            ("Silver·오류 표에 그 기록이 없다", dict(quarantine_ids=self.ids("quarantine"), checkpoint=self.CK, finished=True)),  # 격리 기록은 ④를 채우지 않는다
        ]
        for needle, kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(control.IdentityError, needle):
                self.check(**kwargs)

    def test_checkpoint_id(self):
        self.assertEqual(control.checkpoint_id('{"id":"%s"}' % self.CK), self.CK)
        self.assertEqual(control.checkpoint_id('{"id":"%s"}\n' % self.CK), self.CK)
        for text in ("", "{}", '{"id": 1}', '{"id":"%s"}' % self.CK.upper(), '{"id":"%s\\n"}' % self.CK, '["%s"]' % self.CK, None, '{"id":"x:y"}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                control.checkpoint_id(text)


ENV = {"SILVER_LAKE_ROOT": "s3a://ads-lake/", "SILVER_BRONZE_TABLE": "s3a://ads-lake/bronze/metaverse/outbox_events", "SILVER_SOURCE_DB": "metaverse_lab",
       "SILVER_SOURCE_SCHEMA": "public", "SILVER_SOURCE_TABLE": "eventing_eventoutbox", "SILVER_ACKNOWLEDGED": "/code/pipeline/silver/acknowledged/metaverse.json",
       "SILVER_QUARANTINE": "/code/pipeline/silver/quarantine/metaverse.json", "SILVER_CODE_ROOT": "/code", "SILVER_MAX_FILES_PER_TRIGGER": "200", "SILVER_MAX_BYTES_PER_TRIGGER": "512m"}
S3 = {"S3_ENDPOINT": "http://127.0.0.1:9000", "S3_REGION": "ap-northeast-2", "S3_ACCESS_KEY_FILE": "/keys/access", "S3_SECRET_KEY_FILE": "/keys/secret"}


class Settings(unittest.TestCase):  # SL-3c 결정 S1·정한 것 1
    def test_source_settings(self):
        source = {"db": "metaverse_lab", "schema": "public", "table": "eventing_eventoutbox"}
        self.assertEqual(control.source_settings(dict(source)), source)
        for bad in (None, [], {"db": "a", "schema": "b"}, {**source, "extra": "x"}, {**source, "db": ""}, {**source, "db": 1}, {**source, "table": " t"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                control.source_settings(bad)

    def test_job_settings(self):
        settings = control.job_settings(ENV, "metaverse", 1)
        self.assertEqual((settings.silver, settings.errors, settings.reconcile, settings.quarantine, settings.checkpoint),
                         ("s3a://ads-lake/silver/metaverse/events/g1", "s3a://ads-lake/silver/metaverse/errors/g1",
                          "s3a://ads-lake/silver/metaverse/reconcile/g1", "s3a://ads-lake/silver/metaverse/quarantine/g1",
                          "s3a://ads-lake/checkpoints/silver/metaverse/events/g1"))
        self.assertEqual((settings.acknowledged, settings.quarantine_list), (ENV["SILVER_ACKNOWLEDGED"], ENV["SILVER_QUARANTINE"]))
        self.assertEqual(settings.source, {"db": "metaverse_lab", "schema": "public", "table": "eventing_eventoutbox"})
        self.assertEqual((settings.bronze, settings.max_files, settings.max_bytes, settings.code_root), (ENV["SILVER_BRONZE_TABLE"], 200, "512m", "/code"))
        for limit in ("1", "1048576", "50k", "512m", "1g"):  # Delta 가 읽는 표기(SL-5 D3 (다))
            with self.subTest(limit=limit):
                self.assertEqual(control.job_settings({**ENV, "SILVER_MAX_BYTES_PER_TRIGGER": limit}, "metaverse", 1).max_bytes, limit)

    def test_no_defaults(self):
        with self.assertRaisesRegex(ValueError, "SILVER_LAKE_ROOT, SILVER_BRONZE_TABLE"):
            control.job_settings({k: v for k, v in ENV.items() if k not in ("SILVER_LAKE_ROOT", "SILVER_BRONZE_TABLE")}, "metaverse", 1)
        for name in control._ENV:  # 빈 값·빠진 값 모두 "기본값 없음"으로(값 검사가 같은 이름으로 실패하는 것과 구별 — SL-5 돌연변이 B3)
            for env in ({**ENV, name: " "}, {k: v for k, v in ENV.items() if k != name}):
                with self.subTest(name=name, present=name in env), self.assertRaisesRegex(ValueError, f"기본값 없음\\): {name}$"):
                    control.job_settings(env, "metaverse", 1)

    def test_bad_values(self):
        for env, domain, generation in (({**ENV, "SILVER_MAX_FILES_PER_TRIGGER": "0"}, "metaverse", 1), ({**ENV, "SILVER_MAX_FILES_PER_TRIGGER": "-1"}, "metaverse", 1),
                                        ({**ENV, "SILVER_LAKE_ROOT": "s3a://a`b"}, "metaverse", 1), ({**ENV, "SILVER_BRONZE_TABLE": "x`"}, "metaverse", 1),
                                        ({**ENV, "SILVER_LAKE_ROOT": "///"}, "metaverse", 1), ({**ENV, "SILVER_BRONZE_TABLE": "s3a://b "}, "metaverse", 1), ({**ENV, "SILVER_SOURCE_DB": "a "}, "metaverse", 1),
                                        *(({**ENV, "SILVER_MAX_BYTES_PER_TRIGGER": bad}, "metaverse", 1) for bad in ("0", "-1", "512M", "512mb", "1.5g", "m", "512 m", "512m ", "0512m", "1t")),
                                        (ENV, "Meta", 1), (ENV, "metaverse", 0), (ENV, "metaverse", "1")):
            with self.subTest(env=env, domain=domain, generation=generation), self.assertRaises(ValueError):
                control.job_settings(env, domain, generation)


    def test_s3_settings(self):  # SL-5 — s3a 경로면 넷 모두(기본값 없음), file:// 면 없음
        settings = control.job_settings(ENV, "metaverse", 1)
        self.assertEqual(control.s3_settings({**ENV, **S3}, (settings.silver, settings.bronze)),
                         {"endpoint": "http://127.0.0.1:9000", "region": "ap-northeast-2", "access_key_file": "/keys/access", "secret_key_file": "/keys/secret"})
        with self.assertRaisesRegex(ValueError, "S3_ENDPOINT, S3_SECRET_KEY_FILE$"):  # 빠진 것을 모두 적는다
            control.s3_settings({**ENV, **S3, "S3_ENDPOINT": "", "S3_SECRET_KEY_FILE": " "}, (settings.silver, settings.bronze))
        for name in control.S3_ENV:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, name):
                    control.s3_settings({k: v for k, v in {**ENV, **S3}.items() if k != name}, (settings.silver, settings.bronze))
                with self.assertRaisesRegex(ValueError, f"앞뒤 공백.*{name}"):
                    control.s3_settings({**ENV, **S3, name: S3[name] + " "}, (settings.silver, settings.bronze))
        local = control.job_settings({**ENV, "SILVER_LAKE_ROOT": "/tmp/lake", "SILVER_BRONZE_TABLE": "/tmp/lake/bronze"}, "metaverse", 1)
        self.assertIsNone(control.s3_settings(ENV, (local.silver, local.bronze)))  # file:// 시험 — S3 설정이 없어도 된다
        for env in ({**ENV, "SILVER_LAKE_ROOT": "/tmp/lake"}, {**ENV, "SILVER_BRONZE_TABLE": "/tmp/bronze"}):  # 한쪽만 s3a 여도 필요
            with self.subTest(env=env), self.assertRaisesRegex(ValueError, "S3_ENDPOINT"):
                local = control.job_settings(env, "metaverse", 1)
                control.s3_settings(env, (local.silver, local.bronze))


class Paths(unittest.TestCase):  # SL-7 §2.4 — 세대 경로 규칙 한 곳(잡 설정과 포인터), 원천별 포인터 표(D6)
    def test_silver_paths(self):
        expected = ("s3a://ads-lake/silver/ads/events/g3", "s3a://ads-lake/silver/ads/errors/g3", "s3a://ads-lake/silver/ads/reconcile/g3",
                    "s3a://ads-lake/silver/ads/quarantine/g3", "s3a://ads-lake/checkpoints/silver/ads/events/g3")
        for root in ("s3a://ads-lake", "s3a://ads-lake/", "s3a://ads-lake//"):
            with self.subTest(root=root):
                paths = control.silver_paths(root, "ads", 3)
                self.assertEqual(tuple(paths), expected)
                self.assertEqual((paths.silver, paths.checkpoint), (expected[0], expected[4]))
                self.assertEqual(control.pointer_path(root, "ads"), "s3a://ads-lake/silver/ads/pointer")
        settings = control.job_settings(ENV, "metaverse", 2)  # 잡 설정도 같은 규칙
        self.assertEqual((settings.silver, settings.errors, settings.reconcile, settings.quarantine, settings.checkpoint),
                         tuple(control.silver_paths("s3a://ads-lake", "metaverse", 2)))
        for domain, generation in (("Meta", 1), ("_pointers", 1), ("a:b", 1), ("ads", 0), ("ads", "1"), ("ads", True), ("ads", 1.0)):
            with self.subTest(domain=domain, generation=generation), self.assertRaises(ValueError):
                control.silver_paths("s3a://ads-lake", domain, generation)
        for domain in ("Meta", "", "a/b", "../ads", None):
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                control.pointer_path("s3a://ads-lake", domain)


def pointer_command(argv, environ=None):
    with contextlib.redirect_stderr(io.StringIO()):  # argparse 사용법 글자
        return control.pointer_command(argv, {"SILVER_LAKE_ROOT": "/lake/"} if environ is None else environ)


class PointerCommand(unittest.TestCase):  # SL-7 §2.5 — 손 도구 인자·설정(Spark 전)
    SET = ["set", "--domain", "metaverse", "--generation", "2", "--by", "운영자", "--reason", "MVR-3a 새 세대"]

    def test_set_and_show(self):
        self.assertEqual(pointer_command(self.SET), control.PointerCommand("set", "metaverse", 2, "운영자", "MVR-3a 새 세대", None, "/lake", None))
        self.assertEqual(pointer_command(self.SET + ["--bronze-changed", "Bronze 경로 바뀜"]).bronze_changed, "Bronze 경로 바뀜")
        self.assertEqual(pointer_command(["show", "--domain", "ads"]), control.PointerCommand("show", "ads", None, None, None, None, "/lake", None))
        s3a = {"SILVER_LAKE_ROOT": "s3a://ads-lake", **S3}
        self.assertEqual(pointer_command(["show", "--domain", "ads"], s3a).s3["endpoint"], S3["S3_ENDPOINT"])

    def test_argument_shape(self):  # argparse 가 끝낸다(rc 2)
        for argv in ([], ["get"], ["set"], ["show"], ["show", "--domain", "ads", "--generation", "1"], [a for a in self.SET if a not in ("--by", "운영자")],
                     [a for a in self.SET if a not in ("--reason", "MVR-3a 새 세대")], self.SET + ["extra"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as stop:
                pointer_command(argv)
            self.assertEqual(stop.exception.code, 2)

    def test_values(self):
        def changed(**values):
            argv = list(self.SET)
            for name, value in values.items():
                flag = "--" + name.replace("_", "-")
                argv = argv + [flag, value] if flag not in argv else [value if i and argv[i - 1] == flag else a for i, a in enumerate(argv)]
            return argv
        bad = ([changed(domain=d) for d in ("Meta", "a b", "_pointers", "")] + [changed(generation=g) for g in ("0", "-1", " 1", "+1", "1_0", "01", "1.0", "x", "")]
               + [changed(by=v) for v in ("", " ")] + [changed(reason=v) for v in ("", "\t")] + [changed(bronze_changed=v) for v in ("", " ")])
        for argv in bad:
            with self.subTest(argv=argv), self.assertRaisesRegex(ValueError, "domain|generation|by|reason|bronze_changed"):
                pointer_command(argv)
        for domain in ("Meta", ""):
            with self.subTest(show=domain), self.assertRaisesRegex(ValueError, "domain"):
                pointer_command(["show", "--domain", domain])

    def test_settings(self):
        for environ in ({}, {"SILVER_LAKE_ROOT": " "}):
            with self.subTest(environ=environ), self.assertRaisesRegex(ValueError, "기본값 없음\\): SILVER_LAKE_ROOT$"):
                pointer_command(["show", "--domain", "ads"], environ)
        for root in ("/a`b", "/lake ", "///"):
            with self.subTest(root=root), self.assertRaisesRegex(ValueError, "쓸 수 없는 경로"):
                pointer_command(["show", "--domain", "ads"], {"SILVER_LAKE_ROOT": root})
        with self.assertRaisesRegex(ValueError, "S3_ENDPOINT, S3_REGION, S3_ACCESS_KEY_FILE, S3_SECRET_KEY_FILE$"):
            pointer_command(self.SET, {"SILVER_LAKE_ROOT": "s3a://ads-lake"})

    def test_pointer_change(self):  # set_generation 이 Spark 전에 같은 검사를 한다
        control.pointer_change("ads", 1, "운영자", "이유", None)
        for args in (("ads", 1, None, "이유", None), ("ads", 1, "운영자", 3, None), ("ads", 1, "운영자", "이유", 1), ("ads", 0, "운영자", "이유", None),
                     ("ADS", 1, "운영자", "이유", None)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                control.pointer_change(*args)


def offset_file(**changes):
    source = {"sourceVersion": 1, "reservoirId": "b70da633-44a9-4b0e-91db-ed12955f81a9", "reservoirVersion": 34, "index": -1, "isStartingVersion": False}
    return 'v1\n{"batchWatermarkMs":0,"batchTimestampMs":1794153707073,"conf":{}}\n' + json.dumps({**source, **changes}, separators=(",", ":"))


class ProcessedBronzeVersion(unittest.TestCase):  # SL-6 D1 — Silver 가 처리를 끝낸 Bronze 판(offset 모양은 계획 35 §1.2 실측)
    def test_finished_run(self):
        self.assertEqual(control.processed_bronze_version([0, 1, 2], [0, 1, 2], offset_file()), 33)
        self.assertEqual(control.processed_bronze_version([0], [0], offset_file(reservoirVersion=1)), 0)
        self.assertIsNone(control.processed_bronze_version([], [], ""))  # 끝난 배치 없음 — 맞출 것이 없다

    def test_refused(self):
        cases = {
            "commits 없는 배치": ([0], [], ""),
            "처리 중인 배치": ([0, 1, 2, 3], [0, 1, 2], offset_file()),
            "판 안의 자리": ([0], [0], offset_file(index=0)),
            "시작 스냅숏 중": ([0], [0], offset_file(reservoirVersion=33, index=4, isStartingVersion=True)),
            "시작 스냅숏 표시만": ([0], [0], offset_file(isStartingVersion=True)),
            "sourceVersion 2": ([0], [0], offset_file(sourceVersion=2)),
            "sourceVersion true": ([0], [0], offset_file(sourceVersion=True)),
            "판 문자열": ([0], [0], offset_file(reservoirVersion="34")),
            "판 0": ([0], [0], offset_file(reservoirVersion=0)),
            "index 문자열": ([0], [0], offset_file(index="-1")),
            "키 더 있음": ([0], [0], offset_file(extra=1)),
            "원천 둘": ([0], [0], offset_file() + "\n" + offset_file().split("\n")[2]),
            "v2": ([0], [0], "v2" + offset_file()[2:]),
            "JSON 아님": ([0], [0], "v1\n{}\nnot json"),
            "빈 파일": ([0], [0], ""),
        }
        for name, (offsets, commits, text) in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                control.processed_bronze_version(offsets, commits, text)


class SilverRelease(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.write("pipeline/silver/framework.py", "x = 1\n")
        self.write("pipeline/silver/adapters/metaverse.py", "y = 2\n")
        self.write("pipeline/common/hop_rules.py", "z = 3\n")

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_format_and_inputs(self):
        before = control.silver_release(self.root)
        self.assertRegex(before, r"^silver@[0-9a-f]{12}$")
        self.write("pipeline/silver/acknowledged/metaverse.json", "{}")
        self.write("pipeline/silver/quarantine/metaverse.json", "{}")
        self.write("pipeline/silver/__pycache__/framework.cpython-314.pyc", "junk")
        self.write("pipeline/silver/spark-submit-silver.sh", "echo")
        self.write("pipeline/common/ndjson.py", "w = 4\n")
        self.assertEqual(control.silver_release(self.root), before)  # 넘김·격리 목록·바이트코드·제출 스크립트·다른 공용 파일은 넣지 않는다
        for rel, text in (("pipeline/silver/adapters/metaverse.py", "y = 3\n"), ("pipeline/common/hop_rules.py", "z = 4\n"), ("pipeline/silver/pointers.py", "")):
            with self.subTest(rel=rel):
                self.write(rel, text)
                self.assertNotEqual(control.silver_release(self.root), before)
                before = control.silver_release(self.root)

    def test_moving_content_or_renaming_changes_release(self):
        self.write("pipeline/silver/a.py", "x\n")
        self.write("pipeline/silver/b.py", "")
        one = control.silver_release(self.root)
        (self.root / "pipeline/silver/b.py").rename(self.root / "pipeline/silver/c.py")  # 이름만 바뀜 — 내용·정렬 위치(adapters 뒤, framework 앞)는 그대로
        self.assertNotEqual(control.silver_release(self.root), one)
        (self.root / "pipeline/silver/c.py").rename(self.root / "pipeline/silver/b.py")
        self.assertEqual(control.silver_release(self.root), one)
        self.write("pipeline/silver/a.py", "")
        self.write("pipeline/silver/b.py", "x\n")  # 같은 바이트를 다른 파일로 옮김
        self.assertNotEqual(control.silver_release(self.root), one)

    def test_missing_hop_rules_copy_fails(self):
        (self.root / "pipeline/common/hop_rules.py").unlink()
        with self.assertRaises(FileNotFoundError):
            control.silver_release(self.root)

    def test_no_silver_code_under_the_root_fails(self):  # 뿌리 설정을 잘못 두면 hop_rules 하나만 해시한 값이 나오지 않게
        for rel in ("pipeline/silver/framework.py", "pipeline/silver/adapters/metaverse.py"):
            (self.root / rel).unlink()
        self.write("pipeline/silver/acknowledged/metaverse.json", "{}")
        with self.assertRaisesRegex(FileNotFoundError, "pipeline/silver"):
            control.silver_release(self.root)

    def test_default_root_is_this_repository(self):
        repo = Path(control.__file__).resolve().parents[2]
        self.assertTrue((repo / "pipeline" / "silver" / "control.py").is_file())
        if (repo / "pipeline/common/hop_rules.py").is_file():  # P2-1 사본이 있을 때
            self.assertEqual(control.silver_release(), control.silver_release(repo))
        else:
            with self.assertRaisesRegex(FileNotFoundError, "hop_rules.py"):
                control.silver_release()


if __name__ == "__main__":
    unittest.main()
