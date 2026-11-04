"""Bronze manifest 잡(설계 01 D14·S2, 파이프라인 계획 P2-1): Bronze Delta 를 한 판으로 읽어 공용 요약 규칙으로 창마다 요약하고, S3 에 불변 파일 + 포인터로 게시한다.

광고 서버의 받기(`fetch_hop_manifest`)가 이 게시물을 받아 `verify_hops --pipeline` 으로 outbox 창과 대조한다.
- 요약은 드라이버에서 공용 규칙(`hop_rules.summarize_changes`, 광고 서버 원본의 바이트 사본) 그대로 한다(결정 1 (가)). 규칙이 한 곳이라 `rule_sha256` 이 다 덮는다.
  드라이버 Python 메모리는 읽은 범위의 고유 이벤트 수에 비례한다(JVM 힙 밖).
- manifest 바이트는 `ndjson.write(chain=True)`(광고 서버 원본의 바이트 사본)가 만든다(결정 2 (가)). 임시 폴더에 S3 의 현재 포인터를 받아 두고 쓴 뒤,
  새 파일을 데이터 → 세대 사본 → **포인터 마지막** 순서로 올린다. 데이터·사본 이름은 내용 해시라 덮어쓰지 않는다.
- 포인터를 쓰는 쪽은 하나라는 전제다(로컬 launchd 잠금, 운영 ScheduledSparkApplication `concurrencyPolicy: Forbid`).
  S3A `create(overwrite=false)` 는 확인 후 쓰기(HEAD·LIST 뒤 PUT)이고 원자적 조건부 쓰기가 아니다(hadoop-aws 3.4.2 확인 — Step 기록).
- S3 포인터가 **없을 때(404)만** 새 사슬을 시작한다. 권한 없음·시간 초과 같은 다른 오류는 실패다(아무것도 올리지 않는다).

설정(이 장치에서만 맞는 값은 여기에 두지 않는다 — 제출 스크립트·plist·운영 매니페스트가 넘긴다):
  HOP_BRONZE_TABLE     읽을 Bronze Delta 표(s3a://…)
  HOP_MANIFEST_URI     게시 접두사(s3a://…/, '/' 로 끝남). 운영 manifests/hop_pipeline/, 시험은 다른 접두사
  HOP_SOURCE_DB        원천 DB 이름(Debezium source.db — 다른 출처 행을 가린다)
  HOP_GRACE_SECONDS    창 닫힘 여유(광고 서버 EVENTS_HOP_GRACE_SECONDS 와 같게)
  HOP_MAX_PARTITION_BYTES  (선택, 기본 16m) 읽기 파티션 크기 상한 — toLocalIterator 는 파티션 하나를 executor 메모리에 통째로 만든다.
                       Bronze 는 1분마다 작은 파일을 쌓아 기본 128MB 로 묶으면 executor 1G 에서 넘쳤다(e2e 246만 행, Step 기록)
  S3 접속·자격 증명은 Bronze 와 같은 s3a_session(S3_ENDPOINT·S3_REGION·S3_ACCESS_KEY_FILE·S3_SECRET_KEY_FILE)이 읽는다.
인자: --window hour|day(이름 hop_pipeline_hour 48시간 / hop_pipeline_day 8일), --since·--as-of(다시 요약·시험용, 기본은 지금 기준).
"""
import argparse
import hashlib
import json
import os
import resource
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import hop_rules
import ndjson

SPANS = {"hour": timedelta(hours=48), "day": timedelta(days=8)}  # 매시 48시간·매일 8일(설계 01 D14)
NAMES = {"hour": "hop_pipeline_hour", "day": "hop_pipeline_day"}
ENV = ("HOP_BRONZE_TABLE", "HOP_MANIFEST_URI", "HOP_SOURCE_DB", "HOP_GRACE_SECONDS")


class JobError(Exception):
    pass


def settings(environ=os.environ):
    """필수 설정을 읽는다. 기본값이 없다 — 빠지면 실패한다."""
    missing = [name for name in ENV if not environ.get(name)]
    if missing:
        raise JobError(f"설정이 없다: {', '.join(missing)}")
    uri = environ["HOP_MANIFEST_URI"]
    if not uri.startswith("s3a://") or not uri.endswith("/") or "/../" in uri or "/./" in uri:
        raise JobError(f"HOP_MANIFEST_URI 는 '/' 로 끝나는 s3a:// 접두사여야 한다: {uri!r}")
    try:
        grace = int(environ["HOP_GRACE_SECONDS"])
    except ValueError:
        raise JobError(f"HOP_GRACE_SECONDS 는 정수여야 한다: {environ['HOP_GRACE_SECONDS']!r}")
    return {"table": environ["HOP_BRONZE_TABLE"], "uri": uri, "db": environ["HOP_SOURCE_DB"], "grace": grace}


def plan(window, as_of=None, since=None):
    """창 크기·이름·기준 시각·범위 시작을 정한다. since 는 그 창 크기의 UTC 경계(하루 창은 자정)여야 한다 — 광고 서버가 같은 since 로 outbox 를 요약한다."""
    size = hop_rules.WINDOWS[window]
    now = hop_rules.parse_time(as_of) if as_of else datetime.now(timezone.utc)
    if since is None:
        start = hop_rules.window_start(now, size) - SPANS[window]
    else:
        start = hop_rules.parse_time(since)
        if hop_rules.format_time(start) != since or hop_rules.window_start(start, size) != start:
            raise JobError(f"--since 가 {window} 창의 UTC 경계가 아니다: {since!r}")
    return {"window": window, "size": size, "label": hop_rules.window_label(window), "name": NAMES[window], "now": now, "since": start,
            "read_from": start.date() - timedelta(days=1)}  # 적재 날짜는 하루 여유(Spark 와 원천 서버의 시계가 달라도 빠지지 않게)


def summarize(rows, plan_, config):
    """Bronze 행 (key, value, partition, offset) 을 공용 규칙으로 요약한다. 반환: (줄, checkpoint, 읽은 행 수)."""
    counter = {"rows": 0}

    def counted():
        for row in rows:
            counter["rows"] += 1
            yield row

    lines, open_windows, extra = hop_rules.summarize_changes(counted(), config["db"], plan_["now"], config["grace"], plan_["since"], plan_["size"])
    checkpoint = {"scope": "bronze-delta-snapshot", "hop": "hop_pipeline", "window": plan_["label"], "grace_seconds": config["grace"],
                  "started_at": plan_["now"].isoformat(), "since": hop_rules.format_time(plan_["since"]), "rule_sha256": hop_rules.rule_sha256(),
                  "open_windows": [hop_rules.format_time(w) for w in open_windows], "scanned_rows": counter["rows"],
                  "read_from_ingest_date": plan_["read_from"].isoformat(), **extra}
    return lines, checkpoint, counter["rows"]


def publish(store, name, lines, checkpoint, now=None):
    """`ndjson.write(chain=True)` 로 임시 폴더에 쓰고, 새 파일을 데이터 → 세대 사본 → 포인터 순서로 `store` 에 올린다. 반환: (manifest, 포인터 SHA-256)."""
    pointer = f"{name}.manifest.json"
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        if store.exists(pointer):  # 없을 때(404)만 새 사슬 — 다른 오류는 store 가 예외로 올린다
            (directory / pointer).write_bytes(store.read(pointer))
        manifest = ndjson.write(directory, name, lines, "hop_pipeline", hop_rules.HOP_SCHEMA_VERSION, checkpoint=checkpoint, now=now, chain=True)
        files = sorted(p.name for p in directory.iterdir() if not p.name.startswith(".") and p.name != pointer)
        for file in sorted(files, key=lambda f: not f.endswith(".ndjson")):  # 데이터 파일 먼저, 그다음 세대 사본
            if not store.exists(file):
                store.write(file, (directory / file).read_bytes(), overwrite=False)
        body = (directory / pointer).read_bytes()
        store.write(pointer, body, overwrite=True)
    return manifest, hashlib.sha256(body).hexdigest()


class HadoopStore:
    """Spark 세션의 Hadoop 파일시스템(s3a — Bronze 와 같은 자격 증명·설정)으로 접두사 아래 파일을 읽고 쓴다. 없는 파일은 exists() 가 False(404)이고,
    다른 오류(권한 없음·시간 초과)는 예외로 올라온다."""

    def __init__(self, spark, uri):
        self.jvm = spark._jvm
        self.base = self.jvm.org.apache.hadoop.fs.Path(uri)
        self.fs = self.base.getFileSystem(spark._jsc.hadoopConfiguration())

    def path(self, name):
        return self.jvm.org.apache.hadoop.fs.Path(self.base, name)

    def exists(self, name):
        return bool(self.fs.exists(self.path(name)))

    def read(self, name):
        stream = self.fs.open(self.path(name))
        try:
            return bytes(self.jvm.org.apache.commons.io.IOUtils.toByteArray(stream))
        finally:
            stream.close()

    def write(self, name, body, overwrite):
        stream = self.fs.create(self.path(name), overwrite)  # overwrite=False 는 있으면 FileAlreadyExistsException(확인 후 쓰기)
        try:
            stream.write(bytearray(body))
        finally:
            stream.close()  # S3A 는 닫을 때 올린다


def peak_rss_bytes():
    """이 드라이버 Python 프로세스의 최대 RSS(JVM 은 다른 프로세스라 들어가지 않는다). macOS 는 바이트, Linux 는 KiB 단위로 준다."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bronze manifest 잡(P2-1)")
    parser.add_argument("--window", choices=sorted(SPANS), required=True)
    parser.add_argument("--since", help="범위 시작(UTC 경계, YYYY-MM-DDTHH:00:00Z) — 기본은 지금 창에서 48시간(시간)·8일(하루) 앞")
    parser.add_argument("--as-of", help="기준 시각(열린 창 판단, YYYY-MM-DDTHH:MM:SSZ) — 기본은 지금")
    args = parser.parse_args(argv)
    started = time.monotonic()
    config, plan_ = settings(), plan(args.window, args.as_of, args.since)

    from pyspark.sql import functions as F

    from bronze_ads_outbox import s3a_session

    spark = s3a_session(f"hop_manifest_{args.window}")
    spark.sparkContext.setLogLevel("WARN")
    spark.conf.set("spark.sql.files.maxPartitionBytes", os.environ.get("HOP_MAX_PARTITION_BYTES", "16m"))
    version = spark.sql(f"DESCRIBE HISTORY delta.`{config['table']}` LIMIT 1").collect()[0]["version"]  # 한 판으로 읽는다
    frame = (spark.read.format("delta").option("versionAsOf", version).load(config["table"])
             .filter(F.col("ingest_date") >= F.lit(plan_["read_from"]))
             .select(F.col("key_binary").cast("string").alias("key"), F.col("value_binary").cast("string").alias("value"), "partition", "offset"))
    rows = ((r["key"], r["value"], r["partition"], r["offset"]) for r in frame.toLocalIterator())  # 드라이버로 한꺼번에 모으지 않고 파티션 단위로 흘린다
    lines, checkpoint, scanned = summarize(rows, plan_, config)
    checkpoint["delta_version"] = version
    manifest, digest = publish(HadoopStore(spark, config["uri"]), plan_["name"], lines, checkpoint)
    totals = {field: sum(line[field] for line in lines) for field in ("count", *hop_rules.PIPELINE_COUNTS)}
    print("HOP_MANIFEST " + json.dumps({
        "name": plan_["name"], "uri": config["uri"], "manifest_sha256": digest, "previous_sha256": manifest.get("previous_sha256"), "file": manifest["file"],
        "lines": len(lines), "since": checkpoint["since"], "as_of": checkpoint["started_at"], "delta_version": version, "read_from_ingest_date": checkpoint["read_from_ingest_date"],
        "scanned_rows": scanned, "distinct_events": totals["count"], **{f: totals[f] for f in hop_rules.PIPELINE_COUNTS},
        "not_insert_unplaced": checkpoint["not_insert_unplaced"], "foreign_source_rows": checkpoint["foreign_source_rows"], "open_windows": len(checkpoint["open_windows"]),
        "seconds": round(time.monotonic() - started, 1), "driver_python_peak_rss_bytes": peak_rss_bytes(),
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
