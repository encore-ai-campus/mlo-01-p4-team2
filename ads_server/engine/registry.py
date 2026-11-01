"""이름 → 구현. 선택 알고리즘과 필터는 이름으로 고르고, 알 수 없는 이름은 **즉시 오류**다(조용히 기본값으로 바꾸지 않는다).

어떤 이름을 쓸지는 호출하는 쪽(`records`)이 설정에서 읽어 넘긴다(설계 R4). 이 파일은 설정을 읽지 않는다.
"""
from .filters import balance, budget, floor, period, status
from .policies.weighted_by_bid import WeightedByBid

DEFAULT_POLICY = WeightedByBid.name
# 순서가 곧 판정 순서다. `floor` 는 적용 금액이 없는 후보(하한 미만)를 거르므로 `budget`·`balance` 보다 앞이어야 한다.
DEFAULT_FILTERS = ("status", "period", "floor", "budget", "balance")

# 등록표는 코드로만 정한다(실행 중에 바꾸지 않는다 — 모든 프로세스·스레드가 같은 표를 본다). 새 알고리즘은 `engine/policies/` 에 파일 하나 + 여기 한 줄.
_POLICIES = {WeightedByBid.name: WeightedByBid()}
_FILTERS = {"status": status.check, "period": period.check, "floor": floor.check, "budget": budget.check, "balance": balance.check}


class UnknownName(ValueError):
    pass


def get_policy(name):
    try:
        return _POLICIES[name]
    except KeyError:
        raise UnknownName(f"알 수 없는 선택 정책: {name!r} (등록된 것: {sorted(_POLICIES)})")


def get_filter(name):
    try:
        return _FILTERS[name]
    except KeyError:
        raise UnknownName(f"알 수 없는 필터: {name!r} (등록된 것: {sorted(_FILTERS)})")
