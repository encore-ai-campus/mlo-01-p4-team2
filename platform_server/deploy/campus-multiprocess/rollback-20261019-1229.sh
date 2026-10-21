#!/bin/bash
# 10/19 12:29 반영(소켓 프로세스 6개 + 말풍선 방식)을 백업으로 되돌린다. 둘 다 재시작 없이 바로 된다.
# 사용(dev/ 에서): bash deploy/campus-multiprocess/rollback-20261019-1229.sh [all|sockets|bubbles]
#   sockets: game-server nginx 설정을 이전(4개)으로 다시 읽고 9105·9106 을 끈다(그 두 프로세스에 붙은 사람은 다시 붙는다)
#   bubbles: front-a 의 social.js·social.css 를 이전 파일로(정적 폴더와 ~/frontend/dist 둘 다)
set -euo pipefail
what=${1:-all}
if [[ $what == all || $what == sockets ]]; then
  ssh -o BatchMode=yes game-server 'B=~/deploy-backups/six-sockets-20261019-1229
    sudo install -m 644 $B/campus-backend.old /etc/nginx/sites-available/campus-backend && sudo nginx -t 2>&1 | tail -1 && sudo systemctl reload nginx
    sudo systemctl disable --now playdata-backend@9105 playdata-backend@9106 2>&1 | tail -1
    echo "game-server: 소켓 프로세스 $(systemctl list-units "playdata-backend@*" --plain --no-legend --state=running | wc -l)개, HTTP $(curl -s -o /dev/null -m 10 -w %{http_code} http://127.0.0.1:8000/accounts/login/)"'
fi
if [[ $what == all || $what == bubbles ]]; then
  ssh -o BatchMode=yes front-a 'B=~/deploy-backups/bubbles-20261019-1229
    for f in $(ls $B/old); do sudo install -m 644 -o root -g www-data $B/old/$f /var/www/playdata-campus/static/campus/$f; install -m 644 $B/old/$f ~/frontend/dist/$f; done
    echo "front-a: 말풍선 이전 파일로 되돌림 ($(ls $B/old | tr "\n" " "))"'
fi
