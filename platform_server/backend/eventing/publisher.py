"""Single-process Kafka publisher for durable outbox events, including movement."""
import json
import logging
import os
import threading
import time
from collections import deque

from django.conf import settings
from django.db import close_old_connections, connections, transaction
from django.db.models import F
from django.utils import timezone

from .events import build_envelope
from .models import EventOutbox

logger = logging.getLogger(__name__)
_worker = None
_worker_lock = threading.Lock()
_stop = threading.Event()
_wakeup = threading.Event()


def notify_outbox_ready():
    """Wake the local worker only after an outbox transaction has committed."""
    _wakeup.set()


def _wait_for_work(stop, seconds):
    deadline = time.monotonic() + seconds
    while not stop.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0 or _wakeup.wait(min(remaining, 0.25)):
            return


def producer_config():
    config = {
        "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
        "acks": "all",
        "enable.idempotence": True,
        "compression.type": "zstd",
        "linger.ms": 10,
        "batch.num.messages": settings.CAMPUS_OUTBOX_PUBLISH_RATE_PER_SECOND,
        "message.send.max.retries": 2147483647,
        "retry.backoff.ms": 100,
        "retry.backoff.max.ms": 1000,
        "delivery.timeout.ms": 120000,
    }
    config.update({key: value for key, value in {
        "security.protocol": settings.KAFKA_SECURITY_PROTOCOL,
        "sasl.mechanism": settings.KAFKA_SASL_MECHANISM,
        "sasl.username": settings.KAFKA_SASL_USERNAME,
        "sasl.password": settings.KAFKA_SASL_PASSWORD,
    }.items() if value})
    return config


def _record_deliveries(inflight, delivered, retry_at):
    if not delivered:
        return
    successful = [event_uuid for event_uuid, error in delivered.items() if error is None]
    failed = [event_uuid for event_uuid, error in delivered.items() if error is not None]
    with transaction.atomic():
        if successful:
            EventOutbox.objects.filter(event_uuid__in=successful, published_at__isnull=True).update(
                published_at=timezone.now(),
            )
        if failed:
            EventOutbox.objects.filter(event_uuid__in=failed, published_at__isnull=True).update(
                retry_count=F("retry_count") + 1,
            )
    # Keep each key occupied until its acknowledgement has committed to the DB.
    for event_uuid, error in delivered.items():
        row = inflight.pop(event_uuid)
        if error is not None:
            retry_at[row.message_key] = time.monotonic() + 1
            logger.warning("Kafka delivery failed; event remains in outbox: event=%s topic=%s error=%s",
                           event_uuid, row.topic, error)
    delivered.clear()


def _publish_pending(producer, stop):
    limit = settings.CAMPUS_OUTBOX_PUBLISH_RATE_PER_SECOND
    accepted = deque()
    inflight, delivered, retry_at = {}, {}, {}
    idle_delay = 0.25
    max_idle_delay = settings.CAMPUS_OUTBOX_IDLE_POLL_SECONDS
    try:
        while not stop.is_set():
            # Clear before reading: a commit during the read must still wake the wait.
            _wakeup.clear()
            close_old_connections()
            try:
                producer.poll(0)
                _record_deliveries(inflight, delivered, retry_at)
                now = time.monotonic()
                while accepted and accepted[0] <= now - 1:
                    accepted.popleft()
                retry_at = {key: deadline for key, deadline in retry_at.items() if deadline > now}
                capacity = min(limit - len(inflight), limit - len(accepted))
                queued = 0
                buffer_full = False
                while capacity > 0 and not stop.is_set():
                    blocked_keys = {row.message_key for row in inflight.values()} | retry_at.keys()
                    rows = list(EventOutbox.objects.filter(published_at__isnull=True).exclude(
                        message_key__in=blocked_keys,
                    ).order_by("created_at", "event_uuid")[:capacity])
                    if not rows:
                        break
                    for row in rows:
                        if stop.is_set():
                            break
                        if row.message_key in blocked_keys:
                            continue
                        inflight[row.event_uuid] = row
                        try:
                            if row.payload_json is None:
                                row.payload_json = build_envelope(
                                    row.event_uuid, row.event_type, row.occurred_at, row.raw_data,
                                )
                                if row.payload_json is None:
                                    raise RuntimeError("xxhash is required to build the Kafka envelope")
                                EventOutbox.objects.filter(
                                    event_uuid=row.event_uuid, payload_json__isnull=True,
                                ).update(payload_json=row.payload_json)
                            value = json.dumps(row.payload_json, ensure_ascii=False, separators=(",", ":"),
                                               allow_nan=False).encode("utf-8")

                            def callback(error, message, event_uuid=row.event_uuid):
                                delivered[event_uuid] = error

                            producer.produce(row.topic, key=str(row.message_key).encode("utf-8"),
                                             value=value, on_delivery=callback)
                        except BufferError:
                            inflight.pop(row.event_uuid)
                            buffer_full = True
                            break
                        except Exception as error:
                            delivered[row.event_uuid] = error
                        else:
                            accepted.append(time.monotonic())
                            queued += 1
                        blocked_keys.add(row.message_key)
                        capacity -= 1
                    if buffer_full:
                        break
                    # Query again so a page of one user's rows cannot hide other users.
                    capacity = min(capacity, limit - len(inflight), limit - len(accepted))
                if queued:
                    idle_delay = 0.25
                if inflight:
                    producer.poll(0.1)
                if delivered:
                    continue
                if buffer_full:
                    stop.wait(0.1)
                elif not queued:
                    delay = min(idle_delay, max_idle_delay)
                    if accepted and len(accepted) >= limit:
                        delay = min(delay, max(0.001, accepted[0] + 1 - time.monotonic()))
                    _wait_for_work(stop, delay)
                    idle_delay = min(idle_delay * 2, max_idle_delay)
            except Exception:
                connections.close_all()
                logger.exception("Raw event publisher loop failed; pending events will be retried")
                stop.wait(1)
    finally:
        try:
            producer.flush(5)
        except Exception:
            logger.exception("Kafka producer could not flush during shutdown")
        try:
            _record_deliveries(inflight, delivered, retry_at)
        except Exception:
            logger.exception("Kafka acknowledgements could not be saved during shutdown; outbox rows remain pending")


def _publisher_loop(stop):
    try:
        from confluent_kafka import Producer
    except ImportError:
        logger.error("Kafka publisher is disabled: install project requirements to enable confluent-kafka")
        return
    try:
        producer = Producer(producer_config())
    except Exception:
        logger.exception("Could not configure Kafka producer")
        return

    database = connections["default"]
    original_options = database.settings_dict
    if database.vendor == "mysql":
        # DatabaseWrapper is thread-local. Copy its options so only this one
        # background worker retains a connection; ASGI requests keep max age 0.
        database.settings_dict = {
            **original_options,
            "CONN_MAX_AGE": settings.CAMPUS_OUTBOX_CONNECTION_MAX_AGE_SECONDS,
            "CONN_HEALTH_CHECKS": True,
        }
    try:
        _publish_pending(producer, stop)
    finally:
        connections.close_all()
        database.settings_dict = original_options


def start_outbox_publisher():
    global _worker
    if not settings.KAFKA_BOOTSTRAP_SERVERS:
        return None
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return _worker
        if not os.environ.get("RUN_MAIN") == "true" and "runserver" in os.sys.argv and "--noreload" not in os.sys.argv:
            return None
        _stop.clear()
        _worker = threading.Thread(
            target=_publisher_loop, args=(_stop,), name="campus-kafka-outbox", daemon=True,
        )
        _worker.start()
    return _worker
