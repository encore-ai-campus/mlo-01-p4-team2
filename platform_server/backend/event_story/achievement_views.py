import json
from django.http import JsonResponse
from django.db import DatabaseError
from eventing.events import SESSION_KEY
from .game_services import GameError, finish_game
from django.views.decorators.cache import never_cache

from .achievement_services import achievement_snapshot
from .main_achievement import snapshot as main_badge


@never_cache
def achievements(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "다시 로그인해 주세요."}, status=401)
    if request.method != "GET":
        response = JsonResponse({"error": "지원하지 않는 요청입니다."}, status=405)
        response["Allow"] = "GET"
        return response
    return JsonResponse({"achievements": achievement_snapshot(request.user), "main_achievement": main_badge(request.user)})


@never_cache
def game_result(request, play_uuid):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "다시 로그인해 주세요."}, status=401)
    if request.method != "POST":
        response = JsonResponse({"error": "지원하지 않는 요청입니다."}, status=405)
        response["Allow"] = "POST"
        return response
    try:
        data = json.loads(request.body)
        return JsonResponse(finish_game(request.user, play_uuid, request.session.get(SESSION_KEY), data))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "게임 결과 형식을 확인해 주세요."}, status=400)
    except GameError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    except DatabaseError:
        return JsonResponse({"error": "결과를 저장하지 못했어요. 다시 시도해 주세요."}, status=503)
