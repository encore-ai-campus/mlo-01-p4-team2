"""Delete acknowledged outbox rows daily, independently of Kafka availability."""
from datetime import datetime, time
import json
import logging
from pathlib import Path
import threading
from zoneinfo import ZoneInfo

from django.apps import apps
from django.conf import settings
from django.db import close_old_connections, connections
from django.utils import timezone

from .models import EventOutbox

logger = logging.getLogger(__name__)
_worker = None
_worker_lock = threading.Lock()
_zone = ZoneInfo("Asia/Seoul")


def cleanup_due(now, path):
    local = timezone.localtime(now, _zone)
    schedule = time.fromisoformat(settings.CAMPUS_OUTBOX_CLEANUP_TIME)
    if local.time().replace(tzinfo=None) < schedule:
        return False
    try:
        day = json.loads(path.read_text(encoding="utf-8"))["completed_day"]
        return day < local.date().isoformat()
    except (OSError, ValueError, KeyError, TypeError):
        return True


def run_due_cleanup(now=None, path=None):
    now = now or timezone.now()
    path = Path(path or settings.CAMPUS_OUTBOX_CLEANUP_STATE)
    if not cleanup_due(now, path):
        return False
    local = timezone.localtime(now, _zone)
    cutoff = datetime.combine(local.date(), time.fromisoformat(settings.CAMPUS_OUTBOX_CLEANUP_TIME), _zone)
    removed = 0
    while True:
        keys = list(EventOutbox.objects.filter(published_at__lte=cutoff)
                    .order_by("published_at", "created_at").values_list("pk", flat=True)[:1000])
        if not keys:
            break
        # Recheck acknowledgements at deletion, and commit each batch separately.
        count, _ = EventOutbox.objects.filter(pk__in=keys, published_at__lte=cutoff).delete()
        removed += count
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"completed_day": local.date().isoformat(),
                                     "completed_at": now.isoformat()}), encoding="utf-8")
    temporary.replace(path)
    logger.info("Published outbox cleanup removed %s rows through %s", removed, cutoff.isoformat())
    return True


def cleanup_loop(stop):
    while not apps.ready:
        if stop.wait(1):
            return
    while not stop.is_set():
        try:
            close_old_connections()
            run_due_cleanup()
        except Exception:
            logger.exception("Published outbox cleanup failed; retrying in 60 seconds")
        finally:
            connections.close_all()
        stop.wait(60)


def start_outbox_cleanup():
    global _worker
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return _worker
        time.fromisoformat(settings.CAMPUS_OUTBOX_CLEANUP_TIME)
        _worker = threading.Thread(target=cleanup_loop, args=(threading.Event(),),
                                   name="campus-outbox-cleanup", daemon=True)
        _worker.start()
        return _worker
