# apps/fraud_detection/models.py
import uuid
from django.db import models
from apps.rides.models import Ride
from apps.drivers.models import DriverProfile


class FraudCheck(models.Model):

    class RiskLevel(models.TextChoices):
        LOW      = 'low',      'Faible'
        MEDIUM   = 'medium',   'Moyen'
        HIGH     = 'high',     'Élevé'
        CRITICAL = 'critical', 'Critique'

    class CheckStatus(models.TextChoices):
        PENDING   = 'pending',   'En attente'
        CLEARED   = 'cleared',   'Sans fraude'
        FLAGGED   = 'flagged',   'Signalé'
        CONFIRMED = 'confirmed', 'Fraude confirmée'

    class Statut(models.TextChoices):
        OK             = 'ok',             'OK'
        AVERTISSEMENT  = 'avertissement',  'Avertissement'
        ALERTE         = 'alerte',         'Alerte'

    id                         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # ─── Course et acteurs ────────────────────────────────
    ride                       = models.OneToOneField(Ride, on_delete=models.CASCADE, related_name='fraud_check')
    driver                     = models.ForeignKey(DriverProfile, on_delete=models.CASCADE, related_name='fraud_checks')

    # ─── Distances ────────────────────────────────────────
    gps_distance_km            = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    theoretical_distance_km    = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    distance_deviation_percent = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # ─── Vitesse ──────────────────────────────────────────
    max_speed_kmh              = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    average_speed_kmh          = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    speed_limit_zone           = models.DecimalField(max_digits=5, decimal_places=2, default=50)  # 50 km/h Ouaga
    speed_violations_count     = models.PositiveSmallIntegerField(default=0)

    # ─── Détour ───────────────────────────────────────────
    detour_km                  = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    detour_justified           = models.BooleanField(default=False)

    # ─── Anomalies détectées ──────────────────────────────
    has_gps_gaps               = models.BooleanField(default=False)
    has_route_deviation        = models.BooleanField(default=False)
    has_speed_anomaly          = models.BooleanField(default=False)
    has_distance_mismatch      = models.BooleanField(default=False)

    # ─── Incidents (liste texte) ──────────────────────────
    incidents                  = models.JSONField(default=list)

    # ─── Résultat ─────────────────────────────────────────
    statut                     = models.CharField(max_length=15, choices=Statut.choices, default=Statut.OK)
    risk_level                 = models.CharField(max_length=10, choices=RiskLevel.choices, default=RiskLevel.LOW)
    check_status               = models.CharField(max_length=10, choices=CheckStatus.choices, default=CheckStatus.PENDING)
    fraud_score                = models.PositiveSmallIntegerField(default=0)  # 0-100
    notes                      = models.TextField(blank=True, null=True)

    # ─── Résolution admin ─────────────────────────────────
    reviewed_by                = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_fraud_checks')
    reviewed_at                = models.DateTimeField(null=True, blank=True)

    created_at                 = models.DateTimeField(auto_now_add=True)
    updated_at                 = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'fraud_checks'
        indexes  = [
            models.Index(fields=['statut', 'check_status']),
            models.Index(fields=['driver', 'created_at']),
            models.Index(fields=['fraud_score']),
        ]
        ordering = ['-created_at']
        verbose_name = 'Contrôle anti-fraude'

    def __str__(self):
        return f"Fraude [{self.statut}] Course {self.ride_id} — Score: {self.fraud_score}"