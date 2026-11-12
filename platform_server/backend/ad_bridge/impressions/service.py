"""게임에서 받은 첫 노출 알림을 보낼 대기로 저장한다. 보내기는 백그라운드 작업자가 한다."""

from ..delivery.policy import utc_now


def queue_impression(*, store, decision_id, owner_id, occurred_at: str, now=utc_now, subject_id=None):
    """노출을 한 번만 대기로 저장하고 (기록, 새로 만들었는지)를 돌려준다. 광고 서버는 부르지 않는다."""
    # 공유 노출의 가명은 로그인 사용자를 확인한 서버가 생성해 전달한다.
    viewer = {} if subject_id is None else {"subject_id": subject_id}
    return store.reserve_impression(decision_id, owner_id, occurred_at, now=now(), **viewer)
