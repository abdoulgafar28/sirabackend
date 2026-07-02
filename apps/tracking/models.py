# Create your models here.
import uuid
from django.db import models
from apps.rides.models import Ride
from apps.drivers.models import DriverProfile


class GPSPoint(models.Model):
    """
    Point GPS enregistré tout au long d'une course.
    Collecté toutes les N secondes par le téléphone du conducteur.
    Base du calcul de distance réelle et détection de fraude.
    """
    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ride          = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name='gps_points')
    driver        = models.ForeignKey(DriverProfile, on_delete=models.CASCADE, related_name='gps_points')

    # ─── Coordonnées ──────────────────────────────────────
    latitude      = models.DecimalField(max_digits=9, decimal_places=6)
    longitude     = models.DecimalField(max_digits=9, decimal_places=6)
    altitude      = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)

    # ─── Données de mouvement ─────────────────────────────
    speed_kmh     = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    bearing       = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)  # direction
    accuracy      = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)  # précision GPS en mètres

    # ─── Séquence ─────────────────────────────────────────
    sequence      = models.PositiveIntegerField(default=0)  # ordre du point dans la course
    recorded_at   = models.DateTimeField()                  # timestamp réel de l'enregistrement
    is_offline    = models.BooleanField(default=False)      # enregistré hors ligne ?
    synced_at     = models.DateTimeField(null=True, blank=True)

    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'gps_points'
        ordering = ['sequence']
        
        indexes = [
            models.Index(fields=['ride', 'sequence']),
            models.Index(fields=['driver', 'recorded_at']),
            models.Index(fields=['is_offline', 'synced_at']),
        ]
        verbose_name = 'Point GPS'

    def __str__(self):
        return f"GPS [{self.sequence}] Course {self.ride_id} — ({self.latitude}, {self.longitude})"


class OfflineSyncQueue(models.Model):
    """
    File d'attente de synchronisation pour le mode offline.
    Quand le conducteur n'a pas de connexion, les données sont
    stockées ici et synchronisées dès le retour de la connexion.
    """
    class SyncStatus(models.TextChoices):
        PENDING  = 'pending',  'En attente'
        SYNCED   = 'synced',   'Synchronisé'
        FAILED   = 'failed',   'Échoué'

    class DataType(models.TextChoices):
        GPS_POINTS   = 'gps_points',   'Points GPS'
        RIDE_STATUS  = 'ride_status',  'Statut course'
        PAYMENT      = 'payment',      'Paiement'

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    driver        = models.ForeignKey(DriverProfile, on_delete=models.CASCADE, related_name='sync_queue')
    ride          = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name='sync_queue', null=True, blank=True)

    data_type     = models.CharField(max_length=15, choices=DataType.choices)
    payload       = models.JSONField()         # données brutes à synchroniser
    sync_status   = models.CharField(max_length=10, choices=SyncStatus.choices, default=SyncStatus.PENDING)
    retry_count   = models.PositiveSmallIntegerField(default=0)
    error_message = models.TextField(blank=True, null=True)

    recorded_at   = models.DateTimeField()     # quand c'est arrivé offline
    synced_at     = models.DateTimeField(null=True, blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'offline_sync_queue'
        indexes = [
            models.Index(fields=['driver', 'sync_status']),
            models.Index(fields=['sync_status', 'created_at']),
        ]
        ordering = ['recorded_at']
        verbose_name = 'File de synchronisation offline'

    def __str__(self):
        return f"Sync {self.data_type} — {self.sync_status} — Driver {self.driver_id}"