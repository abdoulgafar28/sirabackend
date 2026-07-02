from rest_framework import serializers
from apps.notifications.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    notification_type_display = serializers.CharField(
        source='get_notification_type_display', read_only=True
    )

    class Meta:
        model  = Notification
        fields = [
            'id', 'notification_type', 'notification_type_display',
            'channel', 'title', 'body', 'data',
            'is_read', 'is_sent', 'sent_at', 'read_at',
            'created_at',
        ]
        read_only_fields = '__all__'


class NotificationMarkReadSerializer(serializers.Serializer):
    """Marquer une ou plusieurs notifications comme lues."""
    notification_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=100,
    )


class DeviceTokenSerializer(serializers.Serializer):
    """Enregistrement du token push mobile (Firebase FCM)."""
    device_token = serializers.CharField(max_length=500)
    platform     = serializers.ChoiceField(choices=[('android', 'Android'), ('ios', 'iOS')])