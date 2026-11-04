"""메타버스 더미 도구의 시험 시계(계획 MD-1 §4.1): 앱의 `django.utils.timezone.now`를 "과거 시작 시각 + 실제 경과 × 배속"으로 바꾼다. 앱 코드는 바꾸지 않는다.

- 공식: `sim = S0 + (time.time() − R0) × speed`. 백엔드(daphne)와 도구(mv_load.py)가 같은 S0·R0·speed를 쓰므로 두 프로세스의 시계가 같다.
- 프로세스 안에서 늘 앞으로만 간다(이전 값보다 작아지지 않음). 실제 지금을 넘지 않는다(넘으면 실제 지금에 머문다 — 도구는 계획 끝 시각이 실행 시작보다 앞일 때만 돈다).
- Django보다 먼저 `install()` 해야 한다. 앱은 `timezone.now()`(모듈 속성)로 부르므로 그 뒤의 모든 호출이 바뀐다(`auto_now_add`·세션 만료·사건 시각).
- `start()` 전에는 실제 시각을 돌려준다(도구의 시드 단계). `start()`로 시계를 켜면 그때부터 시험 시각이다.
- 백엔드: `cd <백엔드> && PYTHONPATH=<이 폴더> MV_SIM_S0=… MV_SIM_R0=… MV_SIM_SPEED=… python -m daphne … mv_simclock:application`
  — daphne가 `application`을 처음 찾을 때 시계를 켜고 `config.asgi`를 불러온다.
"""
import os
import threading
import time
from datetime import datetime, timezone

_lock = threading.Lock()
_clock = None  # (s0, r0, speed). None이면 실제 시각
_last = float("-inf")


def sim_ts(s0, r0, speed, real):
    """시험 시각(UNIX 초). 실제 지금(real)을 넘지 않는다."""
    return min(s0 + (real - r0) * speed, real)


def now_ts():
    global _last
    real = time.time()
    with _lock:
        _last = max(_last, real if _clock is None else sim_ts(*_clock, real))
        return _last


def now():
    return datetime.fromtimestamp(now_ts(), timezone.utc)


def start(s0, r0, speed):
    global _clock, _last
    s0, r0, speed = float(s0), float(r0), float(speed)
    if not speed > 0:
        raise ValueError("speed는 0보다 커야 한다")
    with _lock:
        _clock, _last = (s0, r0, speed), float("-inf")


def install():
    from django.utils import timezone as django_timezone
    django_timezone.now = now


def __getattr__(name):
    if name != "application":
        raise AttributeError(name)
    install()
    start(os.environ["MV_SIM_S0"], os.environ["MV_SIM_R0"], os.environ["MV_SIM_SPEED"])
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from config.asgi import application
    globals()["application"] = application
    return application
