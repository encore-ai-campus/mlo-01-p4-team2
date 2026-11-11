"""Movement-only frontend: health checks and the movement WebSocket bridge."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django_application = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from django.urls import path, re_path
from pages.sockets import BackendSocket


async def movement_http(scope, receive, send):
    if scope['path'] == '/health/':
        await django_application(scope, receive, send)
        return
    await send({'type': 'http.response.start', 'status': 404,
                'headers': [(b'content-type', b'text/plain; charset=utf-8')]})
    await send({'type': 'http.response.body', 'body': b'Not Found'})


async def reject_socket(scope, receive, send):
    await send({'type': 'websocket.close', 'code': 4404})


application = ProtocolTypeRouter({
    'http': movement_http,
    'websocket': URLRouter([
        path('ws/movement/', BackendSocket.as_asgi()),
        re_path(r'.*', reject_socket),
    ]),
})
