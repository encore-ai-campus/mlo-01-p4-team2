"""확인 작업자가 불러오는 실제 HTTP 조회 어댑터. API 준비 후 명시적으로 활성화한다."""
from django.conf import settings

from .api.config import AdsConfig
from .api.click_lookup import validate_path
from .routing import RoutedAdsClient


def _configuration():
    path = validate_path(getattr(settings, 'ADS_CLICK_LOOKUP_PATH', ''))
    config = AdsConfig.from_env()
    if config.timeout >= 60:
        raise ValueError('클릭 조회 타임아웃은 작업자 임대 시간 60초보다 짧아야 합니다.')
    return path, RoutedAdsClient(config)


def validate_configuration():
    # 명령 시작 시 검사하여 설정 누락 때문에 PENDING 기록을 BLOCKED로 바꾸지 않는다.
    _configuration()


def lookup(*, slot_id, decision_id, subject_id):
    path, client = _configuration()
    return client.get_server_click(slot_id=slot_id, path=path,
                                   decision_id=decision_id, subject_id=subject_id)


lookup.validate_configuration = validate_configuration
