"""실험용: 운영 DB 처럼 쓰기 커밋을 느리게(prodlike_mysql, FAKE_COMMIT_MS 기본 5.8 → 로컬과 합쳐 운영의 7.5ms). 운영 설정이 아니다."""
from config.settings import *  # noqa: F401,F403
from config.settings import DATABASES

DATABASES["default"]["ENGINE"] = "prodlike_mysql"
