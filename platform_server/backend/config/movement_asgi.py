import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from channels.routing import ProtocolTypeRouter
from campus.routing import websocket_urlpatterns
from .asgi_routes import authenticated_sockets, reject_http

application = ProtocolTypeRouter({"http": reject_http, "websocket": authenticated_sockets(websocket_urlpatterns)})
