# apps/core/serializers.py
from rest_framework import serializers


class TimestampMixin(serializers.Serializer):
    """Mixin pour afficher les timestamps formatés."""
    created_at = serializers.DateTimeField(format="%d/%m/%Y %H:%M", read_only=True)
    updated_at = serializers.DateTimeField(format="%d/%m/%Y %H:%M", read_only=True)


class ErrorSerializer(serializers.Serializer):
    """Format standard des erreurs API."""
    detail  = serializers.CharField()
    code    = serializers.CharField(required=False)
    field   = serializers.CharField(required=False)