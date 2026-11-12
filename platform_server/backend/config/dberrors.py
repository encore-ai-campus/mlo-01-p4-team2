"""DB 를 잠깐 못 쓰는 오류를 가려 503 으로 알린다(PostgreSQL 전환 결정 D5).

일시 오류: OperationalError(연결 끊김·잠금 대기 초과 55P03·교착 등)와, 장애 전환 중 읽기 전용이 된 서버에 쓴 25006.
"""
import sys

from django.db import DatabaseError, OperationalError
from django.http import JsonResponse
from django.views import defaults


def sqlstate(error):
    """드라이버 오류의 SQLSTATE(PostgreSQL). 없으면 None."""
    return getattr(error.__cause__, "sqlstate", None)


def is_transient(error):
    return isinstance(error, OperationalError) or (isinstance(error, DatabaseError) and sqlstate(error) == "25006")


def server_error(request):
    """handler500: 뷰와 미들웨어에서 난 예외가 모두 여기로 온다. 일시 DB 오류만 503, 나머지는 기존 500."""
    if is_transient(sys.exc_info()[1]):
        response = JsonResponse({"error": "database_busy", "message": "잠시 후 다시 시도해 주세요."}, status=503)
        response["Retry-After"] = "1"
        return response
    return defaults.server_error(request)
