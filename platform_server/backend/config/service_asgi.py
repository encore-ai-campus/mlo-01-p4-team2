import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
from django.core.asgi import get_asgi_application
django_application = get_asgi_application()

from channels.routing import ProtocolTypeRouter
from django.urls import path
from campus.chat import ChatConsumer
from .asgi_routes import authenticated_sockets

application = ProtocolTypeRouter({"http": django_application,
    "websocket": authenticated_sockets([path("ws/chat/", ChatConsumer.as_asgi())])})
