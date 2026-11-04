"""엄격 JSON 검사(계획 15 §2.1, SL-3 결정 1 (나)) — 표준 라이브러리만 쓴다. Spark 에서는 `contracts.strict_reasons` 가 이 함수를 Python UDF 로 부른다
(검사만 하고 오류 사유를 돌려준다. 값 꺼내기·변환은 Spark 내장 식). executor 는 zip 으로 받은 이 파일을 import 한다.

옮긴 곳: 메타버스 Step2 `pipeline/legacy/metaverse/Step2/implementation/pipeline/contracts.py`(p4 df22732, 출처 metaverse c0d9d3f)의
`InvalidRecord`(:26-29)·`_unique_object`(:52-58)·`_invalid_constant`(:61-62)·`decode_json`(:65-82). 바꾼 점: 오류 코드는 SV7 의 `decode` 하나,
바이트뿐 아니라 문자열도 받는다(Debezium 봉투 안의 JSON 문자열 `raw_data`·`payload_json`·`payload`).

거부하는 것: 잘못된 UTF-8, 겹친 키(어느 깊이든), NaN·Infinity·-Infinity, 위로 넘치는 수(1e999 → inf), 짝 없는 서로게이트, 최상위가 객체가 아님,
JSON 이 아님(뒤따르는 내용 포함), 4,300자리를 넘는 정수(Python 정수 변환 한도 — `decode` 오류).
**거부하지 않는 것**: 실수는 Python float(IEEE binary64)로 읽힌다. 아래로 넘치는 수(1e-400, jsonb 가 정규화한 0.000…1)는 0.0 이 되고,
유효숫자 17자리를 넘는 차이(0.12345678901234567890 과 …891)는 같은 값이 된다(2026-11-04 실측). 원문은 payload·envelope 에 그대로 남는다.
내용 해시(공용 규칙 hop_rules.canonical_json·after_identity — RFC 8785/JCS)도 수를 binary64 로 보므로 그런 차이만 있는 두 사본은 conflict 가
아니라 copies 가 된다. 이 검사만 바꿔서는 해시 판정이 바뀌지 않는다 — 바꾸려면 공용 hop_rules(광고 서버 원본 + 사본, rule_sha256)를 함께
바꿔야 하므로 MVR-1 과 같은 자리의 결정이다. **Planner 결정 2026-11-04: 바꾸지 않음 — JCS double 동치, hop_rules 와 같은 해석.**
"""
import json


class InvalidRecord(ValueError):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidRecord("decode", "JSON 키가 겹친다: " + key)
        result[key] = value
    return result


def _invalid_constant(value):
    raise InvalidRecord("decode", "JSON 에 유한하지 않은 수가 있다: " + value)


def decode_json(value, field):
    """UTF-8·JSON 엄격 해석. 어긋나면 `InvalidRecord("decode")`. 반환: (원문 문자열, 해석한 객체)."""
    if isinstance(value, (bytes, bytearray)):
        try:
            text = bytes(value).decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise InvalidRecord("decode", field + " 이(가) 올바른 UTF-8 이 아니다") from error
    elif isinstance(value, str):
        text = value
    else:
        raise InvalidRecord("decode", field + " 이(가) JSON 바이트·문자열이 아니다")
    try:
        parsed = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        json.dumps(parsed, ensure_ascii=False, allow_nan=False).encode("utf-8")  # 넘치는 수(inf)·짝 없는 서로게이트를 여기서 막는다
    except InvalidRecord as error:  # 겹친 키·유한하지 않은 수 — 어느 필드인지 붙인다
        raise InvalidRecord("decode", f"{field}: {error}") from error
    except (ValueError, RecursionError) as error:
        raise InvalidRecord("decode", field + " 이(가) JSON 문서가 아니다") from error
    if not isinstance(parsed, dict):
        raise InvalidRecord("decode", field + " 의 최상위가 JSON 객체가 아니다")
    return text, parsed


def strict_reasons(value, nested=()):
    """`value`(Bronze value 바이트 등)와 그 안의 JSON 문자열들(`nested`: 키 경로 튜플, 예 ("after", "raw_data"))을 한 번에 엄격 검사한다
    (봉투는 한 번만 해석한다). 반환: (value 사유, 안쪽 사유) — 각각 None(정상) 또는 사유 문자열. value 가 틀리면 안쪽은 보지 않는다(None).
    둘을 나눠 돌려주는 것은 어댑터가 출처·op 확인을 안쪽 검사보다 먼저 적용하게 하기 위해서다(framework 머리말의 순서 ③·④ — 다른 표의 행이
    같은 이름의 열에 JSON 이 아닌 문자열을 들고 와도 decode 가 아니라 foreign_source 로 멈춘다). 경로의 값이 문자열일 때만 검사한다
    (없거나 null·문자열이 아닌 값은 어댑터의 계약 검사가 본다). value 가 null(tombstone)이면 사유를 내지만, 어댑터는 그 전에 not_insert 로 보낸다(①)."""
    try:
        _, document = decode_json(value, "value")
    except InvalidRecord as error:
        return str(error), None
    for path in nested:
        inner = document
        for key in path:
            inner = inner.get(key) if isinstance(inner, dict) else None
        if isinstance(inner, str):
            try:
                decode_json(inner, ".".join(path))
            except InvalidRecord as error:
                return None, str(error)
    return None, None
