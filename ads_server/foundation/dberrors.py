"""DB 오류 분류. 오류의 **원인**을 구분해야 하는 곳(예: 멱등 INSERT 의 유일 제약 충돌)이 쓴다.

DB 는 PostgreSQL 하나다(Django 오류가 감싼 드라이버 오류 — `__cause__` — 의 SQLSTATE 로 구분한다). 모르는 오류는 '유일 제약 충돌이 아님'으로 답한다
— 모르면 숨기지 않고 올리는 쪽이 안전하다.
"""
from django.db import OperationalError

UNIQUE_VIOLATION = "23505"
# 교착(40P01)·잠금 대기 초과(55P03 — 접속 설정의 lock_timeout, settings.base.postgres_config). 둘 다 같은 일을 다시 하면 된다 — 데이터나 코드의 결함이 아니다.
# PostgreSQL 에서는 문장이 실패하면 그 트랜잭션이 중단 상태가 되어 되돌려야 다음 문장을 쓸 수 있다(저장점 없이 잡고 계속 쓰면 25P02 → 500).
# 그래서 잡는 쪽은 트랜잭션(저장점) 밖에서 잡는다 — IntegrityError 를 잡는 규칙(계획서 §6)과 같다.
TRANSIENT_SQLSTATES = frozenset({"40P01", "55P03"})


def sqlstate(error):
    """Django DB 오류가 감싼 드라이버 오류의 SQLSTATE. 없으면(연결 실패 등 서버가 답하지 않은 오류) None."""
    return getattr(getattr(error, "__cause__", None), "sqlstate", None)


def is_unique_violation(error):
    """`IntegrityError` 가 **유일 제약 위반**(중복 키)인가. CHECK·NOT NULL·외래 키 위반은 아니다."""
    return sqlstate(error) == UNIQUE_VIOLATION


def violated_constraint(error):
    """제약 위반(`IntegrityError`)이 어긴 제약의 이름(드라이버 오류의 진단 정보). 없으면 None."""
    return getattr(getattr(getattr(error, "__cause__", None), "diag", None), "constraint_name", None)


def is_transient_lock_error(error):
    """교착·잠금 대기 초과처럼 **다시 시도하면 되는** DB 오류인가. 실패 횟수를 세거나 반응을 포기시키는 근거가 되면 안 된다."""
    return isinstance(error, OperationalError) and sqlstate(error) in TRANSIENT_SQLSTATES


def cause_text(error):
    """로그 한 줄에 남길 원인: SQLSTATE, 없으면 메시지의 첫 줄. PostgreSQL 메시지는 여러 줄(DETAIL·CONTEXT)이라 그대로 쓰면 한 줄 로그가 깨진다."""
    code = sqlstate(error)
    if code:
        return code
    lines = str(error).strip().splitlines()
    return lines[0] if lines else "-"
