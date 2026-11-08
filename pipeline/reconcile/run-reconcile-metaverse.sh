#!/usr/bin/env bash
# 메타버스 창 대사 한 번(MVR-2, pipeline/reconcile/metaverse_hops.py) 로컬 실행 — launchd 견본 local.pipeline.metaverse.hop-reconcile-{hour,day}.plist·손 실행.
# 사용: run-reconcile-metaverse.sh hour|day [--as-of …]. 종료 코드는 도구 그대로: 0 정상, 1 확정 불일치, 2 입력 실패, 3 늦음, 75 건너뜀. 상태 파일: $HOP_RECONCILE_DIR/reconcile-<창>.json
# 이 장치에서만 맞는 값(Python·DB·RustFS 주소와 비밀 파일 경로·작업 폴더)은 여기에만 둔다. 비밀은 경로만 넘긴다 — DB 암호는 libpq 가 PGPASSFILE 에서,
# S3 키는 도구가 키 파일에서 읽는다(환경변수에 값이 들어가지 않는다). 파일이 없으면 아무것도 하지 않고 멈춘다.
# 쌍: 기본은 lab DB(metaverse_lab, 앱 계정 metaverse_app + READ ONLY 트랜잭션 — 운영은 전용 읽기 역할, 계획 V3) ↔ 메타버스 Bronze manifest(manifests/hop_pipeline_metaverse/).
# RustFS 키: 로컬 시험에서는 관리자 키 파일을 읽는다(운영은 manifests/hop_pipeline_metaverse/ 만 읽는 키).
set -euo pipefail
window="${1:?사용: run-reconcile-metaverse.sh hour|day [--as-of …]}"
shift
case "$window" in hour|day) ;; *) echo "창 크기는 hour 또는 day: $window" >&2; exit 2 ;; esac
root="$(cd "$(dirname "$0")/../.." && pwd)"
py="${RECONCILE_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/envs/claude/bin/python}"
export PGHOST="${PGHOST:-127.0.0.1}" PGPORT="${PGPORT:-5010}" PGDATABASE="${PGDATABASE:-metaverse_lab}" PGUSER="${PGUSER:-metaverse_app}"
export PGPASSFILE="${PGPASSFILE:-/opt/postgresql/config/metaverse-app.pgpass}"
export HOP_S3_ENDPOINT="${HOP_S3_ENDPOINT:-http://127.0.0.1:9000}" HOP_S3_BUCKET="${HOP_S3_BUCKET:-ads-lake}" HOP_S3_REGION="${HOP_S3_REGION:-ap-northeast-2}"
export HOP_S3_PREFIX="${HOP_S3_PREFIX:-manifests/hop_pipeline_metaverse/}"
export HOP_S3_ACCESS_KEY_FILE="${HOP_S3_ACCESS_KEY_FILE:-/opt/rustfs/config/access-key}" HOP_S3_SECRET_KEY_FILE="${HOP_S3_SECRET_KEY_FILE:-/opt/rustfs/config/secret-key}"
export HOP_GRACE_SECONDS="${HOP_GRACE_SECONDS:-120}"  # Bronze manifest 잡의 HOP_GRACE_SECONDS 와 같게(다르면 상태 파일 두 grace 값으로 보인다)
for secret in "$PGPASSFILE" "$HOP_S3_ACCESS_KEY_FILE" "$HOP_S3_SECRET_KEY_FILE"; do
  [ -r "$secret" ] || { echo "비밀 파일을 읽을 수 없다: $secret" >&2; exit 2; }
done
work="${HOP_RECONCILE_DIR:-$HOME/Library/Caches/metaverse-pipeline/reconcile}"
cd "$root"
exec caffeinate -is "$py" -m pipeline.reconcile.metaverse_hops --window "$window" --dir "$work" "$@"
