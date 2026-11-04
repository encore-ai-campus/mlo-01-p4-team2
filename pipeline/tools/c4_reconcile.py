"""C4 대사: outbox ↔ Bronze 구간 manifest(DRD §5.6 계약, 규칙은 광고 서버 events/hops.py 그대로).

사용: c4_reconcile.py <bronze_dump.ndjson> <out_dir> [--as-of ISO] [--window hour|day] [--since ISO]
- --since: 이 시각(창 경계로 줌) 이후의 창만 양쪽에서 요약한다(덤프도 같은 --since 로 만든다). 덤프는 줄 단위로 읽는다.
- hop_outbox: 광고 서버 outbox_streams(한 읽기 스냅샷). hop_pipeline: Bronze value → after → 정규화 문서, event_id 로 중복을 가려 요약.
- op 는 c(스트리밍)·r(증분 스냅샷 재발행)를 같은 문서로 센다. 그 밖의 op 는 문제(not_insert)로 센다.
- 같은 event_id 가 여러 번이면 '중복'으로 따로 세고, after 6열(event_id·event_name·schema_version·aggregate_id·payload·created_at, sent_at 은 뺌)이 다르면 '충돌'로 센다(줄에는 한 번만).
- 대조는 hopcheck.compare 두 번: ① CDC 규칙(outbox 의 unsent 로 부족분을 지연으로 보지 않음) ② 지금 코드 그대로(구간 이름 hop_outbox → unsent 를 지연 근거로 씀).
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
from events import hopcheck, hops, ndjson  # noqa: E402

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

# ④ Bronze — 원본 봉투를 풀어 정규화 문서로
docs, content, seen_at, dup, conflict, problems = {}, {}, {}, Counter(), [], Counter()
CONTENT_COLUMNS = ("event_id", "event_name", "schema_version", "aggregate_id", "payload", "created_at")  # after 의 sent_at 은 뺀다(재발행 op=r 은 보낸 뒤 값을 읽을 수 있다)
rows_read = 0
with (CWD / a.dump).open(encoding="utf-8") as dump_file:  # 줄 단위로 읽는다(덤프 전체를 메모리에 두지 않는다)
    for line in dump_file:
        rows_read += 1
        r = json.loads(line)
        v = json.loads(r["value"])
        s, af = v["source"], v["after"]
        if (s["db"], s["schema"], s["table"]) != (os.environ["DB_NAME"], "public", "events_outbox"):
            problems["foreign_source"] += 1
            continue
        if v["op"] not in ("c", "r") or af is None:  # r = 증분 스냅샷 재발행(RB-0·D13) — outbox 는 INSERT 만이라 c 와 같은 문서다(같은 event_id 면 중복으로 센다)
            problems["not_insert"] += 1
            continue
        doc = hops.normal_document(af["event_id"], af["event_name"], af["schema_version"], datetime.fromisoformat(af["created_at"]), json.loads(af["payload"]))
        pos = f'{r["partition"]}:{r["offset"]}'
        eid = doc["event_id"]
        same = hops.canonical_json({k: json.loads(af[k]) if k == "payload" else af.get(k) for k in CONTENT_COLUMNS})  # 원문 그대로(시각을 줄이지 않는다)
        if eid in docs:
            dup[eid] += 1
            if same != content[eid]:  # 같은 event_id 인데 내용이 다르면 중복이 아니라 충돌(실패)
                conflict.append(eid)
            seen_at[eid].append(pos)
            continue
        docs[eid], content[eid], seen_at[eid] = doc, same, [pos]
b_lines, b_open = hops.summarize(list(docs.values()), now, grace, since, size)

common = {"window": label, "grace_seconds": grace, "started_at": now.isoformat(), "since": since.isoformat() if since else None}
mo = ndjson.write(out / "outbox", "outbox", o_lines, "hop_outbox", hops.HOP_SCHEMA_VERSION,
                  checkpoint={"scope": "postgresql-repeatable-read-snapshot", "hop": "hop_outbox", **common,
                              "open_windows": [hops.format_time(w) for w in o_open], "scanned_events": o_read})
mb = ndjson.write(out / "pipeline", "pipeline", b_lines, "hop_pipeline", hops.HOP_SCHEMA_VERSION,
                  checkpoint={"scope": "bronze-delta-snapshot", "hop": "hop_pipeline", **common,
                              "open_windows": [hops.format_time(w) for w in b_open], "scanned_rows": rows_read, "distinct_events": len(docs),
                              "duplicate_rows": sum(dup.values()), "duplicate_event_ids": sorted(dup), "content_conflicts": sorted(conflict)})
ob, pb = out / "outbox" / "outbox.manifest.json", out / "pipeline" / "pipeline.manifest.json"
rule_cdc = hopcheck.compare(hopcheck.load_hop(ob, name="outbox", expected_kind="hop_outbox"), hopcheck.load_hop(pb, expected_kind="hop_pipeline"))
rule_now = hopcheck.compare(hopcheck.load_hop(ob, expected_kind="hop_outbox"), hopcheck.load_hop(pb, expected_kind="hop_pipeline"))
report = {
    "as_of": now.isoformat(), "window": label, "grace_seconds": grace,
    "outbox": {"scanned_events": o_read, "lines": len(o_lines), "open_windows": [hops.format_time(w) for w in o_open], "manifest_sha256": ndjson.manifest_digest(mo)},
    "since": since.isoformat() if since else None,
    "bronze": {"rows": rows_read, "distinct_events": len(docs), "duplicate_rows": sum(dup.values()), "content_conflicts": conflict, "problems": dict(problems),
               "duplicates": {e: seen_at[e] for e in sorted(dup)}, "lines": len(b_lines), "open_windows": [hops.format_time(w) for w in b_open],
               "manifest_sha256": ndjson.manifest_digest(mb)},
    "status_cdc_rule": dict(Counter(x["status"] for x in rule_cdc)),
    "status_current_rule": dict(Counter(x["status"] for x in rule_now)),
    "rows_cdc_rule": rule_cdc,
    "current_rule_differs": [{"window_start": x["window_start"], "event_name": x["event_name"], "cdc": y["status"], "current": x["status"]}
                             for x, y in zip(rule_now, rule_cdc) if x["status"] != y["status"]],
}
(out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
print("C4_REPORT " + json.dumps({k: report[k] for k in ("as_of", "outbox", "bronze", "status_cdc_rule", "status_current_rule")}, ensure_ascii=False))
