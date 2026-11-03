"""C5-a: 커넥터 중단(REST stop) 중 이벤트 → 슬롯이 WAL을 붙잡는지·Kafka 무변화 → resume → 빠짐·중복 없이 이어 읽는지."""
import json, subprocess, sys, tempfile, time
from pathlib import Path
from c5_lib import PY, NAME, kafka_event_ids, messages, now, offsets, outbox_ids, rest, slot, state, wait_state

S = Path(__file__).parent
LAB = Path(tempfile.gettempdir()) / "ads_pipeline_lab"  # Django MEDIA_ROOT·STATIC_ROOT 자리(저장소 밖)
log = []
def snap(tag):
    rec = {"t": now(), "tag": tag, "state": state(), "slot": slot(), "offsets": offsets()}
    log.append(rec); print(json.dumps(rec, ensure_ascii=False), flush=True)

before = set(outbox_ids())
snap("baseline")
rest("PUT", f"/connectors/{NAME}/stop"); print("stop ->", wait_state("STOPPED"), flush=True)
time.sleep(3); snap("stopped")
r = subprocess.run([PY, str(S / "real_events2.py"), str(LAB / "media"), str(LAB / "static"), sys.argv[1]], capture_output=True, text=True, timeout=300)
print("seed rc", r.returncode, r.stdout.strip().splitlines()[-1][:200], flush=True)
snap("inserted")
time.sleep(30); snap("stopped+30s")
time.sleep(30); snap("stopped+60s")
rest("PUT", f"/connectors/{NAME}/resume"); print("resume ->", wait_state("RUNNING"), flush=True)
elapsed = 0
for wait in (5, 10, 15):
    time.sleep(wait); elapsed += wait; snap(f"resumed+{elapsed}s")
rows = outbox_ids(); new = sorted(set(rows) - before)
kc = kafka_event_ids(messages())
rep = {"new_rows": len(new), "new_by_name": {}, "missing_in_kafka": [e for e in new if kc[e] == 0], "duplicated_in_kafka": [e for e in new if kc[e] > 1],
       "kafka_messages_total": sum(kc.values()), "kafka_distinct_total": len(kc),
       "created_at_range": [min(rows[e][1] for e in new).isoformat(), max(rows[e][1] for e in new).isoformat()]}
for e in new: rep["new_by_name"][rows[e][0]] = rep["new_by_name"].get(rows[e][0], 0) + 1
print("C5A_REPORT " + json.dumps(rep, ensure_ascii=False), flush=True)
LAB.mkdir(parents=True, exist_ok=True)
(LAB / "c5a_report.json").write_text(json.dumps({"log": log, "report": rep}, ensure_ascii=False, indent=1))
