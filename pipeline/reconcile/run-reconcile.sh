#!/usr/bin/env bash
# 창 대사(P2-2, 광고 서버 reconcile_hops) 로컬 실행 — launchd 견본 local.pipeline.hop-reconcile-{hour,day}.plist·손 실행. 사용: run-reconcile.sh hour|day [--as-of …]
# 종료 코드는 명령 그대로: 0 정상, 1 확정 불일치, 2 입력 실패, 3 늦음, 75 같은 창 크기 대사가 돌고 있어 건너뜀. 상태 파일: $HOP_RECONCILE_DIR/reconcile-<창>.json
# 이 장치에서만 맞는 값(Python·DB·RustFS 주소와 비밀 파일 경로·작업 폴더)은 여기에만 둔다. 비밀은 파일에서 읽어 환경변수로만 넘기고 출력하지 않는다.
# 쌍: 기본은 lab DB(ads_pipeline_lab) ↔ 본 Bronze(파이프라인 잡의 기본 게시 경로 manifests/hop_pipeline/). MongoDB 구간은 끈다(운영 모양, P2-2 결정 A).
# RustFS 키: 로컬 시험에서는 관리자 키 파일을 읽는다(운영은 manifests/hop_pipeline/ 만 읽는 키 — 정책 초안은 P1-8 Step 기록).
set -euo pipefail
window="${1:?사용: run-reconcile.sh hour|day [--as-of …]}"
shift
case "$window" in hour|day) ;; *) echo "창 크기는 hour 또는 day: $window" >&2; exit 2 ;; esac
root="$(cd "$(dirname "$0")/../.." && pwd)"
py="${RECONCILE_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/envs/claude/bin/python}"
lab="${TMPDIR:-/tmp}/ads_pipeline_lab"
mkdir -p "$lab/media" "$lab/static"
export DJANGO_SETTINGS_MODULE=config.settings.dev EVENTS_HOP_WITH_MONGO=false MEDIA_ROOT="$lab/media" STATIC_ROOT="$lab/static"
export DB_HOST="${DB_HOST:-127.0.0.1}" DB_PORT="${DB_PORT:-5010}" DB_USER="${DB_USER:-postgres}" DB_NAME="${DB_NAME:-ads_pipeline_lab}"
# 비밀은 대입과 export 를 나눈다 — `export X="$(cat …)"` 는 파일이 없어도 export 의 종료 코드(0)라 set -e 에 걸리지 않고 빈 값으로 계속된다
DB_PASSWORD="$(cat "${DB_PASSWORD_FILE:-/opt/postgresql/config/postgres-password}")"; export DB_PASSWORD
export HOP_S3_ENDPOINT="${HOP_S3_ENDPOINT:-http://127.0.0.1:9000}" HOP_S3_BUCKET="${HOP_S3_BUCKET:-ads-lake}" HOP_S3_REGION="${HOP_S3_REGION:-ap-northeast-2}"
export HOP_S3_PREFIX="${HOP_S3_PREFIX:-manifests/hop_pipeline/}"
HOP_S3_ACCESS_KEY_ID="$(cat "${HOP_S3_ACCESS_KEY_FILE:-/opt/rustfs/config/access-key}")"; export HOP_S3_ACCESS_KEY_ID
HOP_S3_SECRET_ACCESS_KEY="$(cat "${HOP_S3_SECRET_KEY_FILE:-/opt/rustfs/config/secret-key}")"; export HOP_S3_SECRET_ACCESS_KEY
work="${HOP_RECONCILE_DIR:-$HOME/Library/Caches/ads-pipeline/reconcile}"
cd "$root/ads_server"
exec caffeinate -is "$py" manage.py reconcile_hops --window "$window" --dir "$work" "$@"
