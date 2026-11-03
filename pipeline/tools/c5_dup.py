"""C5-c: Kafka 확정 응답 유실 흉내 — 이미 확정된 메시지를 같은 key·value 바이트로 한 번 더 발행한다(응답을 못 받은 생산자의 재전송과 같은 결과).
사용: c5_dup.py <event_id> [<event_id> ...]"""
import json, subprocess, sys
from c5_lib import ENV, KAFKA, TOPIC, kafka_event_ids, messages, offsets

ids = set(sys.argv[1:])
msgs = messages()
pick = [m for m in msgs if json.loads(m["value"])["after"]["event_id"] in ids]
assert len(pick) == len(ids), (len(pick), len(ids))
before = offsets()
r = subprocess.run(["/opt/kafka/current/bin/kafka-console-producer.sh", "--bootstrap-server", KAFKA, "--topic", TOPIC,
                    "--reader-property", "parse.key=true", "--reader-property", "key.separator=\t", "--request-required-acks", "all"],
                   input="".join(m["key"] + "\t" + m["value"] + "\n" for m in pick), env=ENV, capture_output=True, text=True, timeout=120)
after = offsets()
msgs2 = messages()
kc = kafka_event_ids(msgs2)
copies = {e: [(m["partition"], m["offset"]) for m in msgs2 if json.loads(m["value"])["after"]["event_id"] == e] for e in sorted(ids)}
same_bytes = {e: len({(m["key"], m["value"]) for m in msgs2 if json.loads(m["value"])["after"]["event_id"] == e}) == 1 for e in sorted(ids)}
rep = {"rc": r.returncode, "stderr": r.stderr.strip()[-300:], "offsets_before": before, "offsets_after": after, "positions": copies, "identical_key_value": same_bytes,
       "kafka_messages_total": sum(kc.values()), "kafka_distinct_total": len(kc)}
print("C5C_REPORT " + json.dumps(rep, ensure_ascii=False), flush=True)
