import hashlib
import json
from functools import partial
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from events import ndjson

PREFIX = "manifests/hop_pipeline/"  # 파이프라인 잡이 게시하는 경로(설계 01 D14). 이 경로만 읽는 키를 쓴다(S5)
MANIFEST_LIMIT = 1 << 20  # 포인터·세대 사본 한 개(수 KB)의 상한 — 믿지 않는 입력이 메모리를 채우지 못하게
DATA_LIMIT = 16 << 20  # 데이터 파일 한 개의 상한(시간 창 48개 × 이벤트 종류 8 = 384줄, 수백 KB)


class Command(BaseCommand):
    help = (
        "파이프라인 쪽이 S3 에 게시한 구간 manifest(hop_pipeline)를 받아 로컬 폴더에 같은 모양으로 둔다(설계 01 D14, 계획 P1-8) — 그 폴더를 verify_hops --pipeline 이 읽는다. "
        "포인터 → 가리키는 불변 데이터 파일 → SHA-256·행 수·줄 형식, 세대 사슬(previous_sha256)을 처음까지 확인하고, 모두 맞을 때만 불변 파일을 먼저·포인터를 마지막에 쓴다. "
        "하나라도 어긋나면(위조·손상·경로 밖 파일·사슬 끊김) 아무것도 바꾸지 않고 실패한다. 받은 세대 사본은 폴더에 쌓아 다음에는 새 세대만 받는다. "
        "폴더에 이미 받은 포인터가 있으면 그것이 고정점이다 — 새 사슬이 그 세대를 지나지 않으면(옛 세대로 되돌림, 사슬을 처음부터 새로 시작) 받지 않는다. "
        "정당하게 새로 시작한 사슬이면 사람이 사건을 확인한 뒤 폴더를 비우고 다시 받는다(설계 01 D14·S5, 고정점 결정 (나)). "
        "접속 값은 환경변수 HOP_S3_*(키는 인자로 받지 않는다)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True, help="받은 manifest 를 둘 로컬 폴더(실행마다 같은 폴더 — 세대 사본이 쌓인다)")
        parser.add_argument("--name", default="hop_pipeline", help="포인터 이름(<이름>.manifest.json)")

    def handle(self, *args, **options):
        name, out = options["name"], Path(options["out"])
        if not ndjson.NAME.fullmatch(name):
            raise CommandError(f"--name 은 영문·숫자·밑줄·하이픈 1~64자여야 합니다: {name!r}")
        config = settings.HOP_PIPELINE_S3
        if not config:
            raise CommandError("파이프라인 manifest 저장소 설정이 없습니다(환경변수 HOP_S3_BUCKET 등).")
        get = partial(get_object, client(config), config["BUCKET"])
        with ndjson.run_lock(out):
            local_pointer = ndjson.manifest_path(out, name)
            anchor = hashlib.sha256(local_pointer.read_bytes()).hexdigest() if local_pointer.exists() else None  # 직전에 받은 포인터(없으면 첫 받기)
            pointer = get(f"{name}.manifest.json", MANIFEST_LIMIT)
            try:
                manifest = json.loads(pointer)
            except ValueError as error:
                raise CommandError(f"포인터를 읽을 수 없다: {error}")
            if not isinstance(manifest, dict):
                raise CommandError("포인터가 JSON 객체가 아니다")
            data = {}

            def read_data(file):  # `ndjson.check` 가 파일 이름 형식(경로 구분자 없음)을 확인한 뒤에만 부른다
                if not file.startswith(name + "."):
                    return None  # 다른 이름의 데이터 파일은 묻지 않는다(check 가 그 문제를 적는다)
                data[file] = get(file, DATA_LIMIT)
                return data[file]

            digest = hashlib.sha256(pointer).hexdigest()
            fetched, chain = {}, {digest}  # chain: 사슬이 지난 세대의 SHA-256(check_chain 이 사본마다 해시를 확인한다)

            def read_copy(copy_name):  # 이미 받아 둔 세대 사본은 로컬에서 읽는다
                local = out / copy_name
                if local.exists():
                    body = local.read_bytes()
                else:
                    body = fetched[copy_name] = get(copy_name, MANIFEST_LIMIT)
                chain.add(hashlib.sha256(body).hexdigest())
                return body

            problems = ndjson.check(manifest, name, read_data)
            if not problems:
                problems = [f"세대 사슬 — {p}" for p in ndjson.check_chain(pointer, name, read_copy)]
            if not problems and anchor is not None and anchor not in chain:
                problems = [f"세대 사슬이 직전에 받은 포인터(고정점 {anchor[:16]})를 지나지 않는다 — 옛 세대로 되돌렸거나 사슬을 새로 시작했다"
                            "(정당한 새 시작이면 사건을 확인한 뒤 --out 폴더를 비우고 다시 받는다)"]
            if problems:
                raise CommandError("받은 manifest 가 맞지 않아 아무것도 바꾸지 않았다: " + "; ".join(problems))
            fetched[ndjson.generation_copy_name(name, digest)] = pointer  # 이 세대의 사본도 둔다 — 다음 받기는 이것을 로컬에서 읽고 새 세대만 받는다(게시하는 쪽 `ndjson.write(chain=True)` 와 같은 모양)
            for file, body in [*data.items(), *fetched.items()]:  # 불변 파일(이름에 해시)이 먼저, 포인터가 마지막 — 포인터는 언제나 온전한 파일을 가리킨다
                if not (out / file).exists() or (out / file).read_bytes() != body:
                    ndjson.publish_bytes(out / file, body)
            ndjson.publish_bytes(ndjson.manifest_path(out, name), pointer)
        self.stdout.write(f"{name}: rows={manifest['rows']} file={manifest['file']} manifest_sha256={digest} 새로 받은 세대 사본 {len(fetched) - 1}개")


def client(config):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3", endpoint_url=config["ENDPOINT"], region_name=config["REGION"],
        aws_access_key_id=config["ACCESS_KEY_ID"], aws_secret_access_key=config["SECRET_ACCESS_KEY"],
        config=Config(connect_timeout=5, read_timeout=30, retries={"total_max_attempts": 3, "mode": "standard"}, s3={"addressing_style": "path"}),  # RustFS 는 경로 방식
    )


def get_object(s3, bucket, file, limit):
    """`PREFIX` 아래의 정해진 키 하나를 받는다(목록 조회 없음 — 읽기 전용 키에 ListBucket 이 없다). `file` 은 호출한 쪽이 형식을 확인한 파일 이름이다."""
    from botocore.exceptions import BotoCoreError, ClientError

    key = PREFIX + file
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read(limit + 1)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "AccessDenied", "403"):  # 키에 ListBucket 이 없으면 없는 키도 403 이다
            raise CommandError(f"S3 에 없거나 읽을 권한이 없다: {key} ({code})")
        raise CommandError(f"S3 받기 실패: {key} ({code or type(error).__name__})")
    except BotoCoreError as error:  # 접속 실패·시간 초과 등
        raise CommandError(f"S3 받기 실패: {key} ({type(error).__name__}: {error})")
    if len(body) > limit:
        raise CommandError(f"S3 파일이 상한({limit}바이트)보다 크다: {key}")
    return body
