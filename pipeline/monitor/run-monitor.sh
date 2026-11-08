#!/usr/bin/env bash
# 주기 감시(P2-4b) — LaunchAgent local.pipeline.monitor.plist 가 1분마다 부른다. 사용: run-monitor.sh <python>(설정은 plist 의 환경변수)
# - 출력은 MONITOR_LOG 에 이어 쓴다. 시작할 때 그 파일이 10 MiB 를 넘으면 .1 로 하나만 옮긴다(결정 D (나) — 한 회차 JSON 약 6 KB, 하루 약 8 MB:
#   10 MiB 면 약 1.3일, .1 까지 약 2.6일). plist 에 StandardOutPath·StandardErrorPath 를 두지 않는다 — launchd 는 그 파일을 래퍼보다 먼저 열어,
#   옮긴 회차의 출력이 .1 에 붙는다(리뷰 R3). 그래서 트레이스백("점검 자체 실패" — JSON 없는 rc 1)이 로그에 남는 길은 아래 2>&1 하나다.
# - caffeinate 를 쓰지 않는다: 감시가 잠듦 방지를 걸면 S7 sleep 이 늘 "prevented"(ok)가 된다(리뷰 R1). 다른 래퍼(run-silver 등)는 쓴다.
# - python -I: 스크립트 폴더·현재 폴더의 .py·.pyc 를 import 하지 않는다. 코드 판 확인(check.py code_pin)의 git status 는 무시 파일(*.pyc)을 보지 않는다(리뷰 R2).
set -u
py="${1:?사용: run-monitor.sh <python>}"
log="${MONITOR_LOG:?MONITOR_LOG(로그 파일 경로)가 없다}"
if [ -f "$log" ] && [ "$(wc -c < "$log")" -gt 10485760 ]; then
  mv -f "$log" "$log.1"
fi
exec "$py" -I "$(cd "$(dirname "$0")" && pwd)/check.py" >>"$log" 2>&1
