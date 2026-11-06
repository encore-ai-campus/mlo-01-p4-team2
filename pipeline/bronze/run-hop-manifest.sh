#!/usr/bin/env bash
# Bronze manifest 잡(P2-1) 실행 — 로컬(launchd 견본 local.pipeline.hop-manifest-*.plist·손 실행). 사용: run-hop-manifest.sh hour|day [--since …] [--as-of …]
# - 같은 창 크기의 잡은 동시에 하나만 돈다(lockf -t 0 — 이미 돌고 있으면 기다리지 않고 rc 75로 끝난다. 운영 ScheduledSparkApplication concurrencyPolicy: Forbid 와 같다).
#   포인터를 쓰는 쪽이 하나라는 전제가 이 잠금이다(S3A create 는 확인 후 쓰기라 원자적이지 않다).
# - 이 장치에서만 맞는 값(RustFS 주소·키 파일·Bronze 경로·원천 DB 이름·잠금 폴더)은 여기에만 둔다. 잡 본문(hop_manifest_job.py)에는 기본값이 없다(원천 설정 HOP_SOURCE만 기본 ADS — MVR-2 §2a ②).
#   환경변수로 덮을 수 있다(시험 접두사: HOP_MANIFEST_URI=s3a://ads-lake/tmp/p2-1-test/manifests/hop_pipeline/).
# - Spark 1코어(SPARK_CORES_MAX=1): 상시 Bronze 셋이 쓰고 남는 1코어를 예약 잡이 시각을 나눠 쓴다(매시 :05 시간 잡, 매일 00:35Z 하루 잡 — 하루 주기는 UTC).
set -euo pipefail
window="${1:?사용: run-hop-manifest.sh hour|day [잡 인자...]}"
shift
case "$window" in hour|day) ;; *) echo "창 크기는 hour 또는 day: $window" >&2; exit 2 ;; esac
here="$(cd "$(dirname "$0")" && pwd)"
common="$(cd "$here/../common" && pwd)"
export SPARK_CORES_MAX="${SPARK_CORES_MAX:-1}"
export SPARK_EXECUTOR_MEMORY="${SPARK_EXECUTOR_MEMORY:-1G}"
export SPARK_EXTRA_PY_FILES="$common/hop_rules.py,$common/ndjson.py"
export S3_ENDPOINT="${S3_ENDPOINT:-http://127.0.0.1:9000}"
export S3_ACCESS_KEY_FILE="${S3_ACCESS_KEY_FILE:-/opt/rustfs/config/access-key}"
export S3_SECRET_KEY_FILE="${S3_SECRET_KEY_FILE:-/opt/rustfs/config/secret-key}"
export HOP_BRONZE_TABLE="${HOP_BRONZE_TABLE:-s3a://ads-lake/bronze/ads_outbox_events}"
export HOP_MANIFEST_URI="${HOP_MANIFEST_URI:-s3a://ads-lake/manifests/hop_pipeline/}"
export HOP_SOURCE_DB="${HOP_SOURCE_DB:-ads_pipeline_lab}"
export HOP_GRACE_SECONDS="${HOP_GRACE_SECONDS:-120}"  # 광고 서버 EVENTS_HOP_GRACE_SECONDS 와 같게
locks="${HOP_LOCK_DIR:-$HOME/Library/Caches/ads-pipeline}"
mkdir -p "$locks"
exec /usr/bin/lockf -t 0 "$locks/hop-manifest-$window.lock" caffeinate -is "$here/spark-submit-ads.sh" "$here/hop_manifest_job.py" --window "$window" "$@"
