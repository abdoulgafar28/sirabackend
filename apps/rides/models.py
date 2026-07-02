

# Create your models here.


import uuid
from django.db import models
from apps.users.models import User
from apps.drivers.models import DriverProfile


class RideRequest(models.Model):
    """
    Demande de course émise par un client.
    Statut : pending → accepted/cancelled
    Cycle de vie AVANT le démarrage de la course.
    """
    class ServiceType(models.TextChoices):
        PASSENGER = 'passenger', 'Course passager'
        DELIVERY  = 'delivery',  'Livraison'

    class Status(models.TextChoices):
        PENDING   = 'pending',   'En attente'
        ACCEPTED  = 'accepted',  'Acceptée'
        CANCELLED = 'cancelled', 'Annulée'
        EXPIRED   = 'expired',   'Expirée'

    class CancelledBy(models.TextChoices):
        CLIENT = 'client', 'Client'
        DRIVER = 'driver', 'Conducteur'
        SYSTEM = 'system', 'Système'

    id                    = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client                = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ride_requests')
    driver                = models.ForeignKey(DriverProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name='received_requests')
    

    # ─── Type de service ──────────────────────────────────
    service_type          = models.CharField(max_length=10, choices=ServiceType.choices, default=ServiceType.PASSENGER)

    # ─── Point de départ ──────────────────────────────────
    pickup_latitude       = models.DecimalField(max_digits=9, decimal_places=6)
    pickup_longitude      = models.DecimalField(max_digits=9, decimal_places=6)
    pickup_address        = models.CharField(max_length=255, blank=True, null=True)

    # ─── Destination ──────────────────────────────────────
    destination_latitude  = models.DecimalField(max_digits=9, decimal_places=6)
    destination_longitude = models.DecimalField(max_digits=9, decimal_places=6)
    destination_address   = models.CharField(max_length=255, blank=True, null=True)

    # ─── Estimation ───────────────────────────────────────
    estimated_distance_km = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    estimated_price       = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    estimated_duration_min = models.PositiveSmallIntegerField(null=True, blank=True)

    # ─── Statut ───────────────────────────────────────────
    status                = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    cancelled_by          = models.CharField(max_length=10, choices=CancelledBy.choices, null=True, blank=True)
    cancellation_reason   = models.TextField(blank=True, null=True)

    # ─── Livraison (si service_type = delivery) ───────────
    recipient_name        = models.CharField(max_length=100, blank=True, null=True)
    recipient_phone       = models.CharField(max_length=20, blank=True, null=True)
    package_description   = models.TextField(blank=True, null=True)

    # ─── Timestamps ───────────────────────────────────────
    expires_at            = models.DateTimeField()   # expiration si pas de conducteur
    created_at            = models.DateTimeField(auto_now_add=True)
    updated_at            = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ride_requests'
        indexes = [
            models.Index(fields=['client', 'status']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['pickup_latitude', 'pickup_longitude']),
        ]
        verbose_name = 'Demande de course'

    def __str__(self):
        return f"Demande {self.id} — {self.client.full_name} [{self.status}]"


class Ride(models.Model):
    """
    Course active et terminée.
    Créée quand un conducteur accepte une RideRequest.
    Contient toutes les données financières et GPS finales.
    """
    class Status(models.TextChoices):
        ACCEPTED   = 'accepted',   'Acceptée'
        DRIVER_EN_ROUTE = 'driver_en_route', 'Conducteur en route'
        STARTED    = 'started',    'En cours'
        COMPLETED  = 'completed',  'Terminée'
        CANCELLED  = 'cancelled',  'Annulée'

    class PaymentMethod(models.TextChoices):
        ORANGE_MONEY = 'orange_money', 'Orange Money'
        MOOV_MONEY   = 'moov_money',   'Moov Money'
        CASH         = 'cash',         'Espèces'

    id                    = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request               = models.OneToOneField(RideRequest, on_delete=models.CASCADE, related_name='ride')
    client                = models.ForeignKey(User, on_delete=models.CASCADE, related_name='rides_as_client')
    driver                = models.ForeignKey(DriverProfile, on_delete=models.CASCADE, related_name='rides_as_driver')
    driver_earning = models.OneToOneField('payments.DriverEarning', on_delete=models.CASCADE, related_name='ride_driver_earning')

    # ─── Statut ───────────────────────────────────────────
    status                = models.CharField(max_length=20, choices=Status.choices, default=Status.ACCEPTED)

    # ─── Points GPS départ / arrivée RÉELS ────────────────
    actual_pickup_latitude     = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    actual_pickup_longitude    = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    actual_dropoff_latitude    = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    actual_dropoff_longitude   = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # ─── Distance et durée réelles ────────────────────────
    actual_distance_km         = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    actual_duration_min        = models.PositiveSmallIntegerField(null=True, blank=True)

    # ─── Tarification ─────────────────────────────────────
    base_fare                  = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    distance_fare              = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    total_fare                 = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    driver_earning             = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    platform_commission        = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    # ─── Paiement ─────────────────────────────────────────
    payment_method             = models.CharField(max_length=15, choices=PaymentMethod.choices, null=True, blank=True)
    is_paid                    = models.BooleanField(default=False)

    # ─── Timestamps de cycle de vie ───────────────────────
    driver_arrived_at          = models.DateTimeField(null=True, blank=True)
    started_at                 = models.DateTimeField(null=True, blank=True)
    completed_at               = models.DateTimeField(null=True, blank=True)
    cancelled_at               = models.DateTimeField(null=True, blank=True)
    cancellation_reason        = models.TextField(blank=True, null=True)

    # ─── Mode offline ─────────────────────────────────────
    was_offline                = models.BooleanField(default=False)
    synced_at                  = models.DateTimeField(null=True, blank=True)

    # ─── Timestamps ───────────────────────────────────────
    created_at                 = models.DateTimeField(auto_now_add=True)
    updated_at                 = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rides'
        indexes = [
            models.Index(fields=['client', 'status']),
            models.Index(fields=['driver', 'status']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['is_paid']),
        ]
        verbose_name = 'Course'

    def __str__(self):
        return f"Course {self.id} — {self.client.full_name} → {self.status}"