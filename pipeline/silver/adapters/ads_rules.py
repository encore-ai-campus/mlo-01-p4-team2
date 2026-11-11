"""광고 outbox 사건의 계약 확인·내용 해시·tombstone ID(SL-8 계획 3판 §2.2·§2.3, 결정 C·D). 표준 라이브러리와 pipeline.common 만 쓴다 —
Spark 없이 시험하고, executor 가 어댑터의 Python UDF(`adapter_result`)에서 import 한다.

(종류, 판) 표 `CONTRACTS` 는 광고 서버 `ads_server/events/schemas.py`(빌더가 만드는 페이로드 최상위 키, `SCHEMA_VERSIONS`·`DECISION_BEFORE_SEGMENTS`)와
`api.py`(`enqueue` 셋째 인자 = 대상 키)를 옮겼다. 시험이 둘과 같음을 본다(빌더를 불러 키 집합, api.py 를 ast 로 읽어 대상 키 — 리뷰 조건 C4).
- 계약(결정 C (가)): 페이로드 최상위 키 집합이 그 줄과 정확히 같다, `after.aggregate_id` = 페이로드의 대상 키. 꺼내는 열의 형은 어댑터가 본다(contracts.field).
  중첩 값(`selected`·`basis`·`raw`·`previous_value`·`new_value`)의 모양·선택지 값·event_id 유도는 보지 않는다(계획 §7 5).
- `ad.decision` 은 판이 행마다다(schemas.schema_version_of — `segment_rule` 이 있으면 3, 없으면 2). 두 판은 키 집합이 달라 모양과 판 값이 엇갈리면 계약 오류다.
- 내용 해시(결정 D (가)): sha256(hop_rules.after_identity(after)) — hop 잡(hop_manifest_job: value 문자열을 json.loads 한 after)과 같은 함수·같은 입력.
- tombstone 의 event_id: hop_rules._key_event_id(key)(소문자로 바꿈 — 리뷰 조건 C3)가 소문자 UUID 일 때만.
- 어떤 입력에도 예외를 내지 않는다(리뷰 조건 C1): 공용 hop_rules 를 부르기 전에 모양을 보고, 그래도 나는 예외는 잡아 사유로 돌린다(공용 파일은 그대로).
사유에는 키 이름만 적고 값은 적지 않는다.
"""
import hashlib
import json
import re

from pipeline.common import hop_rules

_DECISION_V2 = frozenset({"audience", "candidate_count", "decided_at", "decision_id", "media_id", "rejected_counts", "selected", "selection_policy",
                          "slot_id", "subject_id"})
CONTRACTS = {  # (종류, 판) → (페이로드 최상위 키, 대상 키, 업무 시각 필드 — Silver occurred_at, 계획 §2.2 대응 표)
    ("ad.decision", 2): (_DECISION_V2, "decision_id", "decided_at"),
    ("ad.decision", 3): (_DECISION_V2 | {"segment_active", "segment_id", "segment_rule"}, "decision_id", "decided_at"),
    ("ad.reaction_received", 1): (frozenset({"decision_id", "inbox_id", "media_id", "occurred_at", "raw", "reaction_id", "received_at", "source",
                                             "subject_id", "type"}), "decision_id", "received_at"),  # 결정 A (가) — DRD "분석의 기준"
    ("ad.reaction", 1): (frozenset({"applied_rule", "audience", "basis", "decision_id", "inbox_id", "media_id", "occurred_at", "person_id", "reaction_id",
                                    "reason_code", "received_at", "source", "status", "subject_id", "type"}), "decision_id", "received_at"),
    ("ad.charge", 1): (frozenset({"budget_day", "campaign_id", "charge_policy", "charged_amount", "created_at", "decision_id", "entry_id", "reaction_ref",
                                  "requested_amount", "shortfall"}), "decision_id", "created_at"),  # 원장 시각
    ("ad.bid_policy_changed", 1): (frozenset({"base_amount", "bid_policy_id", "cap_amount", "change_source", "changed_at", "click_share",
                                              "conversion_share", "floor_amount", "impression_share", "insight_id", "policy_version", "slot_id"}),
                                   "slot_id", "changed_at"),
    ("ad.bid_added", 1): (frozenset({"ad_group_id", "amount", "bid_id", "bid_version", "created_at"}), "ad_group_id", "created_at"),
    ("ad.campaign_changed", 1): (frozenset({"campaign_id", "change_id", "change_type", "changed_at", "new_value", "previous_value"}),
                                 "campaign_id", "changed_at"),
    ("ad.ad_group_changed", 1): (frozenset({"ad_group_id", "campaign_id", "change_id", "change_type", "changed_at", "new_value", "previous_value"}),
                                 "ad_group_id", "changed_at"),
    ("ad.segment_changed", 1): (frozenset({"changed_at", "code", "is_active", "media_id", "name", "segment_id", "segment_version_id", "version"}),
                                "segment_id", "changed_at"),
    # 광고 카테고리·과정 가중치의 판 하나(ACW-1·ACW-3, 기획 63 §4·§5) — 원천 ID 는 "<ad_group_id>/<version>"(페이로드 필드가 아님)
    ("ad.ad_group_profile_changed", 1): (frozenset({"ad_group_id", "category", "changed_at", "version", "weight_aio", "weight_mlo", "weight_skn"}),
                                         "ad_group_id", "changed_at"),
}
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")  # contracts.UUID_RE 와 같은 모양(fullmatch)


def contract_reason(event_name, schema_version, aggregate_id, payload):
    """outbox 한 행(after 의 값 — payload 는 해석한 값)의 계약. 반환: None(맞음) 또는 사유."""
    line = CONTRACTS.get((event_name, schema_version)) if type(schema_version) is int else None  # True·2.0 은 판이 아니다
    if line is None:
        return "계약 표에 없는 (종류, 판)"
    keys, target, _ = line
    if not isinstance(payload, dict):
        return "payload 가 객체가 아니다"
    if set(payload) != keys:
        return f"payload 필드가 계약과 다르다(빠짐 {json.dumps(sorted(keys - set(payload)))}, 더 있음 {json.dumps(sorted(set(payload) - keys), ensure_ascii=False)})"
    if aggregate_id != payload[target]:
        return f"aggregate_id 가 payload.{target} 와 다르다"
    return None


def key_event_id(key):
    """tombstone 오류 행의 event_id: 메시지 키의 event_id(hop 과 같은 함수 — 소문자로 바꿈)가 소문자 UUID 일 때만, 아니면 None."""
    if not isinstance(key, str):
        return None
    try:
        found = hop_rules._key_event_id(key)
    except (RecursionError, TypeError, ValueError):  # 아주 깊은 중첩 키 등 — 모르는 ID 는 null(D1)
        return None
    return found if isinstance(found, str) and _UUID.fullmatch(found) else None


def adapter_result(value, key):
    """어댑터 UDF 의 몸통. `value`·`key`: Bronze value_binary·key_binary 를 문자열로 바꾼 것(hop 잡과 같은 입력).
    반환: (계약 사유 또는 None, content_sha256 또는 None, tombstone 이면 키의 event_id 또는 None). 사유가 있으면 해시는 None.
    Spark 는 조건식 안의 Python UDF 도 모든 행에 부르므로, 앞 순서에서 이미 오류로 간 행(tombstone·JSON 아님·after 없음)도 여기 온다."""
    if value is None:
        return "value 가 없다", None, key_event_id(key)
    try:
        document = json.loads(value) if isinstance(value, str) else None
        after = document.get("after") if isinstance(document, dict) else None
        if not isinstance(after, dict) or not isinstance(after.get("payload"), str):
            return "after·payload 가 없다", None, None
        reason = contract_reason(after.get("event_name"), after.get("schema_version"), after.get("aggregate_id"), json.loads(after["payload"]))
        if reason is not None:
            return reason, None, None
        return None, hashlib.sha256(hop_rules.after_identity(after).encode("utf-8")).hexdigest(), None
    except (RecursionError, TypeError, ValueError) as error:  # JSON 아님·NaN(엄격 검사가 먼저 decode 로 보냄)·정규화할 수 없는 값
        return f"값을 해석하지 못했다: {type(error).__name__}", None, None
