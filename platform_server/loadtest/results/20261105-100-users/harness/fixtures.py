"""Prepare staged scenarios in the owned DB, excluding walking and prerequisite time."""
import json
import os
from pathlib import Path
import sys
OUT = Path('/private/tmp/platform-load100-20261105')
ROOT = Path('/private/tmp/platform-event-pr-53')
os.environ.update(json.loads((OUT / 'environment.json').read_text()))
sys.path.insert(0, str(ROOT / 'backend'))
import django
django.setup()
from django.conf import settings
from django.db import connection
assert connection.settings_dict['PORT'] == '55479'
assert connection.settings_dict['NAME'] == 'load100_20261105'
from django.contrib.auth import get_user_model
from django.core import signing
from django.utils import timezone
from accounts.models import Position
from campus.motion import world
from event_story import main
from event_story.access import SALT
from event_story.models import EventProgress, GamePlay, StoryProgress, StoryRecord
from event_story.achievement_services import ACHIEVEMENT_IMAGES
from learning.models import Question

phase = sys.argv[1]
users = list(get_user_model().objects.filter(username__startswith='load').order_by('username'))
assert len(users) == 100
if phase == 'fullbank':
    from django.core.management import call_command
    for file in ('initial_subject_bank.json','expanded_questions_20261016.json','cs_questions.json'):
        call_command('seed_learning_questions',file=ROOT/'backend/learning/data'/file,verbosity=0)
    metadata=json.loads((OUT/'metadata.json').read_text())
    metadata['answers']={str(q.question_id):q.correct_answer for q in Question.objects.all()}
    (OUT/'metadata.json').write_text(json.dumps(metadata));(OUT/'metadata.json').chmod(0o600)
    from django.db.models import Count
    counts=list(Question.objects.values('subject','difficulty','question_type').annotate(n=Count('pk')).order_by('subject','difficulty','question_type'))
    (OUT/'question-bank-counts.json').write_text(json.dumps(counts,indent=2))
    print('question bank total:',Question.objects.count())
elif phase == 'reset_scenarios':
    import shutil
    archive=OUT/'exploratory';archive.mkdir(exist_ok=True)
    for name in ('scenario-samples.jsonl','scenario-summary.json','scenario-checks.json','scenarios.log',
            'scenario-samples-remaining.jsonl','scenario-summary-remaining.json','scenario-checks-remaining.json','remaining.log','audit.json'):
        if (OUT/name).exists() and not (archive/name).exists():shutil.copy2(OUT/name,archive/name)
    GamePlay.objects.all().delete()
    EventProgress.objects.filter(event_id__in=[*ACHIEVEMENT_IMAGES,main.uid('6')]).delete()
    StoryRecord.objects.filter(progress__event_key='pepero-2026-secret-recipe').delete()
    StoryProgress.objects.filter(event_key='pepero-2026-secret-recipe').delete()
    from learning.models import LearningSession,UserAnswer,PointLedger
    (OUT/'scenario-baseline.json').write_text(json.dumps({'learning_completed':LearningSession.objects.filter(completed_at__isnull=False).count(),
        'answers':UserAnswer.objects.count(),'ledger_rows':PointLedger.objects.count()}))
    metadata=json.loads((OUT/'metadata.json').read_text())
    for user,row in zip(users,metadata['users']):
        row['tickets']={target:signing.dumps({'user':str(user.pk),'target':target},salt=SALT)
            for target in ('investigate','room1','room2','roof-guide','final-guide','recipe-note','recipe-guide')}
    (OUT/'metadata.json').write_text(json.dumps(metadata));(OUT/'metadata.json').chmod(0o600)
elif phase == 'prepare':
    from django.core.management import call_command
    rows = []
    for file in ('initial_subject_bank.json', 'cs_questions.json', 'expanded_questions_20261016.json'):
        rows.extend(json.loads((ROOT / 'backend/learning/data' / file).read_text()))
    selected = []
    for subject in ('python', 'sql', 'cs'):
        for kind in ('multiple_choice', 'fill_blank'):
            existing = {r.get('question_code') for r in selected}
            group = [r for r in rows if r['subject'] == subject and r['difficulty'] == 'intermediate' and r['question_type'] == kind]
            unique = {r.get('question_code', r['question_text']): r for r in group}
            assert len(unique) >= 10, (subject, kind)
            selected.extend(list(unique.values())[:10])
    bank = OUT / 'selected-bank.json'; bank.write_text(json.dumps(selected))
    call_command('seed_learning_questions', file=bank, verbosity=0)
    metadata = []
    for user in users:
        # Prerequisite fixtures allow all game handlers and final-rank races to be tested independently.
        for code in ('1', '3', '4', 'card_match', 'coating', 'topping', 'wrapping', '5'):
            main.complete(user, code)
        metadata.append({'tickets': {target: signing.dumps({'user': str(user.pk), 'target': target}, salt=SALT)
            for target in ('investigate', 'room1', 'room2', 'roof-guide', 'final-guide', 'recipe-note', 'recipe-guide')}})
    answers = {str(q.question_id): q.correct_answer for q in Question.objects.all()}
    (OUT / 'metadata.json').write_text(json.dumps({'users': metadata, 'answers': answers}))
    (OUT / 'metadata.json').chmod(0o600)
elif phase.startswith('spawn:'):
    code = phase.split(':')[1]
    obj = world('campus').objects['minigame-' + code.replace('_', '-')]
    x, y = obj['interactionPoint']
    assert world('campus').walkable(x, y)
    Position.objects.filter(profile__user__in=users).update(last_position={'floor': 'campus', 'x': x, 'y': y, 'dir': 'up'},last_position_at=timezone.now())
elif phase in ('npc_prerequisites','poke_prerequisites'):
    from event_story.achievement_services import manager_ids,CAMPAIGN_ID
    from event_story.models import ManagerInteraction
    target='manager-yeongseok' if phase=='poke_prerequisites' else 'manager-suhyeon'
    for user in users:
        for manager_id in manager_ids():
            ManagerInteraction.objects.update_or_create(user=user,campaign_id=CAMPAIGN_ID,manager_id=manager_id,
                defaults={'greeted_at':None if manager_id=='manager-suhyeon' else timezone.now(),
                    'poke_count':10 if manager_id==target else 11})
elif phase in ('casual', 'manager', 'guide','refill'):
    obj=world('campus').objects.get('manager-suhyeon' if phase=='manager' else 'campus-guide-kkuwang')
    if phase=='refill':
        obj=world('campus').objects['pepero-lounge2-refill'];x,y=obj['interactionPoint']
    elif phase=='casual':
        point=world('campus').closest(640,480); x,y=point['x'],point['y']
    elif phase=='guide': x,y=1250,200
    else: x,y=obj['interactionPoint']
    assert world('campus').walkable(x,y)
    Position.objects.filter(profile__user__in=users).update(last_position={'floor':'campus','x':x,'y':y,'dir':'up'},last_position_at=timezone.now())
elif phase=='snapshot_api':
    from event_story import recipe
    expected=[]
    for user in users:
        expected.append({'main_completed':main.done(user,'6'),'recipe':recipe.status(user),
            'achievements':{str(p.event_id):[p.completed_at.isoformat(),p.completion_order] for p in EventProgress.objects.filter(user=user,event_id__in=ACHIEVEMENT_IMAGES,status='COMPLETED')}})
    (OUT/'expected-api.json').write_text(json.dumps(expected));(OUT/'expected-api.json').chmod(0o600)
elif phase == 'audit':
    from django.db.models import Count
    from learning.models import LearningSession, UserAnswer, PointLedger
    eggs = EventProgress.objects.filter(event_id__in=ACHIEVEMENT_IMAGES, status='COMPLETED')
    final = EventProgress.objects.filter(event_id=main.uid('6'), status='COMPLETED')
    ranks = list(final.values_list('completion_order', flat=True))
    result = {'egg_count': eggs.count(),
        'duplicate_users': list(eggs.values('user_id').annotate(n=Count('pk')).filter(n__gt=1)),
        'duplicate_eggs': list(eggs.values('event_id').annotate(n=Count('pk')).filter(n__gt=1)),
        'invalid_egg_metadata': eggs.filter(completion_order__isnull=True).count() + eggs.filter(completed_at__isnull=True).count(),
        'egg_order_not_one': eggs.exclude(completion_order=1).count(),
        'eggs_missing_session': eggs.filter(session__isnull=True).count(),
        'main_completed': final.count(), 'main_rank_unique': len(set(ranks)) == len(ranks),
        'main_ranks_contiguous': sorted(ranks) == list(range(1, len(ranks)+1)),
        'main_missing_session': final.filter(session__isnull=True).count(),
        'recipe_completed': StoryProgress.objects.filter(event_key='pepero-2026-secret-recipe', completed_at__isnull=False).count(),
        'plays': dict(GamePlay.objects.values('status').annotate(n=Count('pk')).values_list('status', 'n')),
        'learning_completed': LearningSession.objects.filter(completed_at__isnull=False).count(),
        'answers': UserAnswer.objects.count(), 'ledger_rows': PointLedger.objects.count()}
    assert not result['duplicate_users'] and not result['duplicate_eggs'] and not result['invalid_egg_metadata'], result
    assert not result['egg_order_not_one'] and not result['eggs_missing_session'],result
    assert result['main_rank_unique'] and result['main_ranks_contiguous'], result
    (OUT / 'audit.json').write_text(json.dumps(result, default=str, indent=2))
    print(json.dumps(result, default=str))
print('fixture phase:', phase)
