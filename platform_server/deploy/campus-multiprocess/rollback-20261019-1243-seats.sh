#!/bin/bash
# 10/19 12:43 반영(소파·스툴·옥상 벤치 충돌 없앰)을 되돌린다: worlds.json 을 덮고 소켓 프로세스·작업을 하나씩 다시 띄운다.
# 사용(dev/ 에서): bash deploy/campus-multiprocess/rollback-20261019-1243-seats.sh [seats|all]
#   seats(기본): 12:40 상태로(의자만 지나감)   all: 12:40 이전 원래 지도로(의자도 다시 막힘)
set -euo pipefail
case "${1:-seats}" in
  seats) B=deploy-backups/seats-passable-20261019-1243 ;;
  all) B=deploy-backups/chairs-passable-20261019-1240 ;;
  *) echo "사용: $0 [seats|all]"; exit 1 ;;
esac
ssh -o BatchMode=yes game-server "B=~/$B bash -s" <<'EOF'
cp -r $B/old/backend ~/
for u in $(systemctl list-units "playdata-backend@*" --plain --no-legend --state=running | cut -d. -f1) playdata-campus-jobs; do
  sudo systemctl restart $u; sleep 4; echo "  $u $(systemctl is-active $u)"
done
echo "game-server: worlds.json ← $B/old, HTTP $(curl -s -o /dev/null -m 10 -w %{http_code} http://127.0.0.1:8000/accounts/login/)"
EOF
