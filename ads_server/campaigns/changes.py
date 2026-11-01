"""캠페인·광고그룹 변경 로그(계획 docs/plans/00 §5.2). 로그 한 줄과 변경 이벤트를 **같은 트랜잭션**에 쓰는 곳은 여기 하나다 — services·transitions 는 이 함수만 부른다."""
from django.db import connection

from events import api as events

from .models import ChangeLog, ChangeTarget
from .types import ChangeView


def record_change(*, target_type, target_id, campaign_id, change_type, previous, new, now):
    """변경 한 번을 기록한다. `previous`·`new` 는 바뀐 업무 필드의 값(dict — `CREATED` 는 `previous=None`)이고, 저장·이벤트에 싣는 형식은 events 가 정한다
    (`events.change_value`). 로그와 이벤트에 **같은 값**을 쓰므로 로그가 이벤트의 정합성 원천이 된다. 값이 바뀌지 않았으면 아무것도 쓰지 않고 `None`.
    호출한 쪽의 트랜잭션 안에서 부른다(이벤트 쓰기가 실패하면 업무 변경과 함께 되돌려진다)."""
    if not connection.in_atomic_block:  # 로그를 먼저 쓰므로 여기서 막는다 — 트랜잭션 밖이면 로그가 autocommit 으로 커밋된 뒤 outbox 만 실패한다
        raise events.OutsideTransaction("변경 로그는 업무 변경과 같은 트랜잭션 안에서 써야 한다")
    previous_value = None if previous is None else events.change_value(**previous)
    new_value = events.change_value(**new)
    if previous_value == new_value:
        return None
    row = ChangeLog.objects.create(target_type=target_type, target_id=target_id, campaign_id=campaign_id, change_type=change_type,
                                   previous_value=previous_value, new_value=new_value, changed_at=now)
    common = dict(now=now, campaign_id=campaign_id, change_id=row.change_id, change_type=change_type, previous_value=previous_value, new_value=new_value, changed_at=now)
    if target_type == ChangeTarget.CAMPAIGN:
        events.record_campaign_changed(**common)
    else:
        events.record_ad_group_changed(ad_group_id=target_id, **common)
    return row.change_id


def _view(row):
    return ChangeView(change_id=row.change_id, target_type=row.target_type, target_id=row.target_id, campaign_id=row.campaign_id, change_type=row.change_type,
                      previous_value=row.previous_value, new_value=row.new_value, changed_at=row.changed_at)


def change_rows(target_type):
    """그 대상 종류의 변경 로그 전부(`ChangeView`) — 이벤트 누락 점검·기대 이벤트용 읽기."""
    return (_view(r) for r in ChangeLog.objects.filter(target_type=target_type).iterator(chunk_size=2000))


def change_stream(target_type, to_documents):
    """그 대상 종류의 변경 로그를 변경 시각 창으로 읽는 흐름(구간 manifest 의 원본 구간, 색인 `campaigns_chg_type_at_idx`)."""
    return events.table_stream(ChangeLog.objects.filter(target_type=target_type), "changed_at", lambda rows: to_documents([_view(r) for r in rows]))
