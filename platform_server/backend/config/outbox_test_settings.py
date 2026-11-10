"""Isolate outbox checks from deployment databases and background workers."""
import tempfile

from .settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
INSTALLED_APPS = [app for app in INSTALLED_APPS if app != "daphne"]
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CAMPUS_REDIS_URL = ""
KAFKA_BOOTSTRAP_SERVERS = ""
CAMPUS_SESSION_CLEANUP_ENABLED = False
CAMPUS_OUTBOX_CLEANUP_STATE = Path(tempfile.gettempdir()) / "platform-outbox-test-cleanup.json"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
ROOT_URLCONF = __name__
urlpatterns = []
