from django.urls import re_path
from apps.tracking.routing import websocket_urlpatterns as tracking_ws
from apps.tracking.consumer import RideConsumer

# Fusionner toutes les routes WebSocket
websocket_urlpatterns = tracking_ws + [
    re_path(r'^ws/surveillance/$', RideConsumer.as_asgi(), name='ws-surveillance'),
]