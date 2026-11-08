#!/usr/bin/env bash
# 메타버스 Bronze manifest 잡(MVR-2) 실행 — 로컬(launchd 견본 local.pipeline.metaverse.hop-manifest-*.plist·손 실행). 사용: run-hop-manifest-metaverse.sh hour|day [--since …] [--as-of …]
# 광고 잡과 같은 잡·같은 래퍼(run-hop-manifest.sh)를 메타버스 값으로 부른다. 그 래퍼는 ${…:-기본값} 이라 여기서 넘긴 값이 이긴다.
# - 원천 설정 METAVERSE(공용 규칙 hop_rules.METAVERSE). 게시 접두사는 광고와 다르고 포인터 이름(hop_pipeline_hour·day)은 같다.
# - 잠금 폴더가 광고와 달라(잠금 파일 이름 hop-manifest-<창>.lock 은 같음) 두 원천의 잡이 서로 막지 않는다.
# - 이 장치에서만 맞는 값(Bronze 경로·원천 DB 이름·잠금 폴더)은 여기에만 둔다. 환경변수로 덮을 수 있다(시험 접두사: HOP_MANIFEST_URI=s3a://ads-lake/tmp/mvr-2-test/manifests/hop_pipeline_metaverse/).
set -euo pipefail
export HOP_SOURCE=METAVERSE
export HOP_BRONZE_TABLE="${HOP_BRONZE_TABLE:-s3a://ads-lake/bronze/metaverse/outbox_events}"
export HOP_MANIFEST_URI="${HOP_MANIFEST_URI:-s3a://ads-lake/manifests/hop_pipeline_metaverse/}"
export HOP_SOURCE_DB="${HOP_SOURCE_DB:-metaverse_lab}"
export HOP_LOCK_DIR="${HOP_LOCK_DIR:-$HOME/Library/Caches/metaverse-pipeline}"
exec "$(cd "$(dirname "$0")" && pwd)/run-hop-manifest.sh" "$@"
