"""outbox 쓰기. **호출한 쪽의 트랜잭션 안에서만** 쓴다 — 업무 데이터와 같은 트랜잭션이어야 "저장은 됐는데 이벤트가 없는" 상태가 생기지 않는다."""
from django.db import connection
from django.utils import timezone

from . import schemas
from .models import EventOutbox


class OutsideTransaction(RuntimeError):
    """트랜잭션 밖에서 outbox 를 쓰려 했다(프로그래밍 오류)."""


def enqueue(event_name, source_id, aggregate_id, payload, produced_at=None):
    """이벤트 한 건을 outbox 에 쓴다. 같은 (이벤트 종류, 원천 ID) 는 한 줄만 남고 다시 써도 바뀌지 않는다(재처리 멱등)."""
    if not connection.in_atomic_block:
        raise OutsideTransaction("outbox 는 업무 데이터와 같은 트랜잭션 안에서 써야 한다")
    row = EventOutbox(
        event_id=schemas.event_id_of(event_name, source_id), event_name=event_name, schema_version=schemas.schema_version_of(event_name, payload),
        aggregate_id=aggregate_id, payload=payload, created_at=produced_at or timezone.now(),
    )
    # 이미 있으면 아무것도 바꾸지 않는다(ON CONFLICT DO NOTHING). PostgreSQL 은 유일 충돌만 건너뛰고 다른 오류(길이·형식·CHECK)는 그대로 올린다.
    # 충돌 대상을 정할 수 없으므로(Django) 이 표에 PK 말고 다른 유일 제약을 더하면 그 충돌도 조용히 건너뛴다 — 그때 이 줄을 다시 본다.
    # 쓰는 쪽(enqueue)은 INSERT 만 한다(CDC 의 전제 — 공통 레이크하우스 설계안 §3.5). 중계의 sent_at UPDATE·purge DELETE 는 Kafka 전환 때 바꾼다(계획서 §11)
    EventOutbox.objects.bulk_create([row], ignore_conflicts=True)
    return row.event_id
