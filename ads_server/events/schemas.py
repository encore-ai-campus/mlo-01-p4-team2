"""C2 이벤트의 모양(DRD §5.6). **이벤트 필드 이름과 형식은 이 파일에서만 정한다** — 호출하는 쪽(records·billing·masters·campaigns)은 값을 넘기기만 한다(ORM 객체를 넘기지 않는다 —
events 는 foundation 만 알고, 객체를 받으면 import 없이도 위 모듈의 모델 모양에 묶인다, 계획 docs/plans/00 D1).

공통 봉투는 `event_id`·`event_name`·`schema_version`·`produced_at`·`payload` 이고, 여기서는 `payload` 를 만든다.
JSON 이므로 UUID 는 문자열, 시각은 UTC ISO 8601(`records/basis.py` 와 같은 형식)이다. 모양이 바뀌면 **그 종류의** `schema_version` 을 올린다(종류별, 6-10c).
"""
import uuid
from datetime import datetime, timezone

DECISION = "ad.decision"
REACTION_RECEIVED = "ad.reaction_received"
REACTION = "ad.reaction"
CHARGE = "ad.charge"
BID_POLICY_CHANGED = "ad.bid_policy_changed"
BID_ADDED = "ad.bid_added"
CAMPAIGN_CHANGED = "ad.campaign_changed"
AD_GROUP_CHANGED = "ad.ad_group_changed"
SEGMENT_CHANGED = "ad.segment_changed"
SCHEMA_VERSIONS = {  # 지금 만드는 판. ad.decision 2: selected.pricing_unit → charge_shares(6-10c), 3: 대상 그룹 넷(TG-S3)
    DECISION: 3, REACTION_RECEIVED: 1, REACTION: 1, CHARGE: 1,
    BID_POLICY_CHANGED: 1, BID_ADDED: 1, CAMPAIGN_CHANGED: 1, AD_GROUP_CHANGED: 1, SEGMENT_CHANGED: 1,
}
DECISION_BEFORE_SEGMENTS = 2  # 대상 그룹 규칙 이전 결정(`segment_rule` 없음)의 판 — 원본 재구성·백필이 그 행을 이 판으로 만든다(DRD §5.6 v3, 행마다 판)


def schema_version_of(event_name, payload):
    """봉투의 `schema_version`. `ad.decision` 은 **행마다** 고른다 — 페이로드에 대상 그룹 규칙(`segment_rule`)이 있으면 v3, 없으면(이 기능 이전 결정) v2.
    outbox 쓰기·원본 재구성·검사기가 모두 이것을 쓴다(outbox 에 v2 로 저장된 옛 결정과 구간 대사가 어긋나지 않게)."""
    if event_name == DECISION and payload.get("segment_rule") is None:
        return DECISION_BEFORE_SEGMENTS
    return SCHEMA_VERSIONS[event_name]

_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "events.ads-server")


def event_id_of(event_name, source_id):
    """원천 행의 ID(결정·수신함·원장 항목)와 이벤트 종류에서 결정적으로 만든 이벤트 ID. 재처리·재전송에도 같은 값이다."""
    return uuid.uuid5(_NAMESPACE, f"{event_name}/{source_id}")


def _id(value):
    return None if value is None else str(value)


def _iso(value):
    return None if value is None else value.astimezone(timezone.utc).isoformat()


def decision_payload(*, decision_id, media_id, slot_id, decided_at, audience, subject_id, selected, selection_policy, candidate_count, rejected_counts,
                     segment_rule=None, segment_id=None, segment_active=None):
    """`selected` 는 광고가 없으면 `None`, 아니면 선택된 광고의 스냅샷(dict). 광고주 계정(owner)은 싣지 않는다 — 파이프라인에 필요 없고 개인 연결 위험만 늘린다.

    `segment_rule` 이 있으면 v3 모양이다: 최상위 `segment_id`·`segment_active`·`segment_rule` 과 `selected.target_segment_ids`(정렬 — 창 내용 해시가 배열 순서를
    그대로 본다). 그룹 코드·이름은 싣지 않는다(`ad.segment_changed` 로 차원을 만든다). 없으면(이 기능 이전 결정) v2 모양 그대로다(`schema_version_of`)."""
    payload = {
        "decision_id": _id(decision_id), "media_id": _id(media_id), "slot_id": _id(slot_id), "decided_at": _iso(decided_at),
        "audience": audience, "subject_id": subject_id,
        "selected": None if selected is None else {
            "campaign_id": _id(selected["campaign_id"]), "ad_group_id": _id(selected["ad_group_id"]), "creative_id": _id(selected["creative_id"]),
            "charge_shares": dict(selected["charge_shares"]), "bid_id": _id(selected["bid_id"]), "bid_version": selected["bid_version"],
            "bid_policy_id": _id(selected["bid_policy_id"]), "policy_version": selected["policy_version"],
            "bid_amount": selected["bid_amount"], "applied_amount": selected["applied_amount"],
        },
        "selection_policy": selection_policy, "candidate_count": candidate_count, "rejected_counts": rejected_counts,
    }
    if segment_rule is not None:
        payload.update(segment_id=_id(segment_id), segment_active=segment_active, segment_rule=segment_rule)
        if selected is not None:
            payload["selected"]["target_segment_ids"] = [_id(s) for s in sorted(selected["target_segment_ids"])]
    return payload


def reaction_received_payload(*, inbox_id, reaction_id, media_id, decision_id, type, occurred_at, received_at, subject_id, source, raw):
    """접수: **보고된 사실만**. 판정 단위의 사람·슬롯 성격은 싣지 않는다(접수는 판정이 아니다)."""
    return {
        "inbox_id": _id(inbox_id), "reaction_id": _id(reaction_id), "media_id": _id(media_id), "decision_id": _id(decision_id),
        "type": type, "occurred_at": _iso(occurred_at), "received_at": _iso(received_at), "subject_id": subject_id, "source": source, "raw": raw,
    }


def reaction_payload(*, inbox_id, reaction_id, media_id, decision_id, type, occurred_at, received_at, subject_id, source, status, reason_code, applied_rule, basis):
    """판정: 확인한 사실. 판정 단위의 사람(`person_id`)·슬롯 성격(`audience`)은 **`basis` 에서 읽는다**(판정 시점의 스냅샷이 유일한 원본)."""
    return {
        "inbox_id": _id(inbox_id), "reaction_id": _id(reaction_id), "media_id": _id(media_id), "decision_id": _id(decision_id),
        "type": type, "occurred_at": _iso(occurred_at), "received_at": _iso(received_at),
        "subject_id": subject_id, "person_id": basis.get("person"), "source": source, "audience": basis.get("audience"),
        "status": status, "reason_code": reason_code, "applied_rule": applied_rule, "basis": basis,
    }


def charge_payload(*, entry_id, campaign_id, charge_policy, created_at, reaction_ref, decision_id, requested_amount, charged_amount, shortfall, budget_day):
    """과금: 원장 `CHARGE` 한 줄. 지갑 잔액·광고주 계정 정보는 싣지 않는다."""
    return {
        "entry_id": _id(entry_id), "campaign_id": _id(campaign_id), "charge_policy": charge_policy, "created_at": _iso(created_at),
        "reaction_ref": reaction_ref, "decision_id": _id(decision_id), "requested_amount": requested_amount, "charged_amount": charged_amount,
        "shortfall": shortfall, "budget_day": budget_day.isoformat(),
    }


def bid_policy_changed_payload(*, slot_id, bid_policy_id, policy_version, floor_amount, base_amount, cap_amount, impression_share, click_share, conversion_share, changed_at):
    """입찰 정책 버전 하나(추가 전용 — 버전마다 이벤트 하나). 변경 사유·바꾼 관리자는 싣지 않는다(관리자 자유문·사람 연결).
    `change_source` 는 여기서만 정한다: 첫 버전(슬롯 생성)은 `INITIAL`, 그 밖에는 `MANUAL`. `insight_id` 는 지금 항상 `null` 이다 —
    가격 추천을 붙일 때 이 함수가 `insight_id` 를 받아 `RECOMMENDATION` 을 가른다(계획 docs/plans/00 §8.2, 그때도 v1 그대로)."""
    return {
        "slot_id": _id(slot_id), "bid_policy_id": _id(bid_policy_id), "policy_version": policy_version,
        "floor_amount": floor_amount, "base_amount": base_amount, "cap_amount": cap_amount,
        "impression_share": impression_share, "click_share": click_share, "conversion_share": conversion_share,
        "changed_at": _iso(changed_at), "change_source": "INITIAL" if policy_version == 1 else "MANUAL", "insight_id": None,
    }


def bid_added_payload(*, bid_id, ad_group_id, bid_version, amount, created_at):
    """광고그룹 입찰 버전 하나(추가 전용)."""
    return {"bid_id": _id(bid_id), "ad_group_id": _id(ad_group_id), "bid_version": bid_version, "amount": amount, "created_at": _iso(created_at)}


def change_value(**fields):
    """변경 로그·변경 이벤트의 값(`previous_value`·`new_value`) — 바뀐 업무 필드만. **형식은 여기서만 정한다**: UUID → 문자열, 시각 → UTC ISO(`_iso`, 마이크로초),
    UUID 목록 → 정렬한 문자열 목록, 정수·불리언·문자열·`None` 은 그대로. 그 밖의 형은 받지 않는다(조용히 바꾸지 않는다). 로그를 쓰는 쪽(campaigns)이 결과를
    저장하고 같은 값을 이벤트에 넘긴다 — JSONField 에 시각을 그대로 넣으면 저장 형식(밀리초·Z)이 이벤트의 다른 시각 필드와 갈린다."""
    out = {}
    for name, value in fields.items():
        if isinstance(value, uuid.UUID):
            out[name] = str(value)
        elif isinstance(value, datetime):
            if value.tzinfo is None:  # astimezone 은 서버 지역 시각으로 가정해 다른 순간을 만든다
                raise TypeError(f"변경 값 {name} 은 시간대가 있는 시각이어야 한다")
            out[name] = _iso(value)
        elif isinstance(value, (list, tuple, set, frozenset)) and all(isinstance(v, uuid.UUID) for v in value):
            out[name] = sorted(str(v) for v in value)
        elif isinstance(value, str):
            out[name] = str(value)  # 선택지 값(TextChoices)도 그냥 문자열로
        elif value is None or isinstance(value, (bool, int)):
            out[name] = value
        else:
            raise TypeError(f"변경 값 {name} 의 형({type(value).__name__})은 받지 않는다")
    return out


def campaign_changed_payload(*, campaign_id, change_id, change_type, previous_value, new_value, changed_at):
    """캠페인 변경 로그 한 줄. `previous_value`·`new_value` 는 변경 로그에 저장한 JSON 그대로다(바뀐 업무 필드만 — 만드는 쪽이 이미 JSON 값으로 만든다).
    중단 사유·캠페인 이름은 싣지 않는다."""
    return {
        "campaign_id": _id(campaign_id), "change_id": _id(change_id), "change_type": change_type,
        "previous_value": previous_value, "new_value": new_value, "changed_at": _iso(changed_at),
    }


def ad_group_changed_payload(*, ad_group_id, campaign_id, change_id, change_type, previous_value, new_value, changed_at):
    """광고그룹 변경 로그 한 줄(값은 `campaign_changed_payload` 와 같은 규칙). 광고그룹 이름은 싣지 않는다."""
    return {
        "ad_group_id": _id(ad_group_id), "campaign_id": _id(campaign_id), "change_id": _id(change_id), "change_type": change_type,
        "previous_value": previous_value, "new_value": new_value, "changed_at": _iso(changed_at),
    }


def segment_changed_payload(*, segment_id, segment_version_id, version, media_id, code, name, is_active, changed_at):
    """대상 그룹 판 하나(추가 전용 — 판마다 이벤트 하나, 등록의 첫 판 포함). 코드는 매체가 보내는 값 그대로(분석이 매체 쪽 자료와 그룹으로 잇는 열쇠),
    이름은 관리자가 정한 표시용 이름이라 싣는다(그룹 차원). 바꾼 관리자는 싣지 않는다(사람 연결, DRD §5.6)."""
    return {
        "segment_id": _id(segment_id), "segment_version_id": _id(segment_version_id), "version": version, "media_id": _id(media_id),
        "code": code, "name": name, "is_active": is_active, "changed_at": _iso(changed_at),
    }
