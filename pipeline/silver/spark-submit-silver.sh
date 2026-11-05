#!/usr/bin/env bash
# Silver 잡·Spark 시험 제출(Standalone client 모드). 사용: spark-submit-silver.sh <작업.py> [인자...]
# 이 Mac 전용 가정(JAVA_HOME·PYSPARK_PYTHON·의존 jar 세트 경로)은 이 파일에만 둔다. 운영(k8s Spark Operator)은 같은 잡 코드를
# SparkApplication 으로 돌리고, Java·Python·jar·코드는 이미지에 고정한다(SL-3 다시 나누기 §5).
# - 마스터: SPARK_MASTER(기본 Standalone spark://127.0.0.1:7077, 빠른 Spark 단위 시험은 local[1] — 클러스터 코어를 쓰지 않음).
# - 자원: SPARK_CORES_MAX 기본 1(로컬 코어 4개 중 상시 Bronze 셋이 3개, 예약 잡이 남는 1개를 시각으로 나눔 — 15 §7).
# - 의존 jar: Bronze 와 같은 세트(spark4.1.4-delta4.3.1-kafka4.1.4-aws3.4.2)를 extraClassPath 로(pipeline/bronze/spark-submit-ads.sh 와 같은 이유).
# - 코드: executor 가 Python UDF 에서 pipeline.silver·pipeline.common 을 import 하므로 두 폴더의 *.py 를 경로 그대로 zip 해 --py-files 로 보낸다.
#   (pipeline/·pipeline/common/ 에는 __init__.py 가 없다 — zip 안 namespace 패키지로 import 된다, Python 3.14 확인)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
export PYSPARK_PYTHON=/opt/homebrew/Caskroom/miniforge/base/envs/spark/bin/python
export PYSPARK_DRIVER_PYTHON="$PYSPARK_PYTHON"
export SPARK_LOCAL_IP="${SPARK_LOCAL_IP:-127.0.0.1}"  # 드라이버 RPC·UI 를 루프백에만 연다
DEPS="${SPARK_DEPS:-/opt/spark/jobs/deps/spark4.1.4-delta4.3.1-kafka4.1.4-aws3.4.2}"
[ -f "$DEPS/SHA256SUMS" ] || { echo "의존 jar 세트 없음: $DEPS" >&2; exit 2; }
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
dirs=()
for d in pipeline/silver pipeline/common; do [ -d "$ROOT/$d" ] && dirs+=("$d"); done
(cd "$ROOT" && zip -qr "$WORK/pipeline-code.zip" "${dirs[@]}" -i '*.py')
/opt/spark/current/bin/spark-submit \
  --master "${SPARK_MASTER:-spark://127.0.0.1:7077}" --deploy-mode client \
  --conf "spark.pyspark.python=$PYSPARK_PYTHON" --conf "spark.pyspark.driver.python=$PYSPARK_DRIVER_PYTHON" \
  --conf "spark.cores.max=${SPARK_CORES_MAX:-1}" --executor-memory "${SPARK_EXECUTOR_MEMORY:-1G}" \
  --conf "spark.driver.extraClassPath=$DEPS/*" --conf "spark.executor.extraClassPath=$DEPS/*" \
  --conf "spark.sql.session.timeZone=UTC" \
  --py-files "$WORK/pipeline-code.zip" \
  "$@"
