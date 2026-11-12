"""Account-owned hidden recipe, with progress and its reward committed together."""
from django.contrib.auth import get_user_model
from django.utils import timezone

from learning.services import LearningError, learning_write_transaction
from . import main
from .access import verify
from .achievement_services import RECIPE_ID, achievement_snapshot, complete_achievement
from .models import StoryProgress
from .services import log

EVENT_KEY = 'pepero-2026-secret-recipe'
NOTE_ID = 'hidden-recipe-note'
GUIDE_ID = 'campus-guide-kkuwang'
PREREQUISITE_MESSAGE = '메인 미션을 완료하면 주울 것 같이 생겼어요'


def status(user, progress=None):
    progress = progress or StoryProgress.objects.filter(user=user, event_key=EVENT_KEY).first()
    flags = progress.flags if progress else {}
    record = progress.records.filter(key='acquire').first() if flags.get('hasNote') else None
    return {'hasNote': bool(flags.get('hasNote')), 'mainCompleted': main.done(user, '6'),
            'acquiredAt': record.created_at.isoformat() if record else None,
            'noteDelivered': bool(flags.get('noteDelivered')), 'deferred': bool(flags.get('deferred')),
            'secretStarted': bool(flags.get('secretStarted')),
            'secretCompleted': bool(progress and progress.completed_at), 'topping': flags.get('topping')}


def action(user, data, session_uuid=None):
    progress,newly_completed=_write_action(user,data,session_uuid)
    result={'state':status(user,progress)}
    if newly_completed is not None:
        result.update(achievements=achievement_snapshot(user),newly_completed=newly_completed)
    return result


@learning_write_transaction
def _write_action(user,data,session_uuid):
    main.ensure_open(user)
    action = data.get('action')
    if action not in ('acquire', 'recognize', 'defer', 'start', 'topping', 'finish'):
        raise LearningError('지원하지 않는 쪽지 요청입니다.')
    get_user_model().objects.select_for_update().get(pk=user.pk)
    verify(user, data.get('ticket'), 'recipe-note' if action == 'acquire' else 'recipe-guide')
    if not main.done(user, '6'):
        raise LearningError(PREREQUISITE_MESSAGE, 'main_quest_required', 409)
    progress = StoryProgress.objects.filter(user=user, event_key=EVENT_KEY).first()
    if action == 'acquire':
        progress = progress or StoryProgress.objects.create(user=user, event_key=EVENT_KEY)
    elif not progress or not progress.flags.get('hasNote'):
        raise LearningError('먼저 레시피 쪽지를 찾아 챙겨 주세요.', 'note_required', 409)
    flags = dict(progress.flags)
    payload = {'objectId': NOTE_ID if action == 'acquire' else GUIDE_ID}
    if action == 'acquire':
        flags['hasNote'] = True
    elif action == 'recognize':
        flags['noteDelivered'] = True
    elif action in ('defer', 'start'):
        if not flags.get('noteDelivered'):
            raise LearningError('먼저 꾸왕이에게 쪽지를 보여 주세요.', 'recipe_prerequisite', 409)
        if action == 'defer':
            if flags.get('secretStarted'):
                raise LearningError('이미 함께 만들기를 시작했어요.', 'recipe_started', 409)
            flags['deferred'] = True
        else:
            flags['secretStarted'] = True
            flags.pop('deferred', None)
    elif action == 'topping':
        if not flags.get('secretStarted'):
            raise LearningError('먼저 꾸왕이와 함께 만들기를 선택해 주세요.', 'recipe_prerequisite', 409)
        topping = data.get('topping')
        if topping not in ('thanks', 'promise'):
            raise LearningError('마지막 토핑의 마음을 하나 선택해 주세요.')
        if flags.get('topping') and flags['topping'] != topping:
            raise LearningError('이미 선택한 마음으로 레시피를 완성해 주세요.', 'recipe_choice_locked', 409)
        flags['topping'] = payload['topping'] = topping
    elif action == 'finish':
        if not flags.get('secretStarted') or flags.get('topping') not in ('thanks', 'promise'):
            raise LearningError('함께 만들기와 마지막 토핑 선택을 먼저 마쳐 주세요.', 'recipe_prerequisite', 409)
        flags['secretCompleted'] = True
        payload['topping'] = flags['topping']
    if flags != progress.flags:
        progress.flags = flags
        progress.started_at = progress.started_at or timezone.now()
        if action == 'finish':
            progress.completed_at = timezone.now()
        progress.save(update_fields=['flags', 'started_at', 'completed_at', 'updated_at'])
    log(progress, action, 'recipe_note_acquired' if action == 'acquire' else 'recipe_' + action,
        payload, session_uuid)
    newly_completed=None
    if action == 'finish':
        newly_completed = []
        if complete_achievement(user, RECIPE_ID, progress.completed_at, session_uuid=session_uuid):
            newly_completed.append(str(RECIPE_ID))
    return progress,newly_completed
