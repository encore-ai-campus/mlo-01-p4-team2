"""사용자별 옥상 회차와 서버가 승인한 포털 이동의 광고를 발급·보관한다.

광고 서버를 기다리는 동안 DB 연결을 쥐지 않고, 프로세스당 동시 발급 수를 제한해
광고 쪽 지연이 메타버스 요청을 늦추지 않게 한다(AB-1 §4.5).
"""

from dataclasses import asdict
from hashlib import sha256
from datetime import datetime, timezone as utc, timedelta
import logging
import threading
import time
from uuid import UUID

from django.conf import settings
from django.core import signing
from django.db import connection
from django.utils import timezone

from .api.errors import AdsHTTPError, AdsResponseError, AdsTransportError
from .decisions.service import create_decision
from .decisions import store
from .clicks import click_token

ROUND_SECONDS = 10
TRANSFER_SALT = "ad-bridge.portal.v1"
DEFAULT_DECISION_CONCURRENCY = 8
logger = logging.getLogger(__name__)
_slots_lock = threading.Lock()
_slots = None


def transfer_token(user_id, transfer_id):
    return signing.dumps({"user": str(user_id), "transfer": str(transfer_id)}, salt=TRANSFER_SALT)


def transfer_identity(token, user_id):
    data = signing.loads(token, salt=TRANSFER_SALT, max_age=15)
    if data.get("user") != str(user_id):
        raise signing.BadSignature("다른 사용자의 이동입니다.")
    return str(UUID(data["transfer"]))


def _decision_slots():
    """프로세스당 동시 발급 상한. 설정이 틀리면 기본값으로 돌고 경고를 남긴다(예외를 내지 않음)."""
    global _slots
    with _slots_lock:
        if _slots is None:
            raw = getattr(settings, "ADS_DECISION_CONCURRENCY", "")
            try:
                limit = int(raw) if str(raw).strip() else DEFAULT_DECISION_CONCURRENCY
                if not 1 <= limit <= 64:
                    raise ValueError
            except (TypeError, ValueError):
                logger.warning("ADS_DECISION_CONCURRENCY는 1~64의 정수여야 합니다. 기본값 %d로 돕니다.",
                               DEFAULT_DECISION_CONCURRENCY)
                limit = DEFAULT_DECISION_CONCURRENCY
            _slots = threading.BoundedSemaphore(limit)
        return _slots


def _release_connection():
    # 광고 서버를 기다리는 동안 풀 연결을 쥐지 않는다(풀이면 반납, 아니면 닫힘, 다음 쿼리에서 다시 받음).
    # 트랜잭션 안(시험의 TestCase 등)에서는 닫지 않는다.
    if not connection.in_atomic_block:
        connection.close()


LOG_INTERVAL_SECONDS = 60
_last_logged = {}


def warn_throttled(key, message, *args):
    """같은 종류의 경고는 프로세스당 60초에 한 번만 남긴다(사용자마다 몇 초씩 다시 묻기 때문)."""
    now = time.monotonic()
    last, skipped = _last_logged.get(key, (None, 0))
    if last is not None and now - last < LOG_INTERVAL_SECONDS:
        _last_logged[key] = (last, skipped + 1)
        return
    _last_logged[key] = (now, 0)
    logger.warning(message + " (지난 경고 뒤 같은 종류 %d건 생략)", *args, skipped)


def _log_failure(slot, error):
    # 매체 키·가명·사용자 ID는 싣지 않는다.
    status, code = getattr(error, "status", None), getattr(error, "code", None)
    warn_throttled(
        ("decision", slot, type(error).__name__, status, code),
        "광고 결정 발급 실패: slot=%s error=%s status=%s code=%s request_id=%s",
        slot, type(error).__name__, status, code, getattr(error, "request_id", None),
    )


def get_batch(*, client, owner_id, subject_id, transfer_id=None, segment=None, now=None):
    """저장된 회차·이동 예약을 돌려준다. 새로 발급할 자리가 없으면 아무것도 쓰지 않고 None(BUSY)."""
    from .models import PlacementBatch

    owner_id, subject_id = UUID(str(owner_id)), UUID(str(subject_id))
    now = now or timezone.now()
    rooftop = transfer_id is None
    if rooftop:
        epoch = int(now.timestamp()) // ROUND_SECONDS * ROUND_SECONDS
        key = f"roof:{owner_id}:{epoch}"
        expires = datetime.fromtimestamp(epoch + ROUND_SECONDS, tz=utc.utc)
        slots = [getattr(settings, "ADS_ROOFTOP_SLOT_ID", "")]
    else:
        key = f"portal:{transfer_id}"
        expires = now + timedelta(seconds=15)
        slots = [getattr(settings, name, "") for name in ("ADS_PORTAL_LEFT_SLOT_ID", "ADS_PORTAL_RIGHT_SLOT_ID")]
    # 모든 슬롯을 발급 전에 검증해 부분 설정으로 외부 결정을 낭비하지 않는다.
    slots = [str(UUID(slot)) for slot in slots]
    batch = PlacementBatch.objects.filter(key=key).first()
    if batch is None:
        limit = _decision_slots()
        # 기다리지 않는다. 자리가 없으면 예약을 만들지 않아 같은 회차·이동에서 다시 물을 수 있다.
        if not limit.acquire(blocking=False):
            return None
        try:
            batch = _issue(PlacementBatch, client=client, key=key, owner_id=owner_id, subject_id=subject_id,
                           expires=expires, slots=slots, segment=segment)
        finally:
            limit.release()
    if batch.owner_id != owner_id:
        raise ValueError("광고 배치 소유자가 일치하지 않습니다.")
    return batch


def _issue(PlacementBatch, *, client, key, owner_id, subject_id, expires, slots, segment):
    batch, created = PlacementBatch.objects.get_or_create(
        key=key, defaults={"owner_id": owner_id, "expires_at": expires},
    )
    if not created or batch.owner_id != owner_id:
        return batch
    payload = []
    for slot in slots:
        _release_connection()
        try:
            decision = create_decision(client=client, store=store, slot_id=slot,
                                       owner_id=owner_id, subject_id=subject_id, segment=segment)
        except (AdsHTTPError, AdsResponseError, AdsTransportError, ValueError) as error:
            # POST 결과 유실은 발급 여부가 불명확하다. 같은 회차·이동에서 자동 재발급하지 않는다.
            # 슬롯마다 따로 실패시켜 다른 슬롯의 광고는 그대로 쓴다. 브라우저는 실패한 칸에 이전 광고를 둔다.
            _log_failure(slot, error)
            payload.append({"decision_id": None, "ad": None})
        else:
            # 슬롯마다 받은 결정을 그대로 쓴다. 같은 소재여도 합치지 않는다(명세 50 §4).
            payload.append(asdict(decision))
    batch.state = "READY" if any(entry["decision_id"] for entry in payload) else "FAILED"
    batch.payload = payload
    batch.save(update_fields=["state", "payload"])
    return batch


def public_batch(batch, subject_id, *, owner_id):
    owner_id = UUID(str(owner_id))
    if batch.owner_id is not None and batch.owner_id != owner_id:
        raise ValueError("광고 배치 소유자가 일치하지 않습니다.")
    ads = []
    for entry in batch.payload:
        ad = entry["ad"]
        url = ad["click_url"].replace("{subject_id}", str(subject_id)) if ad else None
        ads.append({"decision_id": entry["decision_id"], "ad": None if ad is None else {
            "creative_id": ad["creative_id"], "title": ad["title"], "image_url": ad["image_url"],
            "click_url": url, "click_token": click_token(entry["decision_id"], url, owner_id),
        }})
    # 내부 사용자 UUID가 포함된 옥상 키를 브라우저에 그대로 노출하지 않는다.
    public_key = batch.key
    if batch.key.startswith("roof:") and batch.owner_id is not None:
        public_key = sha256(batch.key.encode("utf-8")).hexdigest()
    return {"batch_id": public_key, "state": batch.state, "ads": ads,
            "server_time": timezone.now().isoformat(), "expires_at": batch.expires_at.isoformat()}


def busy_batch():
    """동시 발급 상한에 걸렸을 때의 답. DB를 쓰지 않았으므로 브라우저는 이전 광고를 두고 곧 다시 묻는다."""
    return {"batch_id": None, "state": "BUSY", "ads": [], "server_time": timezone.now().isoformat(),
            "expires_at": None}
