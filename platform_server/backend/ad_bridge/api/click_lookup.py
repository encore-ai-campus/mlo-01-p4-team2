"""미확정 클릭 조회 계약의 HTTP 경계. 실제 서버에 존재하는 API로 간주하지 않는다."""
import json
import re
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request
from uuid import UUID

from .client import _MAX_RESPONSE_BYTES, _utc, _uuid
from .errors import AdsResponseError, AdsTransportError
from ..click_records import ServerClick


def validate_path(value):
    # base_url 아래의 상대 경로만 허용한다. 인증 키를 임의 호스트로 보내지 않는다.
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*/?', value):
        raise ValueError('ADS_CLICK_LOOKUP_PATH에 확정된 상대 조회 경로가 필요합니다.')
    return value


def parse_click(body, *, decision_id, subject_id):
    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError
        decision = UUID(_uuid(data['decision_id'], 'decision_id'))
        subject = UUID(_uuid(data['subject_id'], 'subject_id'))
        if decision != UUID(str(decision_id)) or subject != UUID(str(subject_id)):
            raise ValueError
        if data['source'] != 'server' or data['type'] != 'CLICK':
            raise ValueError
        if data['status'] not in ('RECEIVED', 'ACCEPTED', 'REJECTED'):
            raise ValueError
        occurred = _utc(data['occurred_at'], 'occurred_at')
        received = _utc(data['received_at'], 'received_at')
        if received < occurred:
            raise ValueError
        return ServerClick(decision_id=decision, subject_id=subject,
                           reaction_id=UUID(_uuid(data['reaction_id'], 'reaction_id')),
                           occurred_at=occurred, received_at=received,
                           source=data['source'], type=data['type'], status=data['status'])
    except (ValueError, KeyError, TypeError, RecursionError):
        raise AdsResponseError('광고 서버 클릭 조회 응답을 검증하지 못했습니다.') from None


def fetch_click(client, *, path, decision_id, subject_id):
    path = validate_path(path)
    query = urlencode({'decision_id': _uuid(decision_id, 'decision_id'),
                       'subject_id': _uuid(subject_id, 'subject_id')})
    request = Request(f'{client._config.base_url}/{path}?{query}', method='GET',
                      headers={'X-Media-Key': client._config.media_key, 'Accept': 'application/json'})
    try:
        with client._opener.open(request, timeout=client._config.timeout) as response:
            if response.status == 404:
                return None
            if response.status != 200 or response.headers.get_content_type() != 'application/json':
                raise AdsResponseError('클릭 조회는 HTTP 200 JSON 응답이어야 합니다.')
            body = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(body) > _MAX_RESPONSE_BYTES:
                raise AdsResponseError('클릭 조회 응답이 크기 제한을 넘었습니다.')
            return parse_click(body, decision_id=decision_id, subject_id=subject_id)
    except HTTPError as error:
        with error:
            if error.code == 404:
                return None
            failure = client._http_error(error)
        raise failure from None
    except (URLError, OSError, HTTPException):
        raise AdsTransportError('광고 서버 클릭 조회 연결 또는 수신에 실패했습니다.') from None
