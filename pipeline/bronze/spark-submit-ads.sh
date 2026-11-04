#!/usr/bin/env bash
# 광고 파이프라인 Spark 작업 제출(Standalone, client 모드). 사용: spark-submit-ads.sh <작업.py> [인자...]
# 판 고정: Spark 4.1.4 · Delta 4.3.1(Spark 4.1 산출물) · Kafka 소스 4.1.4 · hadoop-aws 3.4.2(= Spark 의 Hadoop 3.4.2, AWS SDK v2 2.29.52)
# 의존 jar 는 Worker 에 미리 둔 세트(Data-Pipelines-Ops 관례, 2026-11-03)를 extraClassPath 로 쓴다 — `--packages` 는 executor 마다
# jar 약 711MB 를 work 폴더로 복사했고(설계 초안 §3.8 B5 — 저장소 밖 문서) `--jars local:` 도 복사됐다(Ops 실측). 세트에는 Spark 에 없는 jar 만 있다
# (extraClassPath 는 Spark jar 보다 앞에 붙으므로 겹치는 판을 넣으면 Spark 판이 밀려난다). 판이 바뀌면 새 세트 이름을 만든다.
# SPARK_EXTRA_PY_FILES: 쉼표로 이은 파이썬 파일을 --py-files 에 더한다(예: Bronze manifest 잡의 공용 사본 pipeline/common/*.py). 없으면 지금과 같다.
# 장시간 실행·장애 시험은 잠듦을 막고 돌린다: caffeinate -is pipeline/bronze/spark-submit-ads.sh <작업.py> (설계 초안 §3.8 B6 — 저장소 밖 문서)
set -euo pipefail
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
export PYSPARK_PYTHON=/opt/homebrew/Caskroom/miniforge/base/envs/spark/bin/python
export PYSPARK_DRIVER_PYTHON="$PYSPARK_PYTHON"
export SPARK_LOCAL_IP="${SPARK_LOCAL_IP:-127.0.0.1}"  # 드라이버 RPC·UI(4040)를 루프백에만 연다(없으면 LAN 주소·모든 인터페이스)
DEPS="${SPARK_DEPS:-/opt/spark/jobs/deps/spark4.1.4-delta4.3.1-kafka4.1.4-aws3.4.2}"
[ -f "$DEPS/SHA256SUMS" ] || { echo "의존 jar 세트 없음: $DEPS" >&2; exit 2; }
exec /opt/spark/current/bin/spark-submit \
  --master "${SPARK_MASTER:-spark://127.0.0.1:7077}" --deploy-mode client \
  --conf "spark.pyspark.python=$PYSPARK_PYTHON" --conf "spark.pyspark.driver.python=$PYSPARK_DRIVER_PYTHON" \
  --conf "spark.cores.max=${SPARK_CORES_MAX:-2}" --executor-memory "${SPARK_EXECUTOR_MEMORY:-1G}" \
  --conf "spark.driver.extraClassPath=$DEPS/*" --conf "spark.executor.extraClassPath=$DEPS/*" \
  --py-files "$(dirname "$0")/bronze_ads_outbox.py${SPARK_EXTRA_PY_FILES:+,$SPARK_EXTRA_PY_FILES}" \
  "$@"
