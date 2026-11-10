"""Shared authentication and explicit rejection for role-specific ASGI routes."""
from channels.auth import AuthMiddlewareStack
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.routing import URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.urls import re_path


class RejectedSocket(AsyncWebsocketConsumer):
    async def connect(self):
        await self.close(code=4404)


def authenticated_sockets(patterns):
    return AllowedHostsOriginValidator(AuthMiddlewareStack(URLRouter([
        *patterns, re_path(r".*", RejectedSocket.as_asgi()),
    ])))


async def reject_http(scope, receive, send):
    await send({"type": "http.response.start", "status": 404, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"Movement endpoint accepts WebSocket traffic only."})
