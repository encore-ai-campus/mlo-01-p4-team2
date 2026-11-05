"""C4 대사: outbox ↔ Bronze 구간 manifest(DRD §5.6 계약). 규칙은 광고 서버 공용 요약 규칙 `events/hop_rules.py` 그대로 쓴다(P2-1 Bronze manifest 잡이 생기기 전 J4 판정용).

사용: c4_reconcile.py <bronze_dump.ndjson> <out_dir> [--as-of ISO] [--window hour|day] [--since ISO]
- --since: 이 시각(UTC 정시) 이후의 창만 양쪽에서 요약한다(덤프도 같은 --since 로 만든다). 덤프는 줄 단위로 읽는다.
- hop_outbox: 광고 서버 outbox_streams(한 읽기 스냅샷). hop_pipeline: 덤프 행(key·value·partition·offset)을 `summarize_changes` 로 요약한다 —
  op c·r 를 같은 문서로 고유 event_id 로 세고, 같은 내용의 남는 행은 duplicates, after 6열이 다른 사본이 있으면 그 event_id 는 conflicts(건수·해시에서 뺌),
  c·r 가 아닌 행은 not_insert, 놓을 수 없는 행(DELETE·tombstone·TRUNCATE)·다른 출처 행은 checkpoint 에 싣고 0 보다 크면 대조가 실패한다.
- 대조는 hopcheck.compare(outbox, pipeline, use_unsent=False) — outbox 의 미전송을 지연 근거로 쓰지 않는다(CDC 는 sent_at 을 채우지 않음).
"""
import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[2] / "ads_server"
CWD = Path.cwd()  # 인자의 상대 경로는 실행한 폴더 기준(아래 chdir 전에 고정)
LAB = Path(tempfile.gettempdir()) / "ads_pipeline_lab"  # Django MEDIA_ROOT·STATIC_ROOT 자리(저장소 밖)
sys.path.insert(0, str(BASE))
os.chdir(BASE)
os.environ.update(DJANGO_SETTINGS_MODULE="config.settings.dev", DB_HOST="127.0.0.1", DB_PORT=os.environ.get("PG_PORT", "5010"), DB_USER="postgres",
                  DB_PASSWORD=Path("/opt/postgresql/config/postgres-password").read_text().strip(), DB_NAME=os.environ.get("PG_DB", "ads_pipeline_lab"),
                  MEDIA_ROOT=str(LAB / "media"), STATIC_ROOT=str(LAB / "static"))
import django  # noqa: E402

django.setup()
from django.conf import settings  # noqa: E402

from billing.api import consistent_read  # noqa: E402
from events import hop_rules, hopcheck, hops, ndjson  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("dump")
p.add_argument("out")
p.add_argument("--as-of")
p.add_argument("--window", choices=["hour", "day"], default="hour")
p.add_argument("--since", help="이 ISO 시각(창 경계) 이후의 창만")
a = p.parse_args()
now = datetime.fromisoformat(a.as_of).astimezone(UTC) if a.as_of else datetime.now(UTC)
size, grace, label = hops.WINDOWS[a.window], settings.EVENTS_HOP_GRACE_SECONDS, hops.window_label(a.window)
out = CWD / a.out
since = datetime.fromisoformat(a.since).astimezone(UTC) if a.since else None

# ② outbox — export_hop_manifests 와 같은 호출(MongoDB 구간 없이)
with consistent_read():
    o_lines, o_open, o_read, _ = hops.summarize_windows(hops.outbox_streams(), now, grace, since, size)
    unsent = hops.unsent_counts(size)
for line in o_lines:
    line["unsent"] = unsent.get((line["window_start"], line["event_name"]), 0)

# ④ Bronze — 덤프 행을 공용 요약 규칙으로(파이프라인 잡과 같은 함수)
rows_read = 0


def dump_rows(path):
    global rows_read
    with path.open(encoding="utf-8") as dump_file:  # 줄 단위로 읽는다(덤프 전체를 메모리에 두지 않는다 — event_id 별 사본만 모은다)
        for line in dump_file:
            rows_read += 1
            r = json.loads(line)
            yield r.get("key"), r["value"], r["partition"], r["offset"]


b_lines, b_open, b_extra = hop_rules.summarize_changes(dump_rows(CWD / a.dump), os.environ["DB_NAME"], now, grace, since, size)

common = {"window": label, "grace_seconds": grace, "started_at": now.isoformat(), "since": hops.format_time(since) if since else None}  # UTC 정시 문자열(hopcheck 가 검증·대조, 설계 01 S1)
mo = ndjson.write(out / "outbox", "outbox", o_lines, "hop_outbox", hops.HOP_SCHEMA_VERSION,
                  checkpoint={"scope": "postgresql-repeatable-read-snapshot", "hop": "hop_outbox", **common,
                              "open_windows": [hops.format_time(w) for w in o_open], "scanned_events": o_read})
mb = ndjson.write(out / "pipeline", "pipeline", b_lines, "hop_pipeline", hops.HOP_SCHEMA_VERSION,
                  checkpoint={"scope": "bronze-delta-snapshot", "hop": "hop_pipeline", **common, "rule_sha256": hop_rules.rule_sha256(),
                              "open_windows": [hops.format_time(w) for w in b_open], "scanned_rows": rows_read, **b_extra})
rows = hopcheck.compare(hopcheck.load_hop(out / "outbox" / "outbox.manifest.json", expected_kind="hop_outbox"),
                        hopcheck.load_hop(out / "pipeline" / "pipeline.manifest.json", name="pipeline", expected_kind="hop_pipeline"), use_unsent=False)
total = {field: sum(line[field] for line in b_lines) for field in ("count", *hop_rules.PIPELINE_COUNTS)}
report = {
    "as_of": now.isoformat(), "window": label, "grace_seconds": grace, "since": hops.format_time(since) if since else None,
    "outbox": {"scanned_events": o_read, "lines": len(o_lines), "open_windows": [hops.format_time(w) for w in o_open], "manifest_sha256": ndjson.manifest_digest(mo)},
    "bronze": {"rows": rows_read, "distinct_events": total["count"], **{f: total[f] for f in hop_rules.PIPELINE_COUNTS}, **b_extra,
               "lines": len(b_lines), "open_windows": [hops.format_time(w) for w in b_open], "manifest_sha256": ndjson.manifest_digest(mb)},
    "status": dict(Counter(x["status"] for x in rows)),
    "rows": rows,
}
(out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
print("C4_REPORT " + json.dumps({k: report[k] for k in ("as_of", "since", "outbox", "bronze", "status")}, ensure_ascii=False))
