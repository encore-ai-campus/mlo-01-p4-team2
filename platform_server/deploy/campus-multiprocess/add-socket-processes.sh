#!/bin/bash
# game-server 소켓 프로세스를 4개 → 6개로(9105·9106, 하이퍼스레딩까지 쓴다) 늘리고 nginx upstream 을 바꾼다(least_conn 에 zone).
# 지금 nginx 사이트 파일을 ~/deploy-backups/six-sockets-<시각>/ 에 백업한다. 중간 확인 하나라도 실패하면 백업으로 되돌리고 9105·9106 을 끈다.
# 사용(dev/ 에서): bash deploy/campus-multiprocess/add-socket-processes.sh
set -euo pipefail
cd "$(dirname "$0")"
B="deploy-backups/six-sockets-$(date +%Y%m%d-%H%M)"
ssh -o BatchMode=yes game-server "sudo mkdir -p ~/$B && sudo chown \$(id -un): ~/$B && cat > ~/$B/campus-backend.new" < nginx-campus-backend.conf
ssh -o BatchMode=yes game-server "B=~/$B bash -s" <<'EOF'
set -uo pipefail
SITE=/etc/nginx/sites-available/campus-backend NEW="9105 9106"
cp "$SITE" "$B/campus-backend.old"
rollback() {
  echo "!! 실패: 이전 nginx 설정으로 되돌리고 새 프로세스를 끈다"
  sudo install -m 644 "$B/campus-backend.old" "$SITE"; sudo nginx -t && sudo systemctl reload nginx
  for p in $NEW; do sudo systemctl disable --now "playdata-backend@$p"; done; exit 1
}
for p in $NEW; do
  sudo systemctl enable --now "playdata-backend@$p" 2>&1 | tail -1
  for _ in $(seq 30); do ss -Htln | grep -q "127.0.0.1:$p " && break; sleep 1; done
  ss -Htln | grep -q "127.0.0.1:$p " || { journalctl -u "playdata-backend@$p" --since "-1min" --no-pager | tail -5; rollback; }
  echo "  playdata-backend@$p 듣는 중"
done
sleep 2  # 새 프로세스가 다른 프로세스 목록(1.2초)을 받은 뒤에 접속을 넘긴다
sudo install -m 644 "$B/campus-backend.new" "$SITE"
sudo nginx -t 2>&1 | tail -1 || rollback
sudo systemctl reload nginx || rollback
sleep 2
[ "$(curl -s -o /dev/null -m 10 -w '%{http_code}' http://127.0.0.1:8000/accounts/login/)" = 200 ] || rollback
echo "## 끝: 소켓 프로세스 $(systemctl list-units 'playdata-backend@*' --plain --no-legend --state=running | wc -l)개. 백업 $B"
echo "## 되돌리기: ssh game-server 'sudo install -m 644 $B/campus-backend.old $SITE && sudo systemctl reload nginx && sudo systemctl disable --now playdata-backend@9105 playdata-backend@9106'"
EOF
