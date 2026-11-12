import io
import json
from urllib.error import HTTPError

from helpers import ID


def profile(ident=ID, **changes):
    return {**dict(ad_group_id=ident, version=1, category=12, weight_mlo=60, weight_aio=30,
                   weight_skn=10, changed_at='2026-11-12T03:15:20.123456Z'), **changes}


def item(ident=ID, **changes):
    return {**dict(ad_group_id=ident, weight_mlo=60, weight_aio=30, weight_skn=10), **changes}


def acknowledgement(items, result='changed'):
    return {'items': [dict(ad_group_id=x['ad_group_id'], result=result,
                           profile=profile(**x)) for x in items]}


class Response:
    status = 200
    def __init__(self, body, headers=None):
        self.body = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.headers = {'X-Request-ID': ID} if headers is None else headers
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, size):
        return self.body[:size]


class Opener:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []
    def open(self, request, timeout):
        self.requests.append(request)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def http_error(status, headers=None, problem=None):
    return HTTPError('https://secret.example/NEVER_STORE_THIS_KEY', status, 'SECRET', headers or {},
                     io.BytesIO(json.dumps(problem or {}).encode()))
