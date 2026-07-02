# apps/tracking/middleware.py
import logging
from urllib.parse import parse_qs
from channels.middleware import BaseMiddleware
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser


logger = logging.getLogger('apps')

@database_sync_to_async
def get_user_from_token(token: str):
    """Récupère l'utilisateur depuis un JWT token."""
    try:
        from rest_framework_simplejwt.tokens import AccessToken
        from django.contrib.auth import get_user_model
        User = get_user_model()

        access_token = AccessToken(token)
        user_id      = access_token['user_id']
        return User.objects.get(id=user_id)

    except Exception as e:
        logger.warning(f"JWT WebSocket auth échoué : {e}")
        return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    """
    Middleware d'authentification JWT pour les WebSockets.
    Le token est passé en query string :
    ws://localhost:8000/ws/tracking/?token=xxx
    """
    async def __call__(self, scope, receive, send):
        query_string = scope.get('query_string', b'').decode()
        params       = parse_qs(query_string)
        token_list   = params.get('token', [])

        if token_list:
            scope['user'] = await get_user_from_token(token_list[0])
        else:
            scope['user'] = AnonymousUser()

        return await super().__call__(scope, receive, send)