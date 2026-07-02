"""
ASGI config for config project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/asgi/
"""

import os
import sys

from pathlib import Path  

os.environ.setdefault(
    'DJANGO_SETTINGS_MODULE',
    'config.settings.development'
)



import django
django.setup()


BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


from django.core.asgi import get_asgi_application
from apps.tracking.routing import websocket_urlpatterns
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from channels.security.websocket import AllowedHostsOriginValidator
from apps.tracking.middleware import JWTAuthMiddleware





application = ProtocolTypeRouter({
    'http':      get_asgi_application(),
    'websocket': AllowedHostsOriginValidator(
        JWTAuthMiddleware(
            URLRouter(websocket_urlpatterns)
        )
    ),
})


