"""프록시와 리다이렉트를 사용하지 않는 JSON 전송."""
import json
from dataclasses import dataclass, field
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .core import Failure, UUID_PATTERN, validate_url

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict = field(repr=False)
    body: bytes = field(repr=False)

    @property
    def request_id(self):
        value = self.headers.get("x-request-id")
        return value if isinstance(value, str) and UUID_PATTERN.fullmatch(value) else None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        raise Failure("INVALID_JSON") from None


def request_json(method, url, payload=None, *, headers=None, timeout=30):
    validate_url(url)
    data = None if payload is None else json_bytes(payload)
    try:
        request = Request(url, data=data, headers={"Accept": "application/json", **(headers or {})}, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        opener = build_opener(ProxyHandler({}), NoRedirect())
        try:
            response = opener.open(request, timeout=timeout)
        except HTTPError as error:
            response = error
        with response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            response_headers = {name.lower(): value for name, value in response.headers.items()}
            if len(body) > MAX_RESPONSE_BYTES:
                raise Failure("HTTP_RESPONSE_TOO_LARGE", request_id=response_headers.get("x-request-id"))
            return Response(response.code, response_headers, body)
    except (URLError, OSError, TimeoutError, HTTPException, ValueError):
        raise Failure("HTTP_TRANSPORT_ERROR") from None
