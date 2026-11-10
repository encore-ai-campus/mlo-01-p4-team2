"""Local composite; deploy movement_asgi and service_asgi as separate processes."""
from .movement_asgi import application as movement_application
from .service_asgi import application as service_application


async def application(scope, receive, send):
    if scope["type"] == "websocket" and scope["path"] in {"/ws/movement/", "/ws/campus/"}:
        await movement_application(scope, receive, send)
    else:
        await service_application(scope, receive, send)
