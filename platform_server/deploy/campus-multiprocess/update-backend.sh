#!/bin/bash
# game-server 백엔드 파일 몇 개를 바꾼다: 서버 파일이 기준 커밋과 같은지 확인 → 지금 파일을 ~/deploy-backups/<이름>-<시각>/old 에 백업 →
# 덮어쓰기 → 소켓 프로세스를 하나씩 다시 띄운다(한 번에 4분의 1만 다시 붙는다). 하나라도 안 뜨면 백업으로 되돌린다.
# 사용(platform_server/ 에서): bash deploy/campus-multiprocess/update-backend.sh <이름> <기준 커밋> backend/campus/consumers.py ...
# 되돌리기: 끝에 출력하는 명령(백업을 다시 덮고 같은 순서로 다시 띄운다).
# 기준 커밋은 대상 저장소의 이관 후 해시를 사용한다.
set -euo pipefail
cd "$(dirname "$0")/../.."
NAME=$1 BASE=$2; shift 2
for f in "$@"; do
  [[ $f == backend/* ]] || { echo "backend/ 아래 파일만: $f"; exit 1; }
  if git cat-file -e "$BASE:./$f" 2>/dev/null; then
    ssh -o BatchMode=yes game-server "tr -d '\r' < ~/$f" | diff -q - <(git show "$BASE:./$f") >/dev/null \
      || { echo "서버의 $f 가 $BASE 와 다르다(서버에서 고쳤다?) — 멈춤"; exit 1; }
  fi
done
B="deploy-backups/$NAME-$(date +%Y%m%d-%H%M)"
ssh -o BatchMode=yes game-server "sudo mkdir -p ~/$B/new && sudo chown -R \$(id -un): ~/$B"
tar -czf - "$@" | ssh -o BatchMode=yes game-server "tar -xzf - -C ~/$B/new"
ssh -o BatchMode=yes game-server "B=~/$B bash -s" <<'EOF'
set -uo pipefail
cd ~
for f in $(cd "$B/new" && find backend -type f); do [ -f "$f" ] && install -D -m 644 "$f" "$B/old/$f"; done
restart() {
  for unit in $(systemctl list-units 'playdata-backend@*' --plain --no-legend --state=running | awk '{print $1}' | sed 's/\.service$//') playdata-campus-jobs; do
    sudo systemctl restart "$unit"; port=${unit#*@}
    if [ "$port" != "$unit" ]; then
      for _ in $(seq 30); do ss -Htln | grep -q "127.0.0.1:$port " && break; sleep 1; done
      ss -Htln | grep -q "127.0.0.1:$port " || return 1
      sleep 3  # 끊긴 사람이 다시 붙을 틈
    fi
    echo "  $unit $(systemctl is-active "$unit")"
  done
  [ "$(curl -s -o /dev/null -m 10 -w '%{http_code}' http://127.0.0.1:8000/accounts/login/)" = 200 ]
}
cp -r "$B/new/backend" ~/
echo "## 백업 $B (old = 이전, new = 올린 것). 다시 띄우기:"
if ! restart; then
  echo "!! 실패: 이전 파일로 되돌린다"; cp -r "$B/old/backend" ~/; restart; exit 1
fi
echo "## 끝. 되돌리기: ssh game-server 'cp -r $B/old/backend ~/ && for u in \$(systemctl list-units playdata-backend@* --plain --no-legend | cut -d. -f1) playdata-campus-jobs; do sudo systemctl restart \$u; sleep 5; done'"
EOF
