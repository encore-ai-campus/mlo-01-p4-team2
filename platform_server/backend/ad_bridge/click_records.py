"""클릭 접수·검증·업적 지급. 외부 보고·조회는 트랜잭션 밖에서 실행한다."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone


@dataclass(frozen=True)
class ServerClick:
    """광고 서버 저장 확인 결과. source는 media 보고와 server 관측을 구분한다."""
    decision_id: UUID
    subject_id: UUID
    reaction_id: UUID
    occurred_at: datetime
    received_at: datetime
    source: str
    type: str
    status: str


class ClickNotFound(Exception):
    pass


def record_click(*, user, owner_id, decision_id, subject_id=None, session_uuid=None):
    from .models import AdClick, IssuedDecision
    owner = UUID(str(owner_id))
    session_uuid = UUID(str(session_uuid)) if session_uuid is not None else None
    with transaction.atomic(durable=True):
        decision = IssuedDecision.objects.filter(pk=decision_id, has_ad=True).first()
        if decision is None or (decision.audience == 'individual' and decision.owner_id != owner):
            raise ClickNotFound
        if decision.audience == 'individual':
            subject = decision.subject_id
        elif decision.audience == 'shared' and subject_id is not None:
            subject = UUID(str(subject_id))
        else:
            raise ValueError('클릭 사용자를 확인할 수 없습니다.')
        clicked_at = timezone.now()
        record, created = AdClick.objects.get_or_create(
            decision=decision, owner_id=owner,
            defaults={'user': user, 'subject_id': subject, 'session_uuid': session_uuid,
                      'clicked_at': clicked_at, 'next_check_at': clicked_at},
        )
        if record.user_id != user.pk:
            raise ClickNotFound
        return record, created


def click_status(*, user, owner_id, decision_id):
    from .models import AdClick
    record = AdClick.objects.filter(user=user, owner_id=owner_id, decision_id=decision_id).first()
    if record is None:
        raise ClickNotFound
    return record


def claim_click(*, now, lease_seconds=60, record_id=None):
    from .models import AdClick
    available = AdClick.objects.filter(state=AdClick.State.PENDING).filter(
        Q(next_check_at__isnull=True) | Q(next_check_at__lte=now),
    ).filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now))
    if record_id is not None:
        available = available.filter(pk=record_id)
    with transaction.atomic(durable=True):
        candidate = available.order_by('clicked_at', 'id').values_list('pk', flat=True).first()
        if candidate is None:
            return None
        token = uuid4()
        changed = available.filter(pk=candidate).update(
            lease_token=token, lease_expires_at=now + timedelta(seconds=lease_seconds), attempts=F('attempts') + 1,
        )
        return AdClick.objects.get(pk=candidate, lease_token=token) if changed else None


def defer_click(record, *, now, error, retry_after=0, blocked=False):
    from .models import AdClick
    delay = max(min(5 * 2 ** min(record.attempts - 1, 6), 300), retry_after)
    return AdClick.objects.filter(pk=record.pk, state=AdClick.State.PENDING, lease_token=record.lease_token).update(
        state=AdClick.State.BLOCKED if blocked else AdClick.State.PENDING,
        next_check_at=None if blocked else now + timedelta(seconds=delay),
        lease_token=None, lease_expires_at=None, last_error=error,
    )


def _validate_receipt(record, receipt, *, source):
    if (source not in ('server', 'media') or not isinstance(receipt, ServerClick)
            or receipt.source != source or receipt.type != 'CLICK'):
        raise ValueError('server_click_required')
    if (UUID(str(receipt.decision_id)) != record.decision_id
            or UUID(str(receipt.subject_id)) != record.subject_id):
        raise ValueError('click_identity_mismatch')
    UUID(str(receipt.reaction_id))
    if receipt.status not in ('RECEIVED', 'ACCEPTED', 'REJECTED'):
        raise ValueError('invalid_click_status')
    for stamp in (receipt.occurred_at, receipt.received_at):
        if not isinstance(stamp, datetime) or timezone.is_naive(stamp):
            raise ValueError('invalid_click_time')
    if source == 'media':
        if UUID(str(receipt.reaction_id)) != record.pk or receipt.occurred_at != record.clicked_at:
            raise ValueError('reported_click_mismatch')
    elif receipt.occurred_at < record.decision.decided_at or receipt.received_at < receipt.occurred_at:
        raise ValueError('invalid_click_order')


def confirm_click(record, receipt, *, now, award=None, source='server'):
    """유효 임대 소유자만 확인 완료와 업적 처리를 함께 커밋한다."""
    from .models import AdClick

    _validate_receipt(record, receipt, source=source)
    if award is None:
        from event_story.achievement_services import record_ad_click
        award = record_ad_click
    with transaction.atomic(durable=True):
        # 업적의 계정 → 이벤트 잠금 순서와 맞춘다.
        user = get_user_model().objects.select_for_update().get(pk=record.user_id, is_active=True)
        saved = AdClick.objects.select_for_update().get(pk=record.pk)
        if (saved.state != AdClick.State.PENDING or saved.lease_token != record.lease_token
                or record.lease_token is None or saved.lease_expires_at is None or saved.lease_expires_at <= now):
            return False
        award(user, session_uuid=saved.session_uuid)
        saved.state = AdClick.State.CONFIRMED
        saved.confirmed_at = now
        saved.confirmation_source = source
        saved.server_reaction_id = receipt.reaction_id
        saved.server_clicked_at = receipt.occurred_at
        saved.server_received_at = receipt.received_at
        saved.server_status = receipt.status
        saved.lease_token = saved.lease_expires_at = saved.next_check_at = saved.last_error = None
        saved.save(update_fields=['state', 'confirmed_at', 'confirmation_source', 'server_reaction_id', 'server_clicked_at',
                                  'server_received_at', 'server_status', 'lease_token', 'lease_expires_at',
                                  'next_check_at', 'last_error'])
        return True
