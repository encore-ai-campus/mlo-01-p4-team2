from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.cache import never_cache
from learning.views import endpoint, body
from eventing.events import SESSION_KEY
from . import services


@never_cache
@ensure_csrf_cookie
@endpoint('GET')
def status(request):
    return JsonResponse(services.status(request.user))


@endpoint('POST')
def action(request):
    return JsonResponse(services.act(request.user, body(request), request.session.get(SESSION_KEY)))
