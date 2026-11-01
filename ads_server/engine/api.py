"""engine 의 외부 공개 면. 다른 모듈은 이 파일만 import 한다(설계 R2). 순수 파이썬이며 DB·Django 를 모른다."""
from random import Random

from .filters.chain import run_filters
from .pricing import effect_of, is_expired
from .registry import DEFAULT_POLICY, UnknownName, get_policy
from .types import AdRequest, Candidate, DecisionResult, Effect, Eligible, Selection


def decide(candidates, request: AdRequest, rng: Random, policy=DEFAULT_POLICY, extra_filters=()) -> DecisionResult:
    """적용 금액 계산 → 자격 필터(필수 다섯 + 추가) → 선택 알고리즘. 알 수 없는 정책·필터 이름은 오류다."""
    selector = get_policy(policy)  # 이름 오류는 후보가 없어도 바로 드러나게 먼저 확인한다
    candidates = list(candidates)
    for c in candidates:
        if c.slot_id != request.slot_id:
            raise ValueError("요청한 슬롯의 후보가 아니다")
    priced = [(c, effect_of(c.bid_amount, c.floor, c.cap)) for c in candidates]
    survivors, rejected = run_filters(priced, request, extra_filters)
    eligible = [Eligible.of(c, effect.applied_amount) for c, effect in survivors]  # 필수 `floor` 를 지났으니 적용 금액이 있다
    return DecisionResult(selector.select(eligible, request, rng), considered=len(candidates), rejected=rejected)


__all__ = [
    "decide", "effect_of", "is_expired", "UnknownName",
    "DEFAULT_POLICY", "AdRequest", "Candidate", "Eligible", "Effect", "Selection", "DecisionResult",
]
