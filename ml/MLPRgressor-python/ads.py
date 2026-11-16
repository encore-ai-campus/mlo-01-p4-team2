"""Single-attempt C8 client. Retry policy belongs to the CLI runner."""
from datetime import datetime, timezone
from http.client import HTTPException
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from data import COURSES, uuid_text
from errors import JobError

WEIGHT_FIELDS = tuple(f'weight_{c}' for c in COURSES)
MAX_RESPONSE = 4 * 1024 * 1024
UTC_STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z')
POINTER = re.compile(r'/(?:state|after|limit|items(?:/[0-9]{1,3}(?:/(?:ad_group_id|weight_mlo|weight_aio|weight_skn))?)?)')
PROBLEM_CODES = {'unauthorized', 'forbidden', 'invalid_request', 'payload_too_large', 'unsupported_media_type', 'unavailable'}


def decode_json(raw):
    def reject(value):
        raise ValueError('nonstandard_json_number')
    return json.loads(raw, parse_constant=reject)


def check_weights(item):
    values = [item.get(name) for name in WEIGHT_FIELDS]
    if any(type(v) is not int or not 0 <= v <= 100 for v in values) or sum(values) > 100:
        raise JobError('invalid_api_weights')


def check_profile(profile, ident):
    try:
        if not isinstance(profile, dict) or uuid_text(profile.get('ad_group_id')) != ident:
            raise ValueError('identity')
        category = profile['category']
        stamp = profile['changed_at']
        if (type(profile.get('version')) is not int or profile['version'] < 1
                or (category is not None and (type(category) is not int or not 0 <= category <= 255))
                or not isinstance(stamp, str) or not UTC_STAMP.fullmatch(stamp)):
            raise ValueError('values')
        datetime.fromisoformat(stamp)
        check_weights(profile)
    except (KeyError, ValueError, TypeError):
        raise JobError('invalid_api_profile') from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AdsClient:
    def __init__(self, settings, *, expires_at=None, now=None):
        self.settings = settings
        self.expires_at = expires_at
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.opener = build_opener(NoRedirect())
        self.request_ids = []

    def request(self, method, path, body=None):
        if self.expires_at is not None and self.now() >= self.expires_at:
            raise JobError('target_date_expired', deferred=True)
        raw = None if body is None else json.dumps(body, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if raw is not None and len(raw) > 65536:
            raise JobError('request_exceeds_64kib')
        request = Request(self.settings.ads_base_url + path, data=raw, method=method,
                          headers={'X-Internal-Key': self.settings.ads_key, 'Content-Type': 'application/json'})
        request_id = None
        try:
            with self.opener.open(request, timeout=self.settings.http_timeout) as response:
                request_id = self.remember_id(response.headers)
                if response.status != 200:
                    raise JobError('unexpected_api_status', details={'http_status': response.status}, request_id=request_id)
                if request_id is None:
                    raise JobError('invalid_api_request_id')
                length = response.headers.get('Content-Length')
                if length is not None:
                    if not re.fullmatch(r'[0-9]+', length):
                        raise JobError('invalid_content_length', request_id=request_id)
                    try:
                        length = int(length)
                    except ValueError:
                        raise JobError('invalid_content_length', request_id=request_id) from None
                    if length > MAX_RESPONSE:
                        raise JobError('api_response_too_large', request_id=request_id)
                raw = response.read(MAX_RESPONSE + 1)
                if length is not None and len(raw) < length:
                    raise JobError('api_connection_failed', retryable=True, request_id=request_id)
                if len(raw) > MAX_RESPONSE:
                    raise JobError('api_response_too_large', request_id=request_id)
                try:
                    return decode_json(raw)
                except (ValueError, TypeError, UnicodeError):
                    raise JobError('invalid_api_json', request_id=request_id) from None
        except HTTPError as error:
            request_id = self.remember_id(error.headers)
            try:
                if error.code == 503:
                    delay = error.headers.get('Retry-After', '')
                    if not re.fullmatch(r'[0-9]+', delay):
                        raise JobError('invalid_retry_after', request_id=request_id)
                    try:
                        delay = int(delay)
                    except ValueError:
                        raise JobError('invalid_retry_after', request_id=request_id) from None
                    raise JobError('api_unavailable', retryable=True, retry_after=delay, request_id=request_id)
                details = {'http_status': error.code}
                if error.code == 400:
                    try:
                        problem = decode_json(error.read(MAX_RESPONSE + 1))
                        if isinstance(problem, dict):
                            if problem.get('code') in PROBLEM_CODES:
                                details['problem_code'] = problem['code']
                            errors = problem.get('errors', [])
                            if isinstance(errors, list):
                                details['pointers'] = [e['pointer'] for e in errors[:200]
                                    if isinstance(e, dict) and isinstance(e.get('pointer'), str) and POINTER.fullmatch(e['pointer'])]
                    except (ValueError, TypeError, UnicodeError, HTTPException, OSError):
                        pass
                raise JobError('api_rejected_request', details=details, request_id=request_id)
            finally:
                error.close()
        except (HTTPException, URLError, TimeoutError, ConnectionError, OSError):
            raise JobError('api_connection_failed', retryable=True, request_id=request_id) from None

    def remember_id(self, headers):
        try:
            ident = uuid_text(headers.get('X-Request-ID'))
        except (ValueError, AttributeError):
            return None
        self.request_ids.append(ident)
        return ident

    def list_ads(self):
        ads, cursor, seen = {}, None, set()
        while True:
            query = {'limit': 200}
            if cursor is not None:
                query['after'] = cursor
            page = self.request('GET', '/ad-groups?' + urlencode(query))
            if (not isinstance(page, dict) or not isinstance(page.get('items'), list)
                    or 'next_after' not in page or len(page['items']) > 200):
                raise JobError('invalid_ad_page')
            try:
                for item in page['items']:
                    ident = uuid_text(item['ad_group_id'])
                    if ident in ads:
                        raise JobError('duplicate_api_ad')
                    profile = item['profile']
                    if profile is not None:
                        check_profile(profile, ident)
                    ads[ident] = {'ad_group_id': ident, 'profile': profile}
                cursor = page['next_after']
                if cursor is None:
                    return ads
                cursor = uuid_text(cursor)
                if cursor in seen:
                    raise JobError('api_cursor_loop')
                seen.add(cursor)
            except (KeyError, TypeError, ValueError):
                raise JobError('invalid_ad_page') from None

    def write_weights(self, items):
        if not isinstance(items, list) or len(items) > 200:
            raise JobError('invalid_batch_size')
        seen = set()
        normalized = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {'ad_group_id', *WEIGHT_FIELDS}:
                raise JobError('invalid_weight_fields')
            try:
                ident = uuid_text(item['ad_group_id'])
            except ValueError:
                raise JobError('invalid_batch_id') from None
            if ident in seen:
                raise JobError('duplicate_batch_id')
            seen.add(ident)
            check_weights(item)
            normalized.append({**item, 'ad_group_id': ident})
        result = self.request('POST', '/profiles/weights', {'items': normalized})
        if not isinstance(result, dict) or not isinstance(result.get('items'), list) or len(result['items']) != len(items):
            raise JobError('invalid_write_acknowledgement')
        try:
            for sent, received in zip(normalized, result['items']):
                if (uuid_text(received['ad_group_id']) != sent['ad_group_id']
                        or received.get('result') not in ('created', 'changed', 'unchanged')):
                    raise JobError('mismatched_write_acknowledgement')
                profile = received['profile']
                check_profile(profile, sent['ad_group_id'])
                if any(profile[name] != sent[name] for name in WEIGHT_FIELDS):
                    raise JobError('mismatched_stored_weights')
        except (KeyError, TypeError, ValueError):
            raise JobError('invalid_write_acknowledgement') from None
        return result['items']
