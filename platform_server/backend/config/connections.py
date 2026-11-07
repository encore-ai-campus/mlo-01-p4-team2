"""풀을 쓰지 않을 때만 소켓 작업 스레드의 연결을 60초간 재사용한다."""
from django.db import connections

KEEP_SECONDS = 60


def keep_thread_connection():
    database = connections["default"]
    if database.settings_dict.get("OPTIONS", {}).get("pool"):
        return  # 풀은 CONN_MAX_AGE=0으로 연결을 반환한다.
    if database.settings_dict.get("CONN_MAX_AGE") != KEEP_SECONDS:
        database.settings_dict = {**database.settings_dict, "CONN_MAX_AGE": KEEP_SECONDS, "CONN_HEALTH_CHECKS": True}
