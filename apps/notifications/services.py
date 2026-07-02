from apps.notifications.models import Notification


class NotificationService:
    """Service centralisé de création de notifications."""

    @staticmethod
    def create(recipient, notification_type, title, body, data=None, channel='inapp'):
        """Crée une notification en base."""
        return Notification.objects.create(
            recipient=recipient,
            notification_type=notification_type,
            channel=channel,
            title=title,
            body=body,
            data=data or {},
        )