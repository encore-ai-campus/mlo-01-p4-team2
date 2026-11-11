from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.cache import never_cache
from learning.views import endpoint, body
from eventing.events import SESSION_KEY
from . import main

@never_cache
@ensure_csrf_cookie
@endpoint('GET')
def status(request):
    return JsonResponse(main.status(request.user))

@never_cache
@endpoint('GET')
def ranking(request):
    try:page=max(1,min(10000,int(request.GET.get('page',1))))
    except ValueError:page=1
    return JsonResponse(main.ranking(request.user,page))

@never_cache
@endpoint('POST')
def action(request):
    return JsonResponse(main.action(request.user, body(request), request.session.get(SESSION_KEY)))
