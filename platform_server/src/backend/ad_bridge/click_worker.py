"""클릭 보고·저장 확인 작업자. 미래 서버 관측 조회는 명시적으로 선택한다."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.module_loading import import_string

from .api.errors import AdsHTTPError, AdsResponseError, AdsTransportError
from .click_records import claim_click, confirm_click, defer_click


def configured_lookup():
    path = getattr(settings, 'ADS_CLICK_LOOKUP_BACKEND', '')
    if not path:
        raise ValueError('ADS_CLICK_LOOKUP_BACKEND 설정과 광고 서버 클릭 조회 API가 필요합니다.')
    lookup = import_string(path)
    if not callable(lookup):
        raise ValueError('클릭 조회 어댑터는 호출 가능한 함수여야 합니다.')
    validate = getattr(lookup, 'validate_configuration', None)
    if validate is not None:
        validate()
    return lookup


def check_pending_clicks(*, report=None, lookup=None, limit=100, now=timezone.now, award=None, record_id=None):
    if type(limit) is not int or limit < 1:
        raise ValueError('limit은 양의 정수여야 합니다.')
    if (report is None) == (lookup is None):
        raise ValueError('보고 또는 조회 함수 하나를 지정해 주세요.')
    counts = {'checked': 0, 'confirmed': 0, 'pending': 0, 'blocked': 0}
    for _ in range(limit):
        record = claim_click(now=now(), record_id=record_id)
        if record is None:
            break
        counts['checked'] += 1
        try:
            # 발급 슬롯의 키와 저장된 ID·사용자·시각을 그대로 재사용한다.
            request = dict(slot_id=record.decision.slot_id, decision_id=record.decision_id,
                           subject_id=record.subject_id)
            if report is not None:
                receipt = report(**request, reaction_id=record.pk, occurred_at=record.clicked_at)
            else:
                receipt = lookup(**request)
            if receipt is None:
                defer_click(record, now=now(), error='not_observed')
                counts['pending'] += 1
            elif confirm_click(record, receipt, now=now(), award=award, source='media' if report is not None else 'server'):
                counts['confirmed'] += 1
        except AdsHTTPError as error:
            retryable = (429,) if report is not None else (404, 429)
            blocked = error.status not in retryable and not 500 <= error.status <= 599
            defer_click(record, now=now(), error='report_http_error' if report is not None else 'lookup_http_error', blocked=blocked,
                        retry_after=error.retry_after or 0)
            counts['blocked' if blocked else 'pending'] += 1
        except (AdsTransportError, AdsResponseError):
            defer_click(record, now=now(), error='report_unavailable' if report is not None else 'lookup_unavailable')
            counts['pending'] += 1
        except (ValueError, get_user_model().DoesNotExist):
            defer_click(record, now=now(), error='invalid_confirmation', blocked=True)
            counts['blocked'] += 1
    return counts
