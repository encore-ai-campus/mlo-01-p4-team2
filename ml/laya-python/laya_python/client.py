"""C8 조회와 카테고리 쓰기. DB와 가중치는 건드리지 않는다."""
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlencode
from uuid import UUID

from .core import Failure, require_uuid, validate_url
from .http_client import json_bytes, request_json
from .settings import validate_ads_base


@dataclass(frozen=True)
class Ad:
    ad_group_id: str
    title: str | None = field(default=None, repr=False)
    redirect_url: str | None = field(default=None, repr=False)
    input_error: str | None = None


def uuid_key(value):
    return str(UUID(require_uuid(value)))


def utc_timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value):
        raise ValueError
    datetime.fromisoformat(value)


def decode(response):
    def reject_constant(_):
        raise ValueError

    try:
        value = json.loads(response.body, parse_constant=reject_constant)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise Failure("ADS_INVALID_RESPONSE", request_id=response.request_id) from None


def validate_profile(profile, ad_id):
    if not isinstance(profile, dict) or uuid_key(profile.get("ad_group_id")) != ad_id:
        raise ValueError
    if type(profile.get("version")) is not int or profile["version"] < 1:
        raise ValueError
    category = profile["category"]
    if category is not None and (type(category) is not int or not 0 <= category <= 255):
        raise ValueError
    weights = [profile[name] for name in ("weight_mlo", "weight_aio", "weight_skn")]
    if any(type(value) is not int or not 0 <= value <= 100 for value in weights) or sum(weights) > 100:
        raise ValueError
    utc_timestamp(profile["changed_at"])


class AdsClient:
    def __init__(self, base_url, internal_key, *, transport=request_json, sleep=time.sleep):
        self.base_url = validate_ads_base(base_url)
        if not isinstance(internal_key, str) or not internal_key.strip():
            raise Failure("ADS_INTERNAL_KEY_MISSING")
        if re.search(r"[\r\n\x00]", internal_key):
            raise Failure("INVALID_API_KEY")
        self._key = internal_key
        self.transport, self.sleep = transport, sleep

    def _request(self, method, path, payload=None):
        writing = method == "POST"
        ambiguous = False
        last_id = None
        for attempt in range(3):
            try:
                response = self.transport(method, self.base_url + path, payload,
                                          headers={"X-Internal-Key": self._key}, timeout=30)
            except Failure as error:
                ambiguous |= writing
                last_id = error.request_id or last_id
                if error.code == "HTTP_TRANSPORT_ERROR" and attempt < 2:
                    self.sleep((10, 30)[attempt])
                    continue
                code = "WRITE_OUTCOME_UNKNOWN" if writing else error.code
                raise Failure(code, request_id=last_id) from None
            last_id = response.request_id or last_id
            if response.request_id is None:
                code = "WRITE_OUTCOME_UNKNOWN" if writing else "ADS_INVALID_RESPONSE"
                raise Failure(code, request_id=last_id)
            if response.status == 503:
                ambiguous |= writing
                retry_after = response.headers.get("retry-after", "")
                if attempt < 2 and re.fullmatch(r"[0-9]+", retry_after):
                    try:
                        seconds = int(retry_after)
                        self.sleep(seconds)
                    except (ValueError, OverflowError):
                        break
                    continue
                break
            if response.status == 200:
                return response
            if writing and ambiguous:
                raise Failure("WRITE_OUTCOME_UNKNOWN", request_id=last_id)
            if response.status in (401, 403):
                raise Failure(f"ADS_AUTH_{response.status}", request_id=last_id)
            return response
        raise Failure("WRITE_OUTCOME_UNKNOWN" if writing else "ADS_UNAVAILABLE", request_id=last_id)

    def pages(self, *, full=False):
        after = None
        last_ad = None
        while True:
            query = {"limit": 200}
            if not full:
                query["state"] = "unclassified"
            if after is not None:
                query["after"] = after
            response = self._request("GET", "/ad-groups?" + urlencode(query))
            if response.status != 200:
                raise Failure("ADS_READ_REJECTED", request_id=response.request_id)
            try:
                body = decode(response)
                rows, next_after = body["items"], body["next_after"]
                if not isinstance(rows, list) or len(rows) > 200:
                    raise ValueError
                ads = []
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError
                    ad_id = uuid_key(row["ad_group_id"])
                    if (last_ad is not None and ad_id <= last_ad) or (after is not None and UUID(ad_id).int <= UUID(after).int):
                        raise ValueError
                    if not isinstance(row["name"], str) or len(row["name"]) > 100:
                        raise ValueError
                    utc_timestamp(row["updated_at"])
                    if row["profile"] is not None:
                        validate_profile(row["profile"], ad_id)
                    creatives = row["creatives"]
                    if not isinstance(creatives, list):
                        raise ValueError
                    ads.append(self._ad(ad_id, creatives))
                    last_ad = ad_id
                if next_after is not None:
                    uuid_key(next_after)
                    if not rows or UUID(next_after).int < UUID(last_ad).int or (after is not None and UUID(next_after).int <= UUID(after).int):
                        raise ValueError
            except (Failure, ValueError, KeyError, TypeError, AttributeError):
                raise Failure("ADS_INVALID_RESPONSE", request_id=response.request_id) from None
            yield ads
            if next_after is None:
                return
            after = next_after

    @staticmethod
    def _ad(ad_id, creatives):
        if len(creatives) != 1:
            return Ad(ad_id, input_error="CREATIVE_COUNT_MISMATCH")
        try:
            creative = creatives[0]
            uuid_key(creative["creative_id"])
            title, url = creative["title"], creative["redirect_url"]
            if not isinstance(title, str) or not title.strip() or len(title) > 200:
                raise ValueError
            if not isinstance(url, str) or len(url) > 2048:
                raise ValueError
            validate_url(url)
            if not isinstance(creative["image_url"], str):
                raise ValueError
            utc_timestamp(creative["updated_at"])
            return Ad(ad_id, title, url)
        except (Failure, KeyError, TypeError, ValueError):
            return Ad(ad_id, input_error="INVALID_CREATIVE")

    def write_categories(self, items):
        """pending을 기록한 호출자가 다음 항목을 요청할 때 실제 전송한다."""
        if not isinstance(items, list) or len(items) > 200:
            raise Failure("INVALID_CATEGORY_BATCH")
        pending = []
        ids = set()
        for item in items:
            if not isinstance(item, dict) or set(item) != {"ad_group_id", "category"}:
                raise Failure("INVALID_CATEGORY_BATCH")
            ad_id = uuid_key(item["ad_group_id"])
            category = item["category"]
            if ad_id in ids or type(category) is not int or not 0 <= category <= 255:
                raise Failure("INVALID_CATEGORY_BATCH")
            ids.add(ad_id)
            pending.append({"ad_group_id": ad_id, "category": category})
        while True:
            payload = {"items": pending}
            if len(json_bytes(payload)) > 64 * 1024:
                raise Failure("CATEGORY_BATCH_TOO_LARGE")
            yield {"kind": "pending", "ids": [item["ad_group_id"] for item in pending]}
            response = self._request("POST", "/profiles/categories", payload)
            if not pending and response.status in (400, 413, 415):
                raise Failure("ADS_AUDIT_REJECTED", request_id=response.request_id)
            if response.status == 200:
                try:
                    results = decode(response)["items"]
                    if not isinstance(results, list) or len(results) != len(pending):
                        raise ValueError
                    confirmed = []
                    for sent, result in zip(pending, results):
                        ad_id = sent["ad_group_id"]
                        if uuid_key(result["ad_group_id"]) != ad_id or result["result"] not in {"created", "changed", "unchanged"}:
                            raise ValueError
                        profile = result["profile"]
                        validate_profile(profile, ad_id)
                        if type(profile["category"]) is not int or profile["category"] != sent["category"]:
                            raise ValueError
                        if result["result"] == "created" and profile["version"] != 1:
                            raise ValueError
                        confirmed.append({"ad_group_id": ad_id, "result": result["result"]})
                except (Failure, ValueError, KeyError, TypeError, AttributeError):
                    raise Failure("WRITE_OUTCOME_UNKNOWN", request_id=response.request_id) from None
                yield {"kind": "succeeded", "items": confirmed, "request_id": response.request_id}
                return
            if response.status == 400:
                indices = self._error_indices(response, len(pending))
                rejected = [item["ad_group_id"] for i, item in enumerate(pending) if i in indices]
                yield {"kind": "failed", "ids": rejected, "reason": "AD_SERVER_REJECTED", "request_id": response.request_id}
                pending = [item for i, item in enumerate(pending) if i not in indices]
                if pending:
                    continue
                return
            if response.status in (413, 415):
                yield {"kind": "failed", "ids": [item["ad_group_id"] for item in pending],
                       "reason": f"ADS_REQUEST_{response.status}", "request_id": response.request_id}
                return
            raise Failure("WRITE_OUTCOME_UNKNOWN", request_id=response.request_id)

    @staticmethod
    def _error_indices(response, count):
        try:
            errors = decode(response)["errors"]
            if not isinstance(errors, list) or not errors:
                raise ValueError
            indices = set()
            for error in errors:
                match = re.fullmatch(r"/items/(0|[1-9][0-9]*)(?:/.*)?", error["pointer"])
                if not match or int(match[1]) >= count:
                    raise ValueError
                indices.add(int(match[1]))
            return indices
        except (Failure, ValueError, TypeError, KeyError):
            return set(range(count))
