"""기존 반응 PUT의 저장 확인을 클릭 업적 조건으로 사용한다."""
from datetime import datetime, timezone
from uuid import UUID

from .api.config import AdsConfig
from .click_records import ServerClick
from .routing import RoutedAdsClient


def configured_report():
    # 잘못된 설정으로 레코드를 임대하기 전에 중단한다.
    config = AdsConfig.from_env()
    if config.timeout >= 60:
        raise ValueError('클릭 보고 타임아웃은 임대 시간 60초보다 짧아야 합니다.')
    client = RoutedAdsClient(config)

    def report(*, slot_id, decision_id, reaction_id, occurred_at, subject_id):
        # 최초 클릭 시각과 ID는 DB에서 읽는다. 재시도에도 바뀌지 않는다.
        stamp = occurred_at.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
        received = client.report_click(slot_id=slot_id, decision_id=decision_id,
                                       reaction_id=reaction_id, occurred_at=stamp, subject_id=subject_id)
        return ServerClick(decision_id=UUID(received.decision_id), subject_id=UUID(received.subject_id),
                           reaction_id=UUID(received.reaction_id),
                           occurred_at=datetime.fromisoformat(received.occurred_at),
                           received_at=datetime.fromisoformat(received.received_at),
                           source='media', type=received.type, status=received.status)
    return report
