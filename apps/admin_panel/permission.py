# apps/admin_panel/permissions.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from apps.admin_panel.middleware import get_admin_from_request


class AdminAPIView(APIView):
    """
    Base class pour toutes les vues admin.
    Remplace IsAuthenticated — vérifie le token admin JWT.
    """
    permission_classes = []

    def dispatch(self, request, *args, **kwargs):
        admin, error = get_admin_from_request(request)
        if error:
            return Response(
                {'success': False, 'errors': {'detail': error}},
                status=status.HTTP_401_UNAUTHORIZED
            )
        request.admin = admin
        return super().dispatch(request, *args, **kwargs)