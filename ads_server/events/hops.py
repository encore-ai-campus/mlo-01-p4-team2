"""구간별 정합 manifest 의 읽기 쪽(계획 4-3b): outbox·MongoDB 를 창 단위로 읽어 공용 요약 규칙으로 요약한다.

규칙(창·문서 형태·`ids_sha256`·`content_sha256`·정규화)은 `hop_rules.py` 에 있다 — 표준 라이브러리만 쓰는 파일이라 파이프라인 쪽이 그대로 복사해 쓴다
(계획 02 P1-1, 설계 01 D19). 아래에서 다시 내보내는 이름은 기존 호출(`events.api`·`hopcheck`·시험)을 그대로 두기 위한 것이다.
"""
from collections import Counter

from .hop_rules import (  # noqa: F401 — 다시 내보냄
    DAY, HOP_SCHEMA_VERSION, HOUR, MILLIS_FORMAT, TIME_FORMAT, WINDOW_SIZES, WINDOWS, _es_number, _millis_utc, _produced,
    canonical_json, format_time, is_closed, normal_document, parse_time, rule_sha256, summarize, window_label, window_start,
)
from .models import EventOutbox
from . import store


# ---------------------------------------------------------------- 창 단위 읽기 (외부 검토 00d3067 P3-3)

def summarize_windows(streams, now, grace_seconds, since=None, size=DAY):
    """`summarize` 와 같은 결과를 **닫힌 창 하나·흐름 하나씩** 읽어 만든다 — 전체 이력을 메모리에 두지 않고, 열린 창과 `since` 앞은 읽지 않는다.

    흐름은 (`first`, `read`) 쌍이다: `first(lo)` 는 `lo`(None 이면 처음) 이후 가장 이른 생성 시각(없으면 None), `read(start, end)` 는 생성 시각이 [start, end) 인 정규화 문서.
    흐름끼리 이벤트 이름이 겹치지 않아야 한다. 반환: (줄 목록, 열린 창 시작 목록, 읽은 문서 수, 한 번에 가장 많이 든 문서 수).
    """
    # ponytail: 창 하나·흐름 하나는 통째로 메모리에 든다(ID 순 정렬이 필요하다). 그것도 크면 --window hour, 그래도 크면 외부 정렬
    lines, open_windows, read_count, largest = [], set(), 0, 0
    for first, read in streams:
        lo = since
        while (moment := first(lo)) is not None:  # 빈 창은 건너뛴다(다음 행이 있는 창으로)
            start = window_start(moment, size)
            lo = start + size
            if not is_closed(start, now, grace_seconds, size):
                open_windows.add(start)
                continue
            docs = list(read(start, lo))
            read_count, largest = read_count + len(docs), max(largest, len(docs))
            lines += summarize(docs, now, grace_seconds, since, size)[0]
    return sorted(lines, key=lambda line: (line["window_start"], line["event_name"])), sorted(open_windows), read_count, largest


def table_stream(queryset, field, to_documents):
    """Django 질의를 생성 시각 열 `field` 의 창으로 읽는 흐름(`summarize_windows` 용). `to_documents(rows)` 가 창 하나의 행을 정규화 문서로 바꾼다.
    `field` 로 시작하는(앞의 같음 조건 뒤에 오는) 색인이 있어야 창마다 범위만 읽는다."""
    def first(lo):
        rows = queryset if lo is None else queryset.filter(**{f"{field}__gte": lo})
        return rows.order_by(field).values_list(field, flat=True).first()

    def read(start, end):
        return to_documents(queryset.filter(**{f"{field}__gte": start, f"{field}__lt": end}))
    return first, read


def outbox_streams():
    """② 구간: outbox 를 이벤트 종류마다 창으로 읽는 흐름(색인 `events_outbox_name_created_idx`). 호출한 쪽의 읽기 스냅샷 안에서 쓴다."""
    def documents(rows):
        return [normal_document(row.event_id, row.event_name, row.schema_version, row.created_at, row.payload) for row in rows]
    for name in EventOutbox.objects.order_by("event_name").values_list("event_name", flat=True).distinct():
        yield table_stream(EventOutbox.objects.filter(event_name=name), "created_at", documents)


def mongo_streams():
    """③ 구간: MongoDB 를 이벤트 종류마다 창으로 읽는 흐름(색인 `event_name_produced_at`)."""
    events = store.collection()
    return [_mongo_stream(events, name) for name in sorted(events.distinct("event_name"))]


def _mongo_stream(events, name):
    def first(lo):
        doc = events.find_one({"event_name": name, **({"produced_at": {"$gte": lo}} if lo else {})}, {"produced_at": 1}, sort=[("produced_at", 1)])
        return doc and doc["produced_at"]

    def read(start, end):
        return [normal_document(doc["_id"], doc["event_name"], doc["schema_version"], doc["produced_at"], doc["payload"])
                for doc in events.find({"event_name": name, "produced_at": {"$gte": start, "$lt": end}})]
    return first, read


def unsent_counts(size=DAY):
    """아직 보내지 않은 outbox 행의 (창 시작, 이벤트 이름)별 건수. 중계 지연 후보를 가리는 데 쓴다. 미전송 행만 읽는다(색인 `events_outbox_pending_idx`).
    호출한 쪽의 읽기 스냅샷 안에서 부른다."""
    rows = EventOutbox.objects.filter(sent_at__isnull=True).values_list("created_at", "event_name").iterator()
    return Counter((format_time(window_start(created_at, size)), name) for created_at, name in rows)
