#!/bin/bash
# 10/19 12:40 반영(의자 충돌 없앰, worlds.json)을 백업으로 되돌린다: 이전 파일을 덮고 소켓 프로세스·작업을 하나씩 다시 띄운다.
# 사용(dev/ 에서): bash deploy/campus-multiprocess/rollback-20261019-1240-chairs.sh
set -euo pipefail
ssh -o BatchMode=yes game-server 'B=~/deploy-backups/chairs-passable-20261019-1240
  cp -r $B/old/backend ~/
  for u in $(systemctl list-units "playdata-backend@*" --plain --no-legend --state=running | cut -d. -f1) playdata-campus-jobs; do
    sudo systemctl restart $u; sleep 4; echo "  $u $(systemctl is-active $u)"
  done
  echo "game-server: worlds.json 이전으로, HTTP $(curl -s -o /dev/null -m 10 -w %{http_code} http://127.0.0.1:8000/accounts/login/)"'
