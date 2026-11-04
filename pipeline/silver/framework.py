"""Silver 공통 틀 — 어댑터 인터페이스와 어댑터 결과 확인(계획 15 §2.1, SL-3 다시 나누기 §3 SL-3a, SL-3a 결정 D1).

어댑터(원천마다 `pipeline/silver/adapters/<domain>.py`)는 **Bronze DataFrame → (정상 DataFrame, 오류 DataFrame)** 변환이다.
- 입력: Bronze 표의 행(contracts.BRONZE_SCHEMA).
- 정상: contracts.OK_SCHEMA — Bronze 열 그대로 + 어댑터 열(contracts.ADAPTER_FIELDS). 나머지 공통 열은 공통 틀이 채운다(SL-3b·SL-3c).
- 오류: contracts.ADAPTER_ERROR_SCHEMA — Bronze 열 그대로 + error_code(contracts.ADAPTER_ERROR_CODES)·reason·event_id(모르면 null).
- 모든 Bronze 행은 정상·오류 가운데 정확히 한 번, Bronze 열을 바꾸지 않고 나온다(조용한 유실 0).
- **판정 순서**(앞에서 걸린 코드가 그 행의 코드다 — 설정 이상이 행 오류에 가려 멈추지 않는 일을 막는다):
  ① value 가 null(tombstone) → `not_insert`(멈춤, 15 §2.2)
  ② 봉투 엄격 검사(contracts.strict_reasons 의 `value` 사유) → `decode`. 통과한 행은 try_parse_json(Variant)으로 해석하고,
     Spark 가 해석하지 못하면(Variant 크기 한도 등) 그것도 `decode`
  ③ 출처(source.db·schema·table 이 그 원천의 outbox 가 아님) → `foreign_source`, op 가 c·r 가 아니거나 after 가 없음 → `not_insert`(둘 다 멈춤)
  ④ 안쪽 JSON 문자열 엄격 검사(contracts.strict_reasons 의 `nested` 사유) → `decode`
  ⑤ contracts.extract 로 표대로 꺼냄·원천별 계약 → `contract`·`unknown_schema`
  ②와 ④는 UDF 한 번의 두 결과다(봉투는 한 번만 해석). ③을 ④보다 앞에 두는 것은 다른 표의 행이 같은 이름의 열에 JSON 이 아닌 문자열을
  들고 와도 decode(계속)가 아니라 foreign_source(멈춤)가 되게 하기 위해서다.
- 내용 해시(content_sha256)는 pipeline/common/hop_rules 규칙의 UDF 다(SL-4·SL-8).
check_adapted 는 이 약속을 Spark 식으로 확인한다. 어긋나면 배치를 멈춘다(SV7 '설정 이상은 멈춤').
"""
from functools import reduce

from pyspark.sql import functions as F

from pipeline.silver import contracts

POSITION = ("topic", "partition", "offset")  # Bronze 행 하나를 가리키는 위치
_BRONZE_NAMES = [name for name, _, _ in contracts.BRONZE_FIELDS]


class AdapterContractError(RuntimeError):
    """어댑터 결과 확인에서 결함이 나왔다 — 배치를 멈춘다(사유에 원인 쪽이 적힌다: 어댑터 결함 또는 Bronze 위치 중복)."""


def _same_columns(frame, schema, label):
    expected = [(f.name, f.dataType.simpleString()) for f in schema.fields]
    actual = [(f.name, f.dataType.simpleString()) for f in frame.schema.fields]
    if actual != expected:  # 이름·순서·형(NULL 허용은 Spark 식이 늘 참으로 둘 수 있어 행 검사로 본다)
        raise AdapterContractError(f"{label} 결과의 열이 다르다:\n  기대 {expected}\n  실제 {actual}")


def _row_defects(frame, checks):
    """checks: [(결함 조건 열, 사유)] → 결함이 하나라도 있는 행을 (위치, defect 사유) 로."""
    reasons = F.concat_ws("; ", *[F.when(condition, F.lit(reason)) for condition, reason in checks])
    return frame.select(*POSITION, reasons.alias("defect")).where(F.col("defect") != "")


def _keyed(frame):
    """위치 + Bronze 열 지문. 지문은 어댑터가 Bronze 열을 바꾼 것을 잡는 용도라 64비트 xxhash64 로 충분하다(보안 경계가 아님)."""
    return frame.select(*POSITION, F.xxhash64(*[F.col(name) for name in _BRONZE_NAMES]).alias("_bronze_fp"))


def defects(bronze, ok, errors, domain):
    """어댑터 결과의 결함 행(위치, defect). 모양(열 이름·순서·형)이 다르면 실행 전에 AdapterContractError."""
    _same_columns(ok, contracts.OK_SCHEMA, "정상")
    _same_columns(errors, contracts.ADAPTER_ERROR_SCHEMA, "오류")
    ok_checks = [(F.col(name).isNull(), f"{name} 이(가) null") for name, _, nullable in contracts.ADAPTER_FIELDS if not nullable]
    ok_checks += [
        (F.col("domain") != F.lit(domain), f"domain 이 {domain} 가 아니다"),
        (~F.col("event_id").rlike(contracts.UUID_RE), "event_id 가 소문자 UUID 가 아니다"),
        (~F.col("content_sha256").rlike(contracts.SHA256_RE), "content_sha256 이 소문자 16진 64자가 아니다"),
        (~F.col("first_op").isin("c", "r"), "정상 결과의 op 는 c·r 뿐이다(그 밖은 not_insert 오류)"),
        (F.col("value_binary").isNull(), "value 가 null 인 행(tombstone)이 정상 결과에 있다"),
    ]
    error_checks = [
        (~F.coalesce(F.col("error_code").isin(*contracts.ADAPTER_ERROR_CODES), F.lit(False)), "어댑터가 낼 수 없는 오류 코드"),
        (F.coalesce(F.trim(F.col("reason")) == "", F.lit(True)), "오류 사유가 비었다"),
        (F.col("event_id").isNotNull() & ~F.col("event_id").rlike(contracts.UUID_RE), "오류의 event_id 가 소문자 UUID 가 아니다"),
        (F.col("value_binary").isNull() & ~F.coalesce(F.col("error_code") == "not_insert", F.lit(False)), "value 가 null 인 행(tombstone)은 not_insert 다(순서 ①)"),
    ]
    keys = list(POSITION) + ["_bronze_fp"]
    source, results = _keyed(bronze), _keyed(ok).unionByName(_keyed(errors))
    conservation = [
        source.groupBy(*POSITION).count().where(F.col("count") > 1)
        .select(*POSITION, F.lit("Bronze 에 같은 위치가 둘 이상 있다 — 어댑터 결함이 아니다(설계 01 S3·감시 M7, RB-4 를 확인). 처리는 SL-3b").alias("defect")),
        source.join(results, keys, "left_anti").select(*POSITION, F.lit("Bronze 행이 결과에 없거나 Bronze 열이 바뀌었다").alias("defect")),
        results.groupBy(*POSITION).count().where(F.col("count") > 1).select(*POSITION, F.lit("Bronze 행 하나가 결과에 두 번 이상 나온다").alias("defect")),
        results.join(source, keys, "left_anti").select(*POSITION, F.lit("결과에 Bronze 에 없는 행이 있다(위치 또는 Bronze 열이 다름)").alias("defect")),
    ]
    return reduce(lambda a, b: a.unionByName(b), [_row_defects(ok, ok_checks), _row_defects(errors, error_checks), *conservation])


def check_adapted(bronze, ok, errors, domain, examples=5):
    """어댑터 결과를 확인하고 그대로 돌려준다(ok, errors). 결함이 있으면 예시와 함께 AdapterContractError.
    Spark 실행이 한 번 든다. 부르는 쪽(SL-3c)은 **어댑터 결과(엄격 검사 UDF 를 거친 중간 결과)를 persist** 해 둔다 — Bronze 배치만 persist 하면
    이 확인의 부분 질의마다, 그리고 뒤의 쓰기마다 어댑터 계산과 UDF 가 다시 돈다. 배치 비용의 UDF 단계 시간도 그 persist 를 기준으로 잰다."""
    found = defects(bronze, ok, errors, domain).limit(examples).collect()
    if found:
        raise AdapterContractError("Silver 배치를 멈춘다 — 어댑터 결과 확인에서 결함: "
                                   + "; ".join(f"{row.topic}/{row.partition}/{row.offset}: {row.defect}" for row in found))
    return ok, errors
