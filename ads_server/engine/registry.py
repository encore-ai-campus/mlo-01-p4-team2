"""이름 → 구현. 선택 알고리즘과 추가 필터는 이름으로 고르고, 알 수 없는 이름은 **즉시 오류**다(조용히 기본값으로 바꾸지 않는다).

어떤 이름을 쓸지는 호출하는 쪽(`records`)이 설정에서 읽어 넘긴다(설계 R4). 이 파일은 설정을 읽지 않는다.
"""
from .filters import balance, budget, floor, period, status
from .policies.weighted_by_bid import WeightedByBid

DEFAULT_POLICY = WeightedByBid.name
# **언제나** 이 순서로 먼저 거치는 자격 필터(설정과 무관 — 뺄 수도 순서를 바꿀 수도 없다, 설계 §6.4). `floor` 는 적용 금액이 없는 후보(하한 미만)를 거르므로 `budget`·`balance` 보다 앞이다.
MANDATORY_FILTERS = (status.check, period.check, floor.check, budget.check, balance.check)

# 등록표는 코드로만 정한다(실행 중에 바꾸지 않는다 — 모든 프로세스·스레드가 같은 표를 본다). 새 알고리즘은 `engine/policies/` 에 파일 하나 + 여기 한 줄.
_POLICIES = {WeightedByBid.name: WeightedByBid()}
# 설정으로 켜는 추가 필터: 이름 → check. 필수 다섯 뒤에 설정 순서대로 돈다. 새 필터(예: 타겟팅)는 `engine/filters/` 에 파일 하나 + 여기 한 줄 + 설정(설계 §6.4).
_FILTERS = {}


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
        raise UnknownName(f"알 수 없는 추가 필터: {name!r} (등록된 것: {sorted(_FILTERS)} — 필수 필터는 설정에 적지 않는다)")
