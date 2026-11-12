import json
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from . import service


@never_cache
@ensure_csrf_cookie
@require_http_methods(['GET', 'POST'])
def freeze(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': '로그인이 필요합니다.'}, status=401)
    from . import event_access
    if request.method == 'POST' and 'eventMode' in request.body.decode('utf-8', errors='replace'):
        if not request.user.is_active or not request.user.is_staff:
            return JsonResponse({'error':'관리자만 이벤트 상태를 변경할 수 있습니다.'},status=403)
        try:
            if request.content_type!='application/json' or len(request.body)>3000:raise ValueError
            data=json.loads(request.body)
            if not isinstance(data,dict) or data.get('eventMode') not in event_access.MODES or type(data.get('eventRevision')) is not int or data['eventRevision']<0:raise ValueError
        except (ValueError,UnicodeError,RecursionError):
            return JsonResponse({'error':'이벤트 모드와 변경 번호를 확인해 주세요.'},status=400)
        try:
            state=event_access.change(request.user,data['eventMode'],data['eventRevision'])
        except event_access.RevisionConflict:
            return JsonResponse({'error':'다른 관리자가 변경했습니다. 최신 상태에서 다시 선택해 주세요.','eventAccess':event_access.snapshot()},status=409)
        return JsonResponse({'eventAccess':state})
    if request.method == 'POST':
        if not request.user.is_active or not request.user.is_staff:
            return JsonResponse({'error': '관리자만 이용 상태를 변경할 수 있습니다.'}, status=403)
        try:
            if request.content_type != 'application/json' or len(request.body) > 3000:
                raise ValueError
            data = json.loads(request.body)
            if (not isinstance(data, dict) or data.get('freezeMode','MANUAL') not in ('AUTO','OPEN','CLOSED','MANUAL') or type(data.get('paused')) is not bool
                    or type(data.get('revision')) is not int or data['revision'] < 0
                    or not isinstance(data.get('message', ''), str)
                    or len(data.get('message', '')) > 160):
                raise ValueError
        except (ValueError, UnicodeError, RecursionError):
            return JsonResponse({'error': '정지 상태와 안내 문구를 확인해 주세요.'}, status=400)
        try:
            mode=data.get('freezeMode','MANUAL')
            paused=True if mode=='CLOSED' else False if mode=='OPEN' else data['paused']
            state = service.change(request.user, paused, data.get('message', '').strip(), data['revision'],mode='AUTO' if mode=='AUTO' else 'MANUAL')
        except service.RevisionConflict:
            return JsonResponse({'error': '다른 관리자가 상태를 변경했습니다. 최신 상태를 확인해 주세요.',
                                 **service.read_state(force=True)}, status=409)
    else:
        state = service.read_state()
    payload = {**service.for_user(state,request.user), 'eventAccess': event_access.snapshot()}
    if state['paused']:
        # The lecture pause must not look like user inactivity. Absolute login
        # lifetime and single-login checks still apply; never revive expired sessions.
        from accounts.session_policy import check_session
        payload['session'] = check_session(request.session.session_key, touch=True)
    return JsonResponse(payload, status=503 if state.get('unavailable') else 200)
