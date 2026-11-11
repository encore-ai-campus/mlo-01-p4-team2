"""Display-only Django: templates, static assets and transparent backend transport."""
import os
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv
from .network import local_lan_addresses

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env', override=False)
SECRET_KEY = os.environ.get('FRONTEND_SECRET_KEY', 'django-insecure-campus-display-local')
DEBUG = os.environ.get('FRONTEND_DEBUG', '1') == '1'
default_hosts = ','.join(['localhost', '127.0.0.1', '[::1]', *local_lan_addresses()])
ALLOWED_HOSTS = [host.strip() for host in os.environ.get('FRONTEND_ALLOWED_HOSTS', default_hosts).split(',') if host.strip()]
INSTALLED_APPS = ['daphne', 'django.contrib.staticfiles', 'pages', 'accounts', 'learning']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.middleware.common.CommonMiddleware',
              'django.middleware.clickjacking.XFrameOptionsMiddleware']
# CSRF and authentication are checked by the backend after forwarding the original request.
# No frontend session, auth model, DB driver or migration is needed.
DATABASES = {}
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'
TEMPLATES = []  # pages.rendering uses a UI-only engine with explicit backend context.
STATIC_URL = '/static/'
STATICFILES_DIRS = [('campus', BASE_DIR / 'dist')]
STATIC_ROOT = BASE_DIR / 'staticfiles'
LANGUAGE_CODE = 'ko-kr'
TIME_ZONE = 'Asia/Seoul'
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
BACKEND_URL = os.environ.get('BACKEND_URL', 'http://127.0.0.1:9003').rstrip('/')
MOVEMENT_BACKEND_URL = os.environ.get('MOVEMENT_BACKEND_URL', BACKEND_URL).rstrip('/')
for name, server_url in (('BACKEND_URL', BACKEND_URL), ('MOVEMENT_BACKEND_URL', MOVEMENT_BACKEND_URL)):
    parsed_backend = urlsplit(server_url)
    if parsed_backend.scheme not in {'http', 'https'} or not parsed_backend.hostname or parsed_backend.username or parsed_backend.password or parsed_backend.path not in {'', '/'} or parsed_backend.query or parsed_backend.fragment:
        raise ValueError(f'{name} must be a plain HTTP(S) server address')
BACKEND_TIMEOUT = float(os.environ.get('BACKEND_TIMEOUT', '30'))
BACKEND_HTTP_KEEPALIVE = os.environ.get('BACKEND_HTTP_KEEPALIVE', '1') == '1'
BACKEND_HTTP_POOL_MAXSIZE = int(os.environ.get('BACKEND_HTTP_POOL_MAXSIZE', '100'))
BACKEND_HTTP_IDLE_TIMEOUT = float(os.environ.get('BACKEND_HTTP_IDLE_TIMEOUT', '20'))
if BACKEND_TIMEOUT <= 0 or BACKEND_HTTP_POOL_MAXSIZE <= 0 or BACKEND_HTTP_IDLE_TIMEOUT <= 0:
    raise ValueError('HTTP timeout, pool size and idle timeout must be positive')


# The trusted edge proxy must overwrite this header and restrict direct app access.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
