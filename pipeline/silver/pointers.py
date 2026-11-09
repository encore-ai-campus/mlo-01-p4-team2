"""Silver 원천별 세대 포인터(SL-7 — 계획 15 §2 SL-7 6판 r12, 계획 40 r3: 결정 D1·D2·D3·D5 (가), D6 (가)).

광고·메타버스 Silver·Gold는 원천별로 따로 간다(사용자 2026-11-09 — 합친 보기 없음, 로직만 공통).
- 포인터 표 `<뿌리>/silver/<원천>/pointer`(control.pointer_path): 원천마다 한 행(지금 세대) Delta 표. 바꾸기는 손 도구(`set`)만 한다 — Silver 잡은 포인터를 모른다(D1).
  한 원천의 바꾸기·확인은 그 원천 폴더(`silver/<원천>/`) 안의 표만 읽고 그 원천 포인터 표에만 쓴다(D6).
  바꾸기 전 확인(설계 13 SV6 "새 세대가 따라잡고 대사가 match 인 뒤"): 그 세대 대사 결과 표의 마지막 커밋 요약(reconcile 의 userMetadata)이
  match 이고, 요약의 silver_version = 지금 본 표 판(대사 뒤 본 표에 쓰기가 없었음). 따라잡음(D5): 지금 포인터가 있으면 그 세대의 마지막 대사와 같은
  Bronze 표(끝 '/' 를 뗀 글자)일 때 대상의 bronze_version 이 뒤지지 않아야 한다 — 뒤지면 우회로도 지나가지 않는다. 견줄 수 없으면(Bronze 표가 다름·
  요약에 키 없음·결과 표 없음) `bronze_changed`(이유)로만 지난다 — 처음 쓰는 포인터·견줄 수 있는 경우에는 받지 않는다.
  확인이 하나라도 어긋나면 사유를 모두 적은 PointerError, 쓰지 않는다.
  쓰기는 한 행 덮어쓰기 커밋 하나, userMetadata(쓰기 옵션) = {domain, from_generation, to_generation, set_by, reason, bronze_changed}.
  표 이력이 바꾼 기록이고 행을 지우는 길은 없다. 포인터 행의 판들은 그때의 근거일 뿐이다 — 읽기는 읽을 때 판을 다시 정한다.
- read_events(spark, root, domain): 그 원천 하나만. 포인터 표 판과 그 세대 본 표 판을 먼저 정해 그 판으로 읽고(D3), 본 표 열(이름·순서·형)이
  지금 코드의 contracts.SILVER_FIELDS 와 같은지 본다(옛 열 모양 세대를 읽지 않게). 격리·오류 표는 읽지 않는다(G11).
  반환 View(frame, pointer_version, domain, generation, path, version).
- 손 도구: `spark-submit-silver.sh pipeline/silver/pointers.py set --domain <원천> --generation <N> --by <이름> --reason <이유> [--bronze-changed <이유>]`
  또는 `show --domain <원천>`. 설정은 SILVER_LAKE_ROOT 와(s3a 면) S3 넷 — control.pointer_command 가 Spark 전에 검사한다. rc 0 바꿈·보임, 2 인자·설정, 1 그 밖(PointerError 포함).
"""
import json
import os
import sys
from collections import namedtuple
from datetime import datetime, timezone

from pyspark.sql import functions as F

from pipeline.silver import contracts, control, framework, reconcile, silver_job

POINTER_FIELDS = (
    ("domain", "string", False), ("generation", "int", False), ("set_at", "timestamp", False), ("set_by", "string", False), ("reason", "string", False),
    ("reconcile_version", "bigint", False), ("silver_version", "bigint", False), ("silver_release", "string", False), ("checkpoint_id", "string", False),
    ("bronze_table", "string", False), ("bronze_version", "bigint", False),
)
SUMMARY_KEYS = {"status": str, "silver_version": int, "bronze_version": int, "checkpoint_id": str, "silver_release": str, "bronze_table": str}
HISTORY = 10  # show 가 찍는 마지막 커밋 수
View = namedtuple("View", "frame pointer_version domain generation path version")


class PointerError(RuntimeError):
    """포인터를 바꾸지 않거나 읽지 않는다 — 사유를 모두 적는다."""


def _read(spark, path, version):
    return spark.read.format("delta").option("versionAsOf", version).load(path)


def _shape_problem(path, frame, fields):
    actual = [(f.name, f.dataType.simpleString()) for f in frame.schema.fields]
    expected = [(name, contracts._TYPES[kind].simpleString()) for name, kind, _ in fields]
    if actual == expected:
        return None
    extra, lacking = [c for c in actual if c not in expected], [c for c in expected if c not in actual]
    return f"{path} 의 열이 이 판의 표 모양과 다르다(더 있음 {extra}, 없음 {lacking}{'' if extra or lacking else ', 순서 다름'})"


def _pointer(spark, root, domain):
    """원천 포인터 표의 (경로, 판, 행 dict 또는 None) — 표가 없으면 판 -1·행 None. 열 모양이 다르거나 행이 하나가 아니거나 다른 원천 행이면 PointerError."""
    path = control.pointer_path(root, domain)
    version = framework.table_state(spark, path)[0]
    if version < 0:
        return path, version, None
    frame = _read(spark, path, version)
    problem = _shape_problem(path, frame, POINTER_FIELDS)
    if problem:
        raise PointerError(problem)
    rows = frame.collect()
    if len(rows) != 1 or rows[0]["domain"] != domain:
        raise PointerError(f"{path} 판 {version} 은 원천 {domain} 의 한 행이어야 한다: {[r['domain'] for r in rows]}")
    return path, version, rows[0].asDict()


def _last_run(spark, path):
    """대사 결과 표의 (마지막 커밋 판, 요약 dict 또는 None) — 표가 없으면 None."""
    if framework.table_state(spark, path)[0] < 0:
        return None
    last = spark.sql(f"DESCRIBE HISTORY delta.`{path}` LIMIT 1").first()
    try:
        summary = json.loads(last["userMetadata"])
    except (TypeError, ValueError):
        summary = None
    return last["version"], summary if isinstance(summary, dict) else None


def set_generation(spark, root, domain, generation, *, by, reason, bronze_changed=None, log=print):
    """원천 `domain` 의 포인터를 세대 `generation` 으로(D1·D5 — 모듈 설명). 반환: 쓴 포인터 행 dict."""
    control.pointer_change(domain, generation, by, reason, bronze_changed)
    path, _, pointer = _pointer(spark, root, domain)
    current = pointer["generation"] if pointer else None
    paths = control.silver_paths(root, domain, generation)
    problems = []
    silver_version = framework.table_state(spark, paths.silver)[0]
    if silver_version < 0:
        problems.append(f"본 표가 없다: {paths.silver}")
    else:
        problems.append(_shape_problem(paths.silver, _read(spark, paths.silver, silver_version), contracts.SILVER_FIELDS))
    run, summary = _last_run(spark, paths.reconcile), None
    if run is None:
        problems.append(f"대사 결과 표가 없다: {paths.reconcile}")
    else:
        reconcile_version, summary = run
        lacking = [key for key, kind in SUMMARY_KEYS.items() if summary is None or type(summary.get(key)) is not kind]
        if lacking:
            problems.append(f"{paths.reconcile} 마지막 커밋(판 {reconcile_version})의 대사 요약에 {lacking} 이(가) 없다 — 이 판의 Silver 잡으로 한 회차 돈 뒤 다시")
            summary = None
    if summary is not None:
        if summary["status"] != reconcile.MATCH:
            problems.append(f"마지막 대사(판 {reconcile_version})가 {summary['status']} 다 — 대사 match 인 회차 뒤에만 바꾼다")
        if summary["silver_version"] != silver_version:
            problems.append(f"마지막 대사가 읽은 본 표 판 {summary['silver_version']} ≠ 지금 판 {silver_version} — 대사 뒤 본 표에 쓰기가 있었다(다음 회차가 끝난 뒤 다시)")
        bypass = False  # 따라잡음(D5)을 견줄 수 없어 우회 이유가 필요한가
        if current is not None:
            old = _last_run(spark, control.silver_paths(root, domain, current).reconcile)
            old_summary = (old[1] if old else None) or {}
            old_table, old_version = old_summary.get("bronze_table"), old_summary.get("bronze_version")
            if isinstance(old_table, str) and type(old_version) is int and old_table.rstrip("/") == summary["bronze_table"].rstrip("/"):
                if summary["bronze_version"] < old_version:
                    problems.append(f"따라잡지 않았다: 마지막 대사의 Bronze 판 {summary['bronze_version']} < 지금 포인터 g{current} 의 {old_version}(같은 Bronze 표) — "
                                    "그 세대 예약을 올려 rc 0 회차가 끝난 뒤 다시")
            else:
                bypass = True
                if bronze_changed is None:
                    problems.append(f"따라잡음을 견줄 수 없다: 지금 포인터 g{current} 의 대사 {'결과 표 없음' if old is None else f'Bronze 표 {old_table!r} 판 {old_version!r}'} · "
                                    f"대상 Bronze 표 {summary['bronze_table']!r} — Bronze 표가 바뀐 세대 전환이면 --bronze-changed <이유>")
        if bronze_changed is not None and not bypass:
            problems.append("--bronze-changed 는 따라잡음을 견줄 수 없을 때만 쓴다(처음 쓰는 포인터·같은 Bronze 표에는 쓰지 않는다)")
    problems = [p for p in problems if p]
    if problems:
        raise PointerError(f"포인터를 바꾸지 않는다(domain={domain} generation={generation}): " + " / ".join(problems))
    row = {"domain": domain, "generation": generation, "set_at": datetime.now(timezone.utc), "set_by": by, "reason": reason,
           "reconcile_version": reconcile_version, "silver_version": silver_version, "silver_release": summary["silver_release"],
           "checkpoint_id": summary["checkpoint_id"], "bronze_table": summary["bronze_table"], "bronze_version": summary["bronze_version"]}
    metadata = {"domain": domain, "from_generation": current, "to_generation": generation, "set_by": by, "reason": reason, "bronze_changed": bronze_changed}
    (spark.createDataFrame([tuple(row[name] for name, _, _ in POINTER_FIELDS)], contracts._schema(*POINTER_FIELDS))
     .write.format("delta").mode("overwrite").option("userMetadata", json.dumps(metadata, ensure_ascii=False, sort_keys=True)).save(path))
    log(f"silver pointer domain={domain} from={current} to={generation} reconcile_version={reconcile_version} silver_version={silver_version} "
        f"bronze_version={summary['bronze_version']} bronze_changed={'yes' if bronze_changed else 'no'} by={by}")
    return row


def read_events(spark, root, domain):
    """원천 하나의 지금 세대 본 표(D3 — 모듈 설명). 반환 View(frame, pointer_version, domain, generation, path, version)."""
    path, pointer_version, pointer = _pointer(spark, root, domain)
    if pointer is None:
        raise PointerError(f"포인터 표가 없다: {path} — 첫 포인터를 쓰기 전에는 읽지 않는다")
    table = control.silver_paths(root, domain, pointer["generation"]).silver
    version = framework.table_state(spark, table)[0]
    if version < 0:
        raise PointerError(f"포인터 g{pointer['generation']} 의 본 표가 없다: {table}")
    frame = _read(spark, table, version)
    problem = _shape_problem(table, frame, contracts.SILVER_FIELDS)
    if problem:
        raise PointerError(problem)
    return View(frame, pointer_version, domain, pointer["generation"], table, version)


def show(spark, root, domain, log=print):
    """원천 포인터 행과 마지막 커밋 몇 개(이력). 쓰지 않는다."""
    path, version, pointer = _pointer(spark, root, domain)
    if pointer is None:
        log(f"silver pointer table=none path={path}")
        return
    set_at = _read(spark, path, version).select(F.date_format("set_at", "yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'")).first()[0]  # 세션(UTC) 글자
    log(f"silver pointer version={version} " + " ".join(f"{name}={set_at if name == 'set_at' else pointer[name]}" for name, _, _ in POINTER_FIELDS))
    for r in (spark.sql(f"DESCRIBE HISTORY delta.`{path}` LIMIT {HISTORY}")
              .select("version", F.date_format("timestamp", "yyyy-MM-dd'T'HH:mm:ss'Z'").alias("at"), "operation", "userMetadata").collect()):
        log(f"silver pointer history version={r['version']} at={r['at']} operation={r['operation']} metadata={r['userMetadata']}")


def main(argv=None, environ=None):
    try:
        command = control.pointer_command(argv, os.environ if environ is None else environ)
    except ValueError as error:
        print(f"silver pointer error {error}", file=sys.stderr)
        return 2
    spark = silver_job.session("silver-pointers", command.s3)
    log = lambda line: print(line, flush=True)  # noqa: E731
    try:
        if command.command == "show":
            show(spark, command.root, command.domain, log=log)
        else:
            set_generation(spark, command.root, command.domain, command.generation, by=command.by, reason=command.reason,
                           bronze_changed=command.bronze_changed, log=log)
    except PointerError as error:
        print(f"silver pointer refused {error}", file=sys.stderr)
        return 1
    finally:
        spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
