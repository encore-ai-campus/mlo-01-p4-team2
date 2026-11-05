"""이름 → 과금 정책. 어떤 이름을 쓸지는 설정(`BILLING_CHARGE_POLICY`)이 정한다. 알 수 없는 이름은 즉시 오류다."""
from .share import ShareV1

_POLICIES = {ShareV1.name: ShareV1()}  # 코드로만 정한다(실행 중에 바꾸지 않는다 — 모든 프로세스·스레드가 같은 표를 본다)


class UnknownChargePolicy(ValueError):
    pass


def get_charge_policy(name):
    try:
        return _POLICIES[name]
    except KeyError:
        raise UnknownChargePolicy(f"알 수 없는 과금 정책: {name!r} (등록된 것: {sorted(_POLICIES)})")
