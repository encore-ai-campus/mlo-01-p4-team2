"""발급한 광고의 이동 주소를 로그인 계정에 묶어 서명한다."""
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from django.core import signing


def click_token(decision_id, url, owner_id):
    return signing.dumps({'decision': str(decision_id), 'url': url}, salt=f'ad-bridge.click.v1:{owner_id}')


def click_target(token, decision_id, owner_id):
    # 표시 중인 이전 광고의 링크도 유지하므로 별도 만료 시간을 두지 않는다.
    data = signing.loads(token, salt=f'ad-bridge.click.v1:{owner_id}')
    if not isinstance(data, dict) or data.get('decision') != str(decision_id):
        raise signing.BadSignature('광고 결정이 일치하지 않습니다.')
    url = data.get('url')
    if not isinstance(url, str):
        raise signing.BadSignature('광고 주소를 확인해 주세요.')
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise signing.BadSignature('광고 주소를 확인해 주세요.')
    return url


def shared_click_subject(url):
    """이미 서명 검증한 shared 클릭 URL에서 발급 당시 가명을 읽는다."""
    values = parse_qs(urlsplit(url).query, keep_blank_values=True).get('s', [])
    if len(values) != 1:
        raise ValueError('공유 광고의 클릭 사용자를 확인할 수 없습니다.')
    return UUID(values[0])
