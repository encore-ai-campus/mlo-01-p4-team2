#!/bin/bash
# front-a 프론트 정적 파일(frontend/dist/*) 몇 개를 바꾼다: 서버 파일이 기준 커밋과 같은지 확인 → ~/deploy-backups/<이름>-<시각>/ 에 백업 →
# 내보내는 곳(/var/www/playdata-campus/static/campus) 먼저, 그다음 ~/frontend/dist(페이지의 ?v= 를 이 파일 내용으로 계산한다). 재시작은 없다.
# 사용(platform_server/ 에서): bash deploy/campus-multiprocess/update-frontend-static.sh <이름> <기준 커밋> frontend/dist/social.js ...
# 기준 커밋은 대상 저장소의 이관 후 해시를 사용한다.
set -euo pipefail
cd "$(dirname "$0")/../.."
NAME=$1 BASE=$2; shift 2
for f in "$@"; do
  [[ $f == frontend/dist/* ]] || { echo "frontend/dist/ 아래 파일만: $f"; exit 1; }
  for there in "~/$f" "/var/www/playdata-campus/static/campus/${f#frontend/dist/}"; do
    ssh -o BatchMode=yes front-a "tr -d '\r' < $there" | diff -q - <(git show "$BASE:./$f") >/dev/null \
      || { echo "front-a 의 $there 가 $BASE 와 다르다 — 멈춤"; exit 1; }
  done
done
B="deploy-backups/$NAME-$(date +%Y%m%d-%H%M)"
ssh -o BatchMode=yes front-a "sudo mkdir -p ~/$B/new ~/$B/old && sudo chown -R \$(id -un): ~/$B"
tar -czf - -C frontend/dist "${@#frontend/dist/}" | ssh -o BatchMode=yes front-a "tar -xzf - -C ~/$B/new"
ssh -o BatchMode=yes front-a "B=~/$B bash -s" <<'EOF'
set -euo pipefail
STATIC=/var/www/playdata-campus/static/campus
for f in $(ls "$B/new"); do
  cp "$STATIC/$f" "$B/old/$f"
  sudo install -m 644 -o root -g www-data "$B/new/$f" "$STATIC/$f"
  install -m 644 "$B/new/$f" ~/frontend/dist/"$f"
  echo "  $f 바꿈"
done
echo "## 끝. 백업 $B"
echo "## 되돌리기: ssh front-a 'for f in \$(ls $B/old); do sudo install -m 644 -o root -g www-data $B/old/\$f $STATIC/\$f; install -m 644 $B/old/\$f ~/frontend/dist/\$f; done'"
EOF
