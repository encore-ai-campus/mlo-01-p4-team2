"""Shared event gate; local calendar evaluation needs no scheduler process."""
from datetime import datetime
from zoneinfo import ZoneInfo
from django.db import transaction
from django.utils import timezone
from .models import EventAccessState

TZ = ZoneInfo('Asia/Seoul')
START = datetime(2026,11,11,tzinfo=TZ)
END = datetime(2026,11,12,tzinfo=TZ)
MODES = ('AUTO','OPEN','CLOSED')

def snapshot(now=None):
    row = EventAccessState.objects.filter(pk=1).first()
    mode,revision = (row.mode,row.revision) if row else ('AUTO',0)
    now = now or timezone.now()
    phase = 'open' if START <= now < END else 'before' if now < START else 'closed'
    if mode == 'OPEN': phase = 'open'
    elif mode == 'CLOSED': phase = 'closed'
    message = ('이벤트가 열려 있어요.' if phase=='open' else
               '관리자가 이벤트를 닫았습니다. 저장된 기록은 유지됩니다.' if mode=='CLOSED' else
               '이벤트는 11월 11일에 시작됩니다.' if phase=='before' else
               '이벤트가 종료됐어요. 저장된 기록과 배지는 유지됩니다.')
    return dict(mode=mode,revision=revision,phase=phase,open=phase=='open',
                start=START.isoformat(),end=END.isoformat(),timezone='Asia/Seoul',message=message,
                preview=False)

class RevisionConflict(Exception): pass

@transaction.atomic
def change(actor,mode,revision):
    if not actor.is_active or not actor.is_staff: raise PermissionError
    if mode not in MODES: raise ValueError
    EventAccessState.objects.get_or_create(pk=1)
    row=EventAccessState.objects.select_for_update().get(pk=1)
    if row.revision != revision: raise RevisionConflict
    if row.mode != mode:
        count=EventAccessState.objects.filter(pk=1,revision=revision).update(
            mode=mode,revision=revision+1,updated_by=actor,updated_at=timezone.now())
        if not count: raise RevisionConflict
    return snapshot()
