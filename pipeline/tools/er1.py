"""E-R1(설계 01 §11, 계획 02 P0-6): Kafka signal 로 outbox 창 하나를 읽기 전용 증분 스냅샷하고, 그 창 행만 op=r 로 오는지·스트리밍이 이어지는지 본다.

사용(시험 전용 e2e 환경 — 기본값이 e2e 이름이다):
  er1.py snap <out.json>                                  토픽 끝 offset·outbox 행 수·시각을 적는다(시험 앞뒤)
  er1.py signal --since ISO --until ISO                    execute-snapshot(INCREMENTAL, created_at 창) 신호 1건을 signal 토픽에 쓴다
  er1.py check <before.json> <after.json> --window SINCE,UNTIL[,SIGNAL_AT] [--window …] [--report out.json]
      두 snap 사이의 토픽 메시지를 read_committed 로 읽어 판정한다:
      R1 창마다 outbox 행이 모두 op=r 로 옴(빠짐 0, 중복 허용). SIGNAL_AT 을 주면(부하가 도는 창) 그 뒤에 생긴 행은 op=r 이나 op=c 로 오면 된다
      R2 어느 창에도 들지 않는 op=r 0건 · R3 두 snap 사이에 새로 생긴 outbox 행이 모두 op=c 로 옴(스트리밍 이어짐)
      K  메시지 키가 {"event_id": after.event_id} 하나뿐(키 = PK, message.key.columns 없음)
신호 형식은 Debezium 3.7.0 설치본 상수(ExecuteSnapshot·AbstractSnapshotSignal): data-collections, type, additional-conditions[data-collection, filter].
신호 키는 커넥터의 topic.prefix 와 같아야 한다(다르면 버려진다). signal 토픽은 파티션 0 만 읽힌다.
"""
import argparse
import json
import os
import subprocess
import sys
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import psycopg

E = os.environ.get
KAFKA = E("KAFKA_BOOTSTRAP", "127.0.0.1:9092,127.0.0.1:9192,127.0.0.1:9292")
TOPIC, SIGNAL, PREFIX = E("ER1_TOPIC", "ads.e2e.outbox.events"), E("ER1_SIGNAL", "ads.e2e.outbox.signal"), E("ER1_PREFIX", "adse2e")
DB, PG_PORT = E("PG_DB", "ads_pipeline_e2e"), int(E("PG_PORT", "5010"))
BIN, ENV = "/opt/kafka/current/bin", dict(os.environ, JAVA_HOME=E("JAVA_HOME", "/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"))
CWD = Path.cwd()


def pg():
    return psycopg.connect(host="127.0.0.1", port=PG_PORT, user="postgres", password=Path("/opt/postgresql/config/postgres-password").read_text().strip(),
                           dbname=DB, autocommit=True)


def kafka(*args, check=True, **kw):
    return subprocess.run([f"{BIN}/{args[0]}", "--bootstrap-server", KAFKA, *args[1:]], env=ENV, capture_output=True, text=True, check=check, **kw)


def end_offsets(topic):
    return {int(p): int(o) for _t, p, o in (ln.split(":") for ln in kafka("kafka-get-offsets.sh", "--topic", topic).stdout.split())}


def snap(out):
    with pg() as c:
        rows, latest = c.execute("select count(*), max(created_at) from events_outbox").fetchone()
    data = {"t": datetime.now(UTC).isoformat(), "topic": TOPIC, "end_offsets": end_offsets(TOPIC), "signal_end_offsets": end_offsets(SIGNAL),
            "outbox_rows": rows, "outbox_max_created_at": latest.isoformat() if latest else None}
    (CWD / out).write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print("ER1_SNAP " + json.dumps(data, ensure_ascii=False))


def window_filter(since, until):
    return f"created_at >= '{since.isoformat()}' and created_at < '{until.isoformat()}'"


def signal(since, until):
    sid = f"er1-{uuid.uuid4()}"
    value = {"id": sid, "type": "execute-snapshot", "data": {
        "data-collections": ["public.events_outbox"], "type": "INCREMENTAL",
        "additional-conditions": [{"data-collection": "public.events_outbox", "filter": window_filter(since, until)}]}}
    kafka("kafka-console-producer.sh", "--topic", SIGNAL, "--property", "parse.key=true", "--property", "key.separator=\t",
          input=f"{PREFIX}\t{json.dumps(value)}\n")
    print(f"ER1_SIGNAL id={sid} key={PREFIX} topic={SIGNAL} filter={window_filter(since, until)!r} at={datetime.now(UTC).isoformat()}", flush=True)


def messages(before, after):
    """두 snap 사이(파티션마다 [before, after))의 메시지를 read_committed 로: (op, event_id, created_at, 키 payload) 목록."""
    out = []
    for p, start in before["end_offsets"].items():
        count = after["end_offsets"][p] - start  # EOS 표식이 섞여 있어 실제 메시지는 이보다 적다 — 타임아웃으로 끝낸다
        if count <= 0:
            continue
        r = kafka("kafka-console-consumer.sh", "--topic", TOPIC, "--partition", str(p), "--offset", str(start), "--max-messages", str(count),
                  "--isolation-level", "read_committed", "--timeout-ms", "20000", "--formatter-property", "print.offset=true",
                  "--formatter-property", "print.key=true", check=False)
        for line in r.stdout.splitlines():
            if not line.startswith("Offset:"):
                continue
            off, key, val = line.split("\t", 2)
            if int(off.split(":")[1]) >= after["end_offsets"][p]:
                continue
            v = json.loads(val)
            af = v.get("after") or {}
            k = json.loads(key) if key not in ("", "null") else None
            out.append((v["op"], af.get("event_id"), af.get("created_at"), (k or {}).get("payload", k)))
    return out


def check(before, after, windows, report):
    msgs = messages(before, after)
    reads = Counter(e for op, e, _t, _k in msgs if op == "r")
    creates = {e for op, e, _t, _k in msgs if op == "c"}
    bad_keys = [(op, e, k) for op, e, _t, k in msgs if op in ("c", "r") and k != {"event_id": e}]
    r = {"messages": len(msgs), "by_op": dict(Counter(op for op, _e, _t, _k in msgs)), "op_r_distinct": len(reads),
         "op_r_duplicates": sum(c - 1 for c in reads.values() if c > 1), "K_bad_keys": len(bad_keys), "K_sample": [str(x) for x in bad_keys[:3]],
         "windows": []}
    in_any = set()
    with pg() as c:
        for since, until, signal_at in windows:
            rows = c.execute(f"select event_id::text, created_at from events_outbox where {window_filter(since, until)}").fetchall()
            in_any |= {e for e, _t in rows}
            # 신호 앞에 있던 행은 스냅샷만 보낼 수 있다(op=r). 신호 뒤에 생긴 행은 스트리밍(op=c)으로 와도 된다
            old = {e for e, t in rows if signal_at is None or t < signal_at}
            late = {e for e, t in rows if signal_at is not None and t >= signal_at}
            missing = sorted(old - set(reads)) + sorted(late - set(reads) - creates)
            r["windows"].append({"since": since.isoformat(), "until": until.isoformat(), "signal_at": signal_at.isoformat() if signal_at else None,
                                 "outbox_ids": len(rows), "before_signal": len(old), "after_signal": len(late),
                                 "op_r_of_these": len(set(reads) & {e for e, _t in rows}),
                                 "both_op_c_and_op_r": len({e for e, _t in rows} & set(reads) & creates),   # 스트리밍과 재발행이 같은 event_id 로 둘 다 옴
                                 "op_c_only": len(({e for e, _t in rows} & creates) - set(reads)),          # 재발행 없이 스트리밍만 옴(신호 뒤에 생긴 행)
                                 "R1_missing": len(missing), "R1_sample": missing[:5]})
        # 두 snap 사이에 생긴 행(after snap 은 부하가 끝나 모두 커밋된 뒤에 뜬다)
        new_ids = {x[0] for x in c.execute("select event_id::text from events_outbox where created_at > %s and created_at <= %s",
                                           (before["outbox_max_created_at"], after["outbox_max_created_at"]))}
    outside = sorted(e for e in reads if e not in in_any)
    stream_missing = sorted(new_ids - creates)
    r.update({"R2_outside_windows": len(outside), "R2_sample": outside[:5], "new_outbox_rows_between_snaps": len(new_ids),
              "R3_stream_missing": len(stream_missing), "R3_sample": stream_missing[:5]})
    r["ok"] = all(w["R1_missing"] == 0 for w in r["windows"]) and not outside and not stream_missing and not bad_keys
    if report:
        (CWD / report).write_text(json.dumps(r, ensure_ascii=False, indent=1))
    print("ER1_CHECK " + json.dumps(r, ensure_ascii=False))
    return r["ok"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snap"); s.add_argument("out")
    q = sub.add_parser("signal"); q.add_argument("--since", required=True); q.add_argument("--until", required=True)
    q = sub.add_parser("check"); q.add_argument("before"); q.add_argument("after"); q.add_argument("--report")
    q.add_argument("--window", action="append", required=True, help="SINCE,UNTIL[,SIGNAL_AT] — 여러 번")
    a = ap.parse_args()
    t = lambda x: datetime.fromisoformat(x).astimezone(UTC)  # noqa: E731
    if a.cmd == "snap":
        snap(a.out)
    elif a.cmd == "signal":
        signal(t(a.since), t(a.until))
    else:
        windows = [(t(w.split(",")[0]), t(w.split(",")[1]), t(w.split(",")[2]) if len(w.split(",")) > 2 else None) for w in a.window]
        sys.exit(0 if check(json.loads((CWD / a.before).read_text()), json.loads((CWD / a.after).read_text()), windows, a.report) else 1)
