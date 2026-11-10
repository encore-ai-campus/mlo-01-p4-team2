"""대상 그룹 필터(필수, 설계 §10 "대상 그룹 타기팅" ⑤·⑥). 광고그룹의 대상 그룹 T 가 비면 전체가 대상이다.
아니면 요청 그룹 r 이 있고, 살아 있고(보관된 r 은 "그룹 없음"과 같다), T 안에 있을 때만 통과한다. 일괄 슬롯은 T 가 있는 광고를 내지 않는다
(보는 사람마다 따로 과금되므로 — C1 은 일괄 슬롯 요청의 그룹을 받지 않지만 여기서도 슬롯 성격으로 막는다)."""
from ..types import AUDIENCE_SHARED

RULE = "segment/v1"  # 결정에 남기는 규칙 표시(DRD R1 `segment_rule`, `ad.decision` v3) — 맞춤 규칙을 바꾸면 이 이름도 바꾼다


def check(candidate, effect, request):
    targets = candidate.target_segment_ids
    if not targets:
        return None
    if request.audience == AUDIENCE_SHARED or not request.segment_active or request.segment_id not in targets:
        return "segment_not_targeted"
    return None
