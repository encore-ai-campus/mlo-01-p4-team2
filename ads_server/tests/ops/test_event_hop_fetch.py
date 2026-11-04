"""파이프라인 manifest 받기(`fetch_hop_manifest`, 파이프라인 계획 P1-8 — 설계 01 D14·S5): 포인터 → 불변 데이터 파일 → 체크섬, 세대 사슬, 로컬 고정점.
위조·손상·경로 밖 파일·끊긴 사슬·되돌림은 거부하고, 거부하면 받은 폴더를 바꾸지 않는다.
S3 는 `get_object` 만 있는 가짜다. 원격 쪽 파일은 게시 규칙(`ndjson.write(chain=True)`)으로 만든다 — 파이프라인 잡(P2-1)이 같은 모양으로 게시한다."""
import io
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from botocore.exceptions import ClientError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings, tag

from events import hop_rules, hopcheck, ndjson
from events.management.commands import fetch_hop_manifest
from tests.support.cases import Cases

PREFIX = "manifests/hop_pipeline/"
CONFIG = {"ENDPOINT": "http://127.0.0.1:1", "BUCKET": "ads-lake", "REGION": "us-east-1", "ACCESS_KEY_ID": "test", "SECRET_ACCESS_KEY": "test"}
T = datetime(2026, 10, 7, 13, 0, tzinfo=timezone.utc)


class FakeS3:
    """정해진 키를 GET 만 한다(읽기 전용 키에 ListBucket 이 없다). 받은 키를 기록한다."""

    def __init__(self):
        self.objects, self.gets, self.error = {}, [], None

    def get_object(self, Bucket, Key):
        self.gets.append(Key)
        if self.error:
            raise ClientError({"Error": {"Code": self.error}}, "GetObject")
        if Bucket != CONFIG["BUCKET"] or Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


@override_settings(HOP_PIPELINE_S3=CONFIG)
@tag("security")
class FetchTests(Cases, SimpleTestCase):
    def setUp(self):
        self.remote, self.out = (Path(tempfile.mkdtemp()) for _ in range(2))
        self.addCleanup(shutil.rmtree, self.remote, True)
        self.addCleanup(shutil.rmtree, self.out, True)
        self.s3 = FakeS3()
        self.enterContext(mock.patch.object(fetch_hop_manifest, "client", lambda config: self.s3))

    def publish(self, windows=1):
        """계약 모양(DRD §5.6 [P1-3])의 hop_pipeline manifest 한 세대를 원격(가짜 S3)에 게시한다 — 포인터·데이터·세대 사본을 모두 PREFIX 바로 아래에."""
        lines = [{"window_start": hop_rules.format_time(T.replace(hour=h)), "window_end": hop_rules.format_time(T.replace(hour=h + 1)), "event_name": "ad.decision",
                  "count": 0, "ids_sha256": "0" * 64, "content_sha256": "0" * 64,
                  "duplicates": 0, "conflicts": 0, "not_insert": 0} for h in range(windows)]
        checkpoint = {"window": "utc-hour", "open_windows": [], "rule_sha256": hop_rules.rule_sha256(), "not_insert_unplaced": 0, "foreign_source_rows": 0,
                      **{field: [] for field in hop_rules.EXAMPLE_FIELDS}}
        ndjson.write(self.remote, "hop_pipeline", lines, "hop_pipeline", 1, checkpoint=checkpoint, now=T, chain=True)
        self.s3.objects = {PREFIX + p.name: p.read_bytes() for p in self.remote.iterdir() if not p.name.startswith(".")}
        return self.s3.objects[PREFIX + "hop_pipeline.manifest.json"]

    def fetch(self):
        out = io.StringIO()
        self.s3.gets.clear()
        try:
            call_command("fetch_hop_manifest", "--out", str(self.out), stdout=out)
            return True, out.getvalue()
        except CommandError as error:
            return False, str(error)

    def local(self):
        return {p.name: p.read_bytes() for p in self.out.iterdir() if not p.name.startswith(".")}

    def put(self, file, body):
        self.s3.objects[PREFIX + file] = body if isinstance(body, bytes) else (json.dumps(body, sort_keys=True, indent=2) + "\n").encode()

    def pointer(self):
        return json.loads(self.s3.objects[PREFIX + "hop_pipeline.manifest.json"])

    def refused(self, needle):
        before = self.local()
        ok, text = self.fetch()
        self.assertFalse(ok, text)
        self.assertIn(needle, text)
        self.assertEqual(self.local(), before)  # 거부하면 받은 폴더는 그대로다
        return text

    def case_the_published_manifest_is_mirrored_and_verify_hops_can_read_it(self):
        remote_pointer = self.publish()
        ok, text = self.fetch()
        self.assertTrue(ok, text)
        local = ndjson.manifest_path(self.out, "hop_pipeline")
        self.assertEqual(local.read_bytes(), remote_pointer)  # 같은 바이트 — 판정 감사의 inputs.pipeline 이 S3 의 포인터와 같은 SHA-256 이다
        self.assertEqual((ndjson.verify(local), ndjson.verify_chain(local)), ([], []))
        hopcheck.load_hop(local, "pipeline", expected_kind="hop_pipeline")  # verify_hops --pipeline 이 그대로 읽는다
        self.assertIn(f"manifest_sha256={ndjson.manifest_sha256(local)} 새로 받은 세대 사본 0개", text)
        self.assertEqual(self.s3.gets, [PREFIX + "hop_pipeline.manifest.json", PREFIX + self.pointer()["file"]])  # 첫 세대: 포인터와 데이터뿐
        self.publish(windows=2)
        self.publish(windows=3)
        ok, text = self.fetch()
        self.assertTrue(ok, text)
        self.assertIn("새로 받은 세대 사본 1개", text)  # 둘째 세대 사본만 새로 받는다 — 첫 세대 사본은 지난번에 로컬에 두었다
        self.assertEqual(len(self.s3.gets), 3)
        self.assertEqual((ndjson.verify(local), ndjson.verify_chain(local)), ([], []))
        self.assertEqual(local.read_bytes(), self.s3.objects[PREFIX + "hop_pipeline.manifest.json"])
        ok, text = self.fetch()  # 새 세대가 없어도 다시 확인하고 그대로 둔다
        self.assertTrue(ok, text)
        self.assertIn("새로 받은 세대 사본 0개", text)

    def case_a_corrupted_or_forged_data_file_is_refused(self):
        self.publish()
        ok, _ = self.fetch()
        self.assertTrue(ok)
        self.publish(windows=2)
        file = self.pointer()["file"]
        self.put(file, self.s3.objects[PREFIX + file].replace(b'"count":0', b'"count":9', 1))  # 같은 이름, 다른 내용
        self.refused("SHA-256 이 manifest 와 다르다")
        self.publish(windows=2)
        forged = self.pointer() | {"sha256": "f" * 64}  # 포인터만 바꿈
        self.put("hop_pipeline.manifest.json", forged)
        self.refused("SHA-256 이 manifest 와 다르다")

    def case_a_file_outside_the_prefix_is_never_requested(self):
        self.publish()
        good = self.pointer()
        for file in ("../hop_pipeline.0123456789abcdef.ndjson", "a/hop_pipeline.0123456789abcdef.ndjson", "/etc/passwd", "hop_pipeline.ndjson"):
            with self.subTest(file=file):
                self.put("hop_pipeline.manifest.json", good | {"file": file})
                self.refused("올바른 데이터 파일 이름이 아니다")
                self.assertEqual(self.s3.gets, [PREFIX + "hop_pipeline.manifest.json"])  # 포인터 말고는 묻지 않았다
        self.put("hop_pipeline.manifest.json", good | {"file": "hop_outbox." + good["file"].split(".", 1)[1]})  # 다른 이름의 데이터 파일
        self.refused("다른 이름의 데이터 파일")
        self.assertEqual(self.s3.gets, [PREFIX + "hop_pipeline.manifest.json"])  # 그 파일도 묻지 않는다

    def case_a_broken_chain_or_a_rollback_is_refused(self):
        self.publish()
        self.publish(windows=2)
        del self.s3.objects[PREFIX + ndjson.generation_copy_name("hop_pipeline", self.pointer()["previous_sha256"])]  # 첫 받기 전이라 로컬에도 없다
        self.refused("S3 에 없거나 읽을 권한이 없다")
        self.publish(windows=3)
        previous = ndjson.generation_copy_name("hop_pipeline", self.pointer()["previous_sha256"])
        self.put(previous, self.s3.objects[PREFIX + previous].replace(b'"rows": 2', b'"rows": 7'))  # 직전 세대 사본이 바뀜
        self.refused("세대 사슬 — 직전 세대 manifest 가 바뀌었다")
        self.remote, self.s3.objects = Path(tempfile.mkdtemp()), {}
        self.addCleanup(shutil.rmtree, self.remote, True)
        self.publish()
        ok, text = self.fetch()
        self.assertTrue(ok, text)
        anchor = ndjson.manifest_path(self.out, "hop_pipeline").read_bytes()
        self.publish(windows=2)
        ok, text = self.fetch()
        self.assertTrue(ok, text)
        self.put("hop_pipeline.manifest.json", anchor)  # 옛 세대로 되돌림(그 사본·데이터는 S3 에 그대로 있다)
        self.refused("직전에 받은 포인터(고정점")
        self.remote, self.s3.objects = Path(tempfile.mkdtemp()), {}  # 사슬을 처음부터 새로 시작
        self.addCleanup(shutil.rmtree, self.remote, True)
        self.publish(windows=4)
        self.refused("사슬을 새로 시작했다")
        shutil.rmtree(self.out)  # 사람이 사건을 확인하고 폴더를 비우면 받는다
        ok, text = self.fetch()
        self.assertTrue(ok, text)

    def case_bad_pointers_and_storage_errors_are_refused(self):
        self.refused("S3 에 없거나 읽을 권한이 없다: manifests/hop_pipeline/hop_pipeline.manifest.json (NoSuchKey)")
        self.publish()
        for body, needle in ((b"{", "포인터를 읽을 수 없다"), (b"[]", "포인터가 JSON 객체가 아니다"), (b" " * (fetch_hop_manifest.MANIFEST_LIMIT + 1), "상한")):
            with self.subTest(needle=needle):
                self.put("hop_pipeline.manifest.json", body)
                self.refused(needle)
        self.publish()
        for code, needle in (("AccessDenied", "없거나 읽을 권한이 없다"), ("403", "없거나 읽을 권한이 없다"), ("InternalError", "S3 받기 실패")):
            with self.subTest(code=code):
                self.s3.error = code
                self.refused(needle)
        self.s3.error = None
        with override_settings(HOP_PIPELINE_S3=None):
            self.refused("저장소 설정이 없습니다")
        with self.assertRaises(CommandError):
            call_command("fetch_hop_manifest", "--out", str(self.out), "--name", "../x", stdout=io.StringIO())

    def test_fetch(self):
        self.run_cases()


@tag("security")
class RealClientTests(SimpleTestCase):
    def test_the_client_uses_path_style_addressing_and_bounded_retries(self):
        """RustFS 는 경로 방식 주소만 받는다. 네트워크 없이 설정만 본다."""
        s3 = fetch_hop_manifest.client(CONFIG)
        self.assertEqual((s3.meta.config.s3["addressing_style"], s3.meta.config.retries["total_max_attempts"], s3.meta.endpoint_url), ("path", 3, CONFIG["ENDPOINT"]))
