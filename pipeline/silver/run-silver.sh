#!/usr/bin/env bash
# Silver 잡 실행 — 로컬(launchd 견본 local.pipeline.silver-<원천>.plist·손 실행). 사용: SILVER_CODE_COMMIT=<커밋 40자> run-silver.sh <원천> <세대>
# - 작업 저장소가 아니라 리뷰된 커밋에 맞춘 실행 전용 코드 사본에서 돈다(SL-5 D4 (가) — 만들기·올리기는 runbooks/silver.md).
#   spark-submit-silver.sh 는 이 사본의 pipeline/silver·pipeline/common 을 zip 해 보내므로, 사본 HEAD 가 SILVER_CODE_COMMIT 과 다르거나
#   사본에 미커밋·추적 안 된 파일이 있으면 돌지 않는다(rc 2) — 리뷰 전 코드가 Silver 표에 쓰지 않게.
# - 같은 표(원천·세대)의 잡은 동시에 하나만 돈다(lockf -t 0 — 이미 돌고 있으면 기다리지 않고 rc 75, lockf 가 stderr 에 한 줄 남긴다.
#   운영 ScheduledSparkApplication concurrencyPolicy: Forbid 와 같다). 잡 안에서는 한 프로세스 한 쿼리만 검사한다(15 §9 33).
# - 이 장치에서만 맞는 값(호수 뿌리·Bronze 경로·원천 DB 이름·읽기 상한·RustFS 주소·키 파일·잠금 폴더)은 여기에만 둔다. 잡 본문에는 기본값이 없다.
#   환경변수로 덮을 수 있다 — 넘김 목록·격리 목록·코드 뿌리는 예외(사본의 것으로 고정). 원천별 값은 아래 case 한 곳이다(광고는 SL-8 뒤에 더한다).
# - Spark 1코어(SPARK_CORES_MAX=1): 상시 Bronze 셋이 쓰고 남는 1코어를 예약 잡이 시각을 나눠 쓴다(Silver 매시 :10 — 15 §7).
set -euo pipefail
[ $# -eq 2 ] || { echo "사용: SILVER_CODE_COMMIT=<커밋 40자> run-silver.sh <원천> <세대>" >&2; exit 2; }
domain="$1"
generation="$2"
case "$generation" in ""|0*|*[!0-9]*) echo "세대는 1 이상의 정수: $generation" >&2; exit 2 ;; esac  # 잠금 파일 이름에 들어간다
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
commit="${SILVER_CODE_COMMIT:-}"
[ -n "$commit" ] || { echo "silver refused: SILVER_CODE_COMMIT(이 사본이 돌 커밋 40자)이 없다 — runbooks/silver.md" >&2; exit 2; }
head="$(git -C "$root" rev-parse HEAD)" || { echo "silver refused: 코드 사본 $root 에서 git rev-parse HEAD 가 실패했다" >&2; exit 2; }
if [ "$head" != "$commit" ]; then
  echo "silver refused: 코드 사본 $root 의 HEAD $head 가 SILVER_CODE_COMMIT $commit 과 다르다 — runbooks/silver.md 의 올리기 절차" >&2
  exit 2
fi
status="$(git -C "$root" status --porcelain --untracked-files=all)" || { echo "silver refused: 코드 사본 $root 에서 git status 가 실패했다" >&2; exit 2; }  # 실패를 깨끗함으로 보지 않는다
if [ -n "$status" ]; then
  echo "silver refused: 코드 사본 $root 에 미커밋·추적 안 된 파일이 있다 — 사본에서는 아무것도 고치지 않는다" >&2
  exit 2
fi
case "$domain" in
  metaverse)
    export SILVER_BRONZE_TABLE="${SILVER_BRONZE_TABLE:-s3a://ads-lake/bronze/metaverse/outbox_events}"
    export SILVER_SOURCE_DB="${SILVER_SOURCE_DB:-metaverse_lab}"
    export SILVER_SOURCE_SCHEMA="${SILVER_SOURCE_SCHEMA:-public}"
    export SILVER_SOURCE_TABLE="${SILVER_SOURCE_TABLE:-eventing_eventoutbox}"
    # 읽기 상한(SL-5 D3 (다)): 평소 매시 실행은 배치 하나. 장애 뒤 Bronze 파일이 커져도(파일 하나 ≤ Bronze 상한 240,000행) 바이트 상한이 먼저 닿아
    # 배치가 약 79만 행을 넘지 않는다(Bronze parquet 행당 약 972 B 실측 — 512 MiB + 넘게 만드는 파일 하나)
    export SILVER_MAX_FILES_PER_TRIGGER="${SILVER_MAX_FILES_PER_TRIGGER:-1000}"
    export SILVER_MAX_BYTES_PER_TRIGGER="${SILVER_MAX_BYTES_PER_TRIGGER:-512m}"
    ;;
  *) echo "모르는 원천: $domain(지금은 metaverse — 광고는 SL-8 뒤)" >&2; exit 2 ;;
esac
export SILVER_LAKE_ROOT="${SILVER_LAKE_ROOT:-s3a://ads-lake}"
export SILVER_ACKNOWLEDGED="$root/pipeline/silver/acknowledged/$domain.json"  # 리뷰·커밋된 사본의 목록만 — 덮지 않는다
export SILVER_QUARANTINE="$root/pipeline/silver/quarantine/$domain.json"  # 유령 격리 목록(SL-9) — 넘김 목록과 같이 리뷰·커밋된 사본만, 덮지 않는다
export SILVER_CODE_ROOT="$root"  # silver_release 는 이 사본의 코드로 — 덮지 않는다
export S3_ENDPOINT="${S3_ENDPOINT:-http://127.0.0.1:9000}"
export S3_REGION="${S3_REGION:-ap-northeast-2}"
export S3_ACCESS_KEY_FILE="${S3_ACCESS_KEY_FILE:-/opt/rustfs/config/access-key}"
export S3_SECRET_KEY_FILE="${S3_SECRET_KEY_FILE:-/opt/rustfs/config/secret-key}"
export SPARK_CORES_MAX="${SPARK_CORES_MAX:-1}"
locks="${SILVER_LOCK_DIR:-$HOME/Library/Caches/silver-pipeline}"
mkdir -p "$locks"
exec /usr/bin/lockf -t 0 "$locks/silver-$domain-g$generation.lock" caffeinate -is "$here/spark-submit-silver.sh" "$here/silver_job.py" --domain "$domain" --generation "$generation"
