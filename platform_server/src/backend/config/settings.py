"""Local Django settings for the campus map and user accounts."""
import os
from pathlib import Path

from .network import local_lan_addresses

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None
if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env", override=False)

# Relative data paths are resolved inside this backend, independently of frontend.
data_location = Path(os.environ.get("CAMPUS_DATA_DIR", BASE_DIR))
DATA_DIR = (data_location if data_location.is_absolute() else BASE_DIR / data_location).resolve()

# This fallback is for local development. A deployed server must provide its own key.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-campus-local-development")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
default_hosts = ",".join(["localhost", "127.0.0.1", "[::1]", *local_lan_addresses()])
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", default_hosts).split(",")
    if host.strip()
]

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "campus_control.apps.CampusControlConfig",
    "direct_messages.apps.DirectMessagesConfig",
    "daphne",
    "django.contrib.auth",
    "identity",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "accounts.apps.AccountsConfig",
    "campus.apps.CampusConfig",
    "event_story.apps.EventStoryConfig",
    "learning.apps.LearningConfig",
    "eventing.apps.EventingConfig",
    "community.apps.CommunityConfig",
    "ad_bridge.apps.AdBridgeConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "accounts.session_policy.CampusSessionPolicyMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "eventing.middleware.PlatformSessionMiddleware",
    "campus_control.middleware.CampusFreezeMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = []  # Presentation data only; HTML belongs to frontend.
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
# 기존 주소는 역할별 주소가 비어 있을 때 함께 사용한다.
CAMPUS_REDIS_URL = os.environ.get("CAMPUS_REDIS_URL", "").strip()
CAMPUS_MOVEMENT_REDIS_URL = os.environ.get("CAMPUS_MOVEMENT_REDIS_URL", "").strip() or CAMPUS_REDIS_URL
CAMPUS_SERVICE_REDIS_URL = os.environ.get("CAMPUS_SERVICE_REDIS_URL", "").strip() or CAMPUS_REDIS_URL
if bool(CAMPUS_MOVEMENT_REDIS_URL) != bool(CAMPUS_SERVICE_REDIS_URL):
    raise ValueError("Configure both CAMPUS_MOVEMENT_REDIS_URL and CAMPUS_SERVICE_REDIS_URL, or use CAMPUS_REDIS_URL.")
CHANNEL_LAYERS = {"default": (
    {"BACKEND": "channels_redis.pubsub.RedisPubSubChannelLayer", "CONFIG": {"hosts": [CAMPUS_MOVEMENT_REDIS_URL]}}
    if CAMPUS_MOVEMENT_REDIS_URL else {"BACKEND": "channels.layers.InMemoryChannelLayer"}
)}
# 같은 주소이면 하나의 계층을 재사용해 메모리 개발과 기존 단일 Redis 동작을 유지한다.
if CAMPUS_SERVICE_REDIS_URL != CAMPUS_MOVEMENT_REDIS_URL:
    CHANNEL_LAYERS["service"] = {"BACKEND": "channels_redis.pubsub.RedisPubSubChannelLayer",
                                 "CONFIG": {"hosts": [CAMPUS_SERVICE_REDIS_URL], "prefix": "campus.service"}}

DATABASE_ENGINE = os.environ.get("DATABASE_ENGINE", "postgresql").strip().lower()
if DATABASE_ENGINE in {"postgresql", "postgres", "pgsql"}:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "platform"),
        "USER": os.environ.get("POSTGRES_USER", "platform"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 0,
        "OPTIONS": {"connect_timeout": 5},
    }}
    pool_max_size = int(os.environ.get("POSTGRES_POOL_MAX_SIZE", "10"))
    if pool_max_size < 0:
        raise ValueError("POSTGRES_POOL_MAX_SIZE must be nonnegative; 0 disables pooling.")
    if pool_max_size:
        DATABASES["default"]["OPTIONS"]["pool"] = {"min_size": 1, "max_size": pool_max_size}
        DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
elif DATABASE_ENGINE == "mysql":
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.environ.get("MYSQL_DATABASE", ""),
        "USER": os.environ.get("MYSQL_USER", ""),
        "PASSWORD": os.environ.get("MYSQL_PASSWORD", ""),
        "HOST": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "PORT": os.environ.get("MYSQL_PORT", "3306"),
        "OPTIONS": {"charset": "utf8mb4"},
        "CONN_MAX_AGE": 0,
    }}
    if not DATABASES["default"]["NAME"] or not DATABASES["default"]["USER"]:
        raise ValueError("MYSQL_DATABASE and MYSQL_USER are required when DATABASE_ENGINE=mysql")
elif DATABASE_ENGINE == "sqlite":
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": DATA_DIR / "db.sqlite3"}}
else:
    raise ValueError("DATABASE_ENGINE must be postgresql, sqlite or mysql")
AUTH_USER_MODEL = "identity.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "campus:map"
LOGOUT_REDIRECT_URL = "accounts:login"
SESSION_COOKIE_NAME = "campus_sessionid"
CSRF_COOKIE_NAME = "campus_csrftoken"

# Seconds; configure before starting the server. Activity never moves the fixed login deadline.
CAMPUS_LOGIN_MAX_AGE_SECONDS = int(os.environ.get("CAMPUS_LOGIN_MAX_AGE_SECONDS", 8 * 60 * 60))
CAMPUS_LOGIN_IDLE_TIMEOUT_SECONDS = int(os.environ.get("CAMPUS_LOGIN_IDLE_TIMEOUT_SECONDS", 30 * 60))
CAMPUS_LOGIN_WARNING_SECONDS = int(os.environ.get("CAMPUS_LOGIN_WARNING_SECONDS", 60))
CAMPUS_SESSION_ACTIVITY_WRITE_INTERVAL_SECONDS = int(os.environ.get("CAMPUS_SESSION_ACTIVITY_WRITE_INTERVAL_SECONDS", 15))
if min(CAMPUS_LOGIN_MAX_AGE_SECONDS, CAMPUS_LOGIN_IDLE_TIMEOUT_SECONDS,
       CAMPUS_LOGIN_WARNING_SECONDS, CAMPUS_SESSION_ACTIVITY_WRITE_INTERVAL_SECONDS) <= 0:
    raise ValueError("Campus session durations must be positive seconds")
SESSION_COOKIE_AGE = CAMPUS_LOGIN_MAX_AGE_SECONDS
CAMPUS_SESSION_CLEANUP_ENABLED = os.environ.get("CAMPUS_SESSION_CLEANUP_ENABLED", "1") == "1"
CAMPUS_SESSION_CLEANUP_TIME = os.environ.get("CAMPUS_SESSION_CLEANUP_TIME", "04:00")
CAMPUS_SESSION_CLEANUP_STATE = DATA_DIR / "var" / "session-cleanup.json"

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "/static/"
STATICFILES_DIRS = []
STATIC_ROOT = BASE_DIR / "staticfiles"

# The public cafeteria channel changes its profile photo to the current menu.
BOARD_MENU_SOURCE_URL = "https://pf.kakao.com/_xfWxfCxj"
BOARD_MENU_STORAGE_ROOT = DATA_DIR / "var" / "board-menu"
BOARD_MENU_UPDATE_TIME = "10:00"
BOARD_MENU_TIME_ZONE = "Asia/Seoul"

# Actual per-correct-answer rewards; shared by multiple-choice and fill-blank.
LEARNING_POINTS_BY_DIFFICULTY = {
    'beginner': int(os.environ.get('LEARNING_POINTS_BEGINNER', '20')),
    'intermediate': int(os.environ.get('LEARNING_POINTS_INTERMEDIATE', '50')),
    'advanced': int(os.environ.get('LEARNING_POINTS_ADVANCED', '80')),
}
if any(not 0 <= amount < 2**63 for amount in LEARNING_POINTS_BY_DIFFICULTY.values()):
    raise ValueError('Learning rewards must be non-negative signed 64-bit integers')

# The local frontend forwards the original public scheme for CSRF origin checks.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Private messages are enabled explicitly for each environment.
CAMPUS_DM_ENABLED = os.environ.get("CAMPUS_DM_ENABLED", "1") == "1"

# 운영 슬롯을 준비한 환경에서만 광고 요청을 활성화한다.
ADS_ENABLED = os.environ.get("ADS_ENABLED", "0") == "1"
ADS_ROOFTOP_SLOT_ID = os.environ.get("ADS_ROOFTOP_SLOT_ID", "")
ADS_PORTAL_LEFT_SLOT_ID = os.environ.get("ADS_PORTAL_LEFT_SLOT_ID", "")
ADS_PORTAL_RIGHT_SLOT_ID = os.environ.get("ADS_PORTAL_RIGHT_SLOT_ID", "")

# Event main story: production follows the KST event day; local preview is explicit.
EVENT_STORY_DATE = os.environ.get("EVENT_STORY_DATE", "2026-11-11")
EVENT_STORY_PREVIEW = os.environ.get("EVENT_STORY_PREVIEW", "0") == "1"

# Optional future --lookup mode only. Default click delivery uses the existing reaction PUT.
ADS_CLICK_LOOKUP_BACKEND = os.environ.get("ADS_CLICK_LOOKUP_BACKEND", "")

ADS_CLICK_LOOKUP_PATH = os.environ.get("ADS_CLICK_LOOKUP_PATH", "")
