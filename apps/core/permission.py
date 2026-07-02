# apps/core/permissions.py
from rest_framework.permissions import BasePermission


class IsClient(BasePermission):
    """Réservé aux utilisateurs avec le rôle client."""
    message = "Accès réservé aux clients."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            request.user.role == 'client' and
            request.user.status == 'active'
        )


class IsDriver(BasePermission):
    """Réservé aux conducteurs validés."""
    message = "Accès réservé aux conducteurs actifs."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            request.user.role == 'driver' and
            request.user.status == 'active' and
            hasattr(request.user, 'driver_profile') and
            request.user.driver_profile.validation_status == 'approved'
        )


class IsAdminUser(BasePermission):
    """Réservé aux administrateurs SIRA."""
    message = "Accès réservé aux administrateurs."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            request.user.role == 'admin'
        )


class IsDriverOrClient(BasePermission):
    """Conducteur OU client — tous les utilisateurs actifs."""
    message = "Accès non autorisé."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            request.user.role in ['client', 'driver'] and
            request.user.status == 'active'
        )


class IsOwnerOrAdmin(BasePermission):
    """Propriétaire de la ressource OU admin."""
    message = "Vous n'avez pas accès à cette ressource."

    def has_object_permission(self, request, view, obj):
        if request.user.role == 'admin':
            return True
        # Chercher un champ 'user' ou 'client' sur l'objet
        owner = getattr(obj, 'user', None) or getattr(obj, 'client', None)
        return owner == request.user