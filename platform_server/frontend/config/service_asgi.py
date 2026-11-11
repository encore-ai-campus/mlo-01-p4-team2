"""Service frontend: existing HTTP pages/APIs and the chat WebSocket bridge."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django_application = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from django.urls import path, re_path
from pages.sockets import BackendSocket


async def reject_socket(scope, receive, send):
    await send({'type': 'websocket.close', 'code': 4404})


application = ProtocolTypeRouter({
    'http': django_application,
    'websocket': URLRouter([
        path('ws/chat/', BackendSocket.as_asgi()),
        re_path(r'.*', reject_socket),
    ]),
})
