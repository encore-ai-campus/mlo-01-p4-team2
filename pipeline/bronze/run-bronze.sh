#!/usr/bin/env bash
# Bronze 상시 쿼리 실행(로컬). 운영 SparkApplication 의 restartPolicy OnFailure(최초 1회 + 재시도 3회 — 설계 R6-1 (나), 팀 Step2)를 흉내 낸다.
# - 비정상 종료(드라이버 강제 종료·S3/Kafka 장애로 쿼리 실패)면 같은 checkpoint 로 다시 띄운다. 재시도를 다 쓰면 rc=1 로 끝난다(경보 → 사람이 다시 제출).
# - 이 스크립트에 SIGTERM 을 보내면(앞에서 돌릴 때는 Ctrl-C 도) Bronze 를 정상 종료하고 다시 띄우지 않는다(사람이 멈춘 것).
#   백그라운드로 띄운 스크립트는 bash 가 SIGINT 를 무시한 채 시작하므로 SIGTERM 으로 멈춘다.
# - 잠듦을 막고 돈다(설계 S7). checkpoint 경로마다 하나만 띄운다(설계 B3).
set -uo pipefail
RETRIES="${BRONZE_RETRIES:-3}"
INTERVAL="${BRONZE_RETRY_INTERVAL:-10}"  # 재시도 간격(초) — 팀 Step2 에서 아직 정하지 않은 값이라 임시값
here="$(cd "$(dirname "$0")" && pwd)"
child=""
stop() { echo "bronze stop requested"; [ -n "$child" ] && kill -TERM "$child" 2>/dev/null; wait "$child" 2>/dev/null; exit 0; }
trap stop TERM INT
for attempt in $(seq 0 "$RETRIES"); do
  if [ "$attempt" -gt 0 ]; then echo "bronze retry $attempt/$RETRIES in ${INTERVAL}s"; sleep "$INTERVAL" & wait $!; fi  # wait 중에도 신호를 바로 받는다
  caffeinate -is "$here/spark-submit-ads.sh" "$here/bronze_ads_outbox.py" &
  child=$!
  wait "$child"; rc=$?
  child=""
  [ "$rc" -eq 0 ] && exit 0
  echo "bronze exited rc=$rc (attempt $attempt)"
done
echo "bronze FAILED: retries exhausted ($RETRIES)"
exit 1
