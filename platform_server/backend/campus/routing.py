from django.urls import path

from .consumers import CampusConsumer

websocket_urlpatterns = [path("ws/movement/", CampusConsumer.as_asgi()), path("ws/campus/", CampusConsumer.as_asgi())]
