"""Use only an explicitly configured, disposable PostgreSQL test database."""
from .settings import *

if DATABASES['default']['ENGINE'] != 'django.db.backends.postgresql':
    raise RuntimeError('PostgreSQL integration tests require DATABASE_ENGINE=postgresql')
CAMPUS_SESSION_CLEANUP_ENABLED = False
CAMPUS_TEST_LEGACY_MOVEMENT = True
TEST_RUNNER = 'tests.runner.PostgreSQLTestRunner'
CAMPUS_REDIS_URL = CAMPUS_MOVEMENT_REDIS_URL = CAMPUS_SERVICE_REDIS_URL = ''
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher', 'django.contrib.auth.hashers.PBKDF2PasswordHasher']

# TransactionTestCase flushes lookup rows; restore the same fixed catalogue as fresh migrations.
def _restore_catalog(sender, **kwargs):
    if sender.name == 'eventing':
        from tests.test_postgres_platform import seed_catalog
        seed_catalog()
    elif sender.name == 'event_story':
        from importlib import import_module
        from django.apps import apps
        from django.db import connection
        from types import SimpleNamespace
        for name in ('0004_seed_easter_eggs','0005_learning_achievements'):
            import_module('event_story.migrations.' + name).seed(apps, SimpleNamespace(connection=connection))

from django.db.models.signals import post_migrate
post_migrate.connect(_restore_catalog, dispatch_uid='postgres_test_catalog', weak=False)
