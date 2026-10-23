"""Backend regressions without deployment databases, Redis, or Kafka."""
import os
from tempfile import TemporaryDirectory

_test_data_directory = TemporaryDirectory(prefix="playdata-onprem-tests-")

os.environ.update({
    "DATABASE_ENGINE": "sqlite",
    "MYSQL_POOL_SIZE": "0",
    "CAMPUS_REDIS_URL": "",
    "CAMPUS_MOVEMENT_REDIS_URL": "",
    "CAMPUS_SERVICE_REDIS_URL": "",
    "KAFKA_BOOTSTRAP_SERVERS": "",
    "CAMPUS_SESSION_CLEANUP_ENABLED": "0",
    "CAMPUS_DATA_DIR": _test_data_directory.name,
})

from config.settings import *  # noqa: E402,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
INSTALLED_APPS = [app for app in INSTALLED_APPS if app != "daphne"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
# 역할 분리 검사는 test_redis_roles에서 독립된 두 메모리 계층을 지정한다.
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CAMPUS_REDIS_URL = CAMPUS_MOVEMENT_REDIS_URL = CAMPUS_SERVICE_REDIS_URL = ""
KAFKA_BOOTSTRAP_SERVERS = ""
CAMPUS_SESSION_CLEANUP_ENABLED = False
ROOT_URLCONF = "config.urls"
