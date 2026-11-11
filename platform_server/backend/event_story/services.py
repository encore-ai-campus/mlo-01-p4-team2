from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from learning import services as learning

from .models import StoryProgress, StoryRecord
from .access import verify

STEPS = ['게시판에서 조사 시작', '라운지 2의 단서 2개 조사', 'Python 또는 SQL 초급 통과', '수량표 작성 순서 추리', '해결 방법 선택 · 선물 복구']


def window(now=None):
    now = now or timezone.now()
    start = datetime.fromisoformat(settings.EVENT_STORY_DATE).replace(tzinfo=ZoneInfo('Asia/Seoul'))
    end = start + timedelta(days=1)
    preview = settings.DEBUG and settings.EVENT_STORY_PREVIEW
    phase = 'open' if preview or start <= now < end else 'before' if now < start else 'closed'
    return {'phase': phase, 'preview': preview, 'start': start.isoformat(), 'end': end.isoformat()}


def ensure_open():
    phase = window()['phase']
    if phase != 'open':
        message = '이벤트는 11월 11일 하루 종일 진행돼요. 11월 11일에 만나요!' if phase == 'before' else '이벤트가 종료됐어요. 저장된 기록은 그대로 보관됩니다.'
        raise learning.LearningError(message, 'event_' + phase, 409)


def key():
    return 'pepero-main-' + settings.EVENT_STORY_DATE


def locked_progress(user):
    get_user_model().objects.select_for_update().get(pk=user.pk)
    return StoryProgress.objects.select_for_update().get_or_create(user=user, event_key=key())[0]


def log(progress, record_key, kind, payload=None, session_uuid=None):
    """Returns (record, created); only a new record of a reported kind becomes an event."""
    from eventing.events import record_story_action
    with transaction.atomic():
        record, created = StoryRecord.objects.get_or_create(progress=progress, key=record_key, defaults={
            'kind': kind, 'payload': payload or {}, 'session_uuid': str(session_uuid or '')})
        if created:
            record_story_action(record, session_uuid)
    return record, created


def require(progress, *flags):
    if not progress.started_at or not all(progress.flags.get(flag) for flag in flags):
        raise learning.LearningError('현재 미션을 먼저 완료해 주세요. 아직 확인하지 않은 단서가 있어요.', 'prerequisite', 409)


def status(user, progress=None):
    p = progress or StoryProgress.objects.filter(user=user, event_key=key()).first()
    flags = p.flags if p else {}
    completed = [bool(flags.get('board')), bool(flags.get('wrapper') and flags.get('count')),
                 bool(flags.get('learning')), bool(flags.get('quantity')), bool(p and p.completed_at)]
    stage = next((i + 1 for i, value in enumerate(completed) if not value), 6)
    pending = ('memo' if flags.get('learning') and not flags.get('memo') else
               'apology' if flags.get('ethics') and not flags.get('apology') else
               'ending' if flags.get('apology') and not (p and p.completed_at) else None)
    return {'eventKey': key(), 'window': window(), 'started': bool(p and p.started_at), 'stage': stage,
            'steps': [{'name': title, 'complete': done} for title, done in zip(STEPS, completed)],
            'flags': flags, 'pending': pending, 'completed': bool(p and p.completed_at)}


@learning.learning_write_transaction
def act(user, data, session_uuid=None):
    ensure_open()
    p = locked_progress(user)
    action = data.get('action')
    allowed = {'popup_view', 'start', 'board', 'wrapper', 'count', 'memo', 'quantity', 'ethics', 'apology', 'finish'}
    if action not in allowed:
        raise learning.LearningError('지원하지 않는 미션 요청입니다.')
    if action == 'popup_view':
        log(p, 'popup_view', 'popup_view', session_uuid=session_uuid)
        return {'state': status(user, p)}
    if action == 'start':
        log(p, 'popup_view', 'popup_view', session_uuid=session_uuid)
        if not p.started_at:
            p.started_at = timezone.now()
            p.save(update_fields=['started_at', 'updated_at'])
            log(p, 'start', 'event_start', session_uuid=session_uuid)
        return {'state': status(user, p)}
    require(p)
    # Completed steps are replayable without creating a second record.
    flag = 'completed' if action == 'finish' else action
    if p.flags.get(flag):
        return {'state': status(user, p), 'correct': True}
    prerequisites = {'board': (), 'wrapper': ('board',), 'count': ('board',),
        'memo': ('learning',), 'quantity': ('memo',), 'ethics': ('quantity',),
        'apology': ('ethics',), 'finish': ('apology',)}
    require(p, *prerequisites[action])
    if action in {'board', 'wrapper', 'count', 'quantity', 'ethics'}:
        verify(user, data.get('ticket'), action)
    if action in {'quantity', 'ethics'}:
        if type(data.get('answer')) is not int or data['answer'] not in (1, 2, 3):
            raise learning.LearningError('선택지 하나를 선택하세요.')
        if data['answer'] != 2:
            # Wrong submissions have no rewards, penalties, or progress effects.
            return {'state': status(user, p), 'correct': False}
    p.flags = {**p.flags, flag: True}
    if action == 'finish':
        p.completed_at = timezone.now()
    p.save(update_fields=['flags', 'completed_at', 'updated_at'])
    log(p, action, 'story_' + action, session_uuid=session_uuid)
    return {'state': status(user, p), 'correct': True}


def learning_started(session):
    from . import main
    main.learning_started(session)
    """Only eligible sessions started after the two clues can advance the story."""
    if session.subject == 'cs' or window()['phase'] != 'open' or session.difficulty != 'beginner':
        return
    p = StoryProgress.objects.select_for_update().filter(user=session.user, event_key=key()).first()
    if p and p.started_at and p.flags.get('wrapper') and p.flags.get('count'):
        log(p, f'learning-start:{session.pk}', 'learning_start', {'session': session.pk, 'subject': session.subject})


def learning_completed(session):
    from . import main
    main.learning_completed(session)
    """Observe the existing server-graded pass/fail result; do not change grading."""
    if session.subject == 'cs' or window()['phase'] != 'open' or session.difficulty != 'beginner' or session.total_questions != 10 or not session.completed_at:
        return
    p = StoryProgress.objects.select_for_update().filter(user=session.user, event_key=key()).first()
    if not p or not p.records.filter(key=f'learning-start:{session.pk}').exists():
        return
    passed = session.correct_count >= 7
    log(p, f'learning-result:{session.pk}', 'learning_result', {'session': session.pk,
        'subject': session.subject, 'score': session.correct_count, 'passed': passed})
    if passed and not p.flags.get('learning'):
        p.flags = {**p.flags, 'learning': True, 'learningSubject': session.subject, 'learningScore': session.correct_count}
        p.save(update_fields=['flags', 'updated_at'])
