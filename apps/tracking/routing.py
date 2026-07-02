# apps/tracking/routing.py
from django.urls import re_path
from apps.tracking import consumer


websocket_urlpatterns = [
    # Conducteur envoie sa position + reçoit les demandes de course
    re_path(
        r'^ws/driver/$',
        consumer.DriverConsumer.as_asgi(),
        name='ws-driver',
    ),

    # Client suit sa course en temps réel
    re_path(
        r'^ws/ride/(?P<ride_id>[0-9A-Fa-f-]+)/$',
        consumer.RideConsumer.as_asgi(),
        name='ws-ride',
    ),

    # ← AJOUTER : Surveillance admin
    re_path(
        r'^ws/surveillance/$',
        consumer.DriverConsumer.as_asgi(),
        name='ws-surveillance',
    ),

]














