

# Create your models here.


import uuid
from django.db import models
from apps.users.models import User


class Notification(models.Model):
    """
    Notifications système envoyées aux utilisateurs.
    Supporte push mobile, SMS et in-app.
    """
    class NotificationType(models.TextChoices):
        RIDE_REQUEST    = 'ride_request',    'Nouvelle demande de course'
        RIDE_ACCEPTED   = 'ride_accepted',   'Course acceptée'
        RIDE_STARTED    = 'ride_started',    'Course démarrée'
        RIDE_COMPLETED  = 'ride_completed',  'Course terminée'
        RIDE_CANCELLED  = 'ride_cancelled',  'Course annulée'
        DRIVER_ARRIVED  = 'driver_arrived',  'Conducteur arrivé'
        PAYMENT_SUCCESS = 'payment_success', 'Paiement réussi'
        PAYMENT_FAILED  = 'payment_failed',  'Paiement échoué'
        ACCOUNT_APPROVED= 'account_approved','Compte approuvé'
        ACCOUNT_SUSPENDED='account_suspended','Compte suspendu'
        GENERAL         = 'general',         'Général'

    class Channel(models.TextChoices):
        PUSH = 'push', 'Notification Push'
        SMS  = 'sms',  'SMS'
        INAPP= 'inapp','In-App'

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')

    notification_type = models.CharField(max_length=20, choices=NotificationType.choices)
    channel         = models.CharField(max_length=5, choices=Channel.choices, default=Channel.INAPP)
    title           = models.CharField(max_length=255)
    body            = models.TextField()
    data            = models.JSONField(null=True, blank=True)   # données additionnelles (ride_id, etc.)

    is_read         = models.BooleanField(default=False)
    is_sent         = models.BooleanField(default=False)
    sent_at         = models.DateTimeField(null=True, blank=True)
    read_at         = models.DateTimeField(null=True, blank=True)

    # ─── Push mobile ──────────────────────────────────────
    device_token    = models.CharField(max_length=500, blank=True, null=True)

    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notifications'
        indexes = [
            models.Index(fields=['recipient', 'is_read']),
            models.Index(fields=['recipient', 'created_at']),
            models.Index(fields=['is_sent', 'created_at']),
        ]
        ordering = ['-created_at']
        verbose_name = 'Notification'

    def __str__(self):
        return f"Notif [{self.notification_type}] → {self.recipient.full_name}"