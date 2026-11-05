"""Proof of proximity is issued by the authoritative movement connection."""
from django.core import signing
from campus.motion import ControlError

TARGETS = {
    'recipe-note': ('rooftop', 'hidden-recipe-note'),
    'recipe-guide': ('campus', 'campus-guide-kkuwang'),
    'board': ('campus', 'class3-4-noticeboard'),
    'wrapper': ('campus', 'campus-guide-kkuwang'),
    'count': ('campus', 'campus-guide-kkuwang'),
    'quantity': ('campus', 'campus-guide-kkuwang'),
    'ethics': ('rooftop', 'festival-instagram-interaction'),
}
SALT = 'campus.story.interaction.v1'


def issue(user_id, controller, data):
    target = data.get('target')
    if target in {'room1','room2','roof-guide','investigate','final-guide'}:
        from math import hypot
        if target in {'room1','room2'}:
            n=int(target[-1])
            if not controller.interior or controller.interior.get('missionRoom')!=n or not controller.private_near() or controller.private_near()['id']=='classroom-exit':
                raise ControlError('미션 방 안의 작업대 또는 매니저님 가까이 이동해 주세요.')
        else:
            # Keep existing ticket names so saved progress/results remain compatible.
            # All three interactions now require the single physical 18F guide.
            obj=controller.world.objects.get('campus-guide-kkuwang')
            if controller.interior or controller.pose['floor']!='campus' or not obj or controller.world.distance(obj,controller.pose)>100:
                raise ControlError('18층 꾸왕이 가까이에서 상호작용해 주세요.')
        controller.input([]);controller.route=[]
        details={'user':str(user_id),'target':target}
        return {'type':'story_access','ticket':signing.dumps(details,salt=SALT),**details}
    if not isinstance(target, str) or target not in TARGETS:
        raise ControlError('미션 대상을 확인해 주세요.')
    details = {'user': str(user_id), 'target': target}
    floor, object_id = TARGETS[target]
    obj = controller.world.objects.get(object_id)
    if (not obj or controller.interior or controller.pose['floor'] != floor
            or controller.world.distance(obj, controller.pose) > obj.get('interactionRadius', 80)):
        raise ControlError('목표 가까이 이동한 뒤 상호작용해 주세요.')
    controller.input([])
    controller.route = []
    return {'type': 'story_access', 'ticket': signing.dumps(details, salt=SALT), **details}


def verify(user, token, target):
    from learning.services import LearningError
    try:
        value = signing.loads(token, salt=SALT, max_age=900)
        if value.get('user') != str(user.pk) or value.get('target') != target:
            raise signing.BadSignature()
        return value
    except (signing.BadSignature, TypeError, ValueError):
        raise LearningError('대상 가까이에서 다시 열어 주세요.', 'interaction_expired', 409)
