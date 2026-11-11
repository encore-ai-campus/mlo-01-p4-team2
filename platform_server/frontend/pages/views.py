import http.client
import json
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET
from .transport import call_backend, apply_headers, passthrough
from .rendering import render_document

NOTICE = '<!doctype html><html lang="ko"><meta charset="utf-8"><title>연결 확인</title><main><h1>서버 연결을 확인해 주세요</h1><p>잠시 후 다시 시도해 주세요.</p></main></html>'

def unavailable(request):
    if request.path.startswith('/api/'):
        return JsonResponse({'error': 'backend_unavailable', 'message': '서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.'}, status=502)
    return HttpResponse(NOTICE, status=502)

def relay(request, backend):
    # 백엔드 503(DB 를 잠깐 못 씀): API 는 JSON 그대로, 화면 요청은 502 처럼 HTML 안내로. Retry-After 는 둘 다 넘긴다
    if backend.status == 503 and not request.path.startswith('/api/'):
        return apply_headers(HttpResponse(NOTICE, status=503), backend, html=True)
    return passthrough(backend)

def forward(request, **kwargs):
    try:
        backend = call_backend(request)
    except (OSError, http.client.HTTPException, TimeoutError):
        return unavailable(request)
    return relay(request, backend)

def display(request, **kwargs):
    try:
        backend = call_backend(request)
    except (OSError, http.client.HTTPException, TimeoutError):
        return unavailable(request)
    if backend.status not in {200, 400}:
        return relay(request, backend)
    try:
        data = json.loads(backend.body)
        if 'presentation' not in data:
            return passthrough(backend)
        html, _ = render_document(data['presentation'], settings.BASE_DIR, settings.DEBUG)
    except (ValueError, KeyError, TypeError):
        return unavailable(request)
    return apply_headers(HttpResponse(html, status=backend.status), backend, html=True)

@require_GET
def health(request):
    return JsonResponse({'service': 'frontend', 'status': 'ok'})
