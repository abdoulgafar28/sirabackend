

# Create your models here.

import uuid
from django.db import models
from apps.drivers.models import DriverProfile


class Vehicle(models.Model):
    """
    Moto du conducteur. Un conducteur = une moto active.
    """
    class VehicleStatus(models.TextChoices):
        ACTIVE   = 'active',   'Active'
        INACTIVE = 'inactive', 'Inactive'

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    driver          = models.OneToOneField(DriverProfile, on_delete=models.CASCADE, related_name='vehicle')

    # ─── Informations véhicule ────────────────────────────
    brand           = models.CharField(max_length=50)         # Marque (Honda, Yamaha...)
    model           = models.CharField(max_length=50)         # Modèle
    color           = models.CharField(max_length=30)
    plate_number    = models.CharField(max_length=20, unique=True)
    year            = models.PositiveSmallIntegerField()

    # ─── Statut ───────────────────────────────────────────
    status          = models.CharField(max_length=20, choices=VehicleStatus.choices, default=VehicleStatus.ACTIVE)

    # ─── Photos ───────────────────────────────────────────
    photo_front     = models.ImageField(upload_to='vehicles/photos/', blank=True, null=True)
    photo_side      = models.ImageField(upload_to='vehicles/photos/', blank=True, null=True)

    # ─── Timestamps ───────────────────────────────────────
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'vehicles'
        indexes = [models.Index(fields=['plate_number'])]
        verbose_name = 'Véhicule'

    def __str__(self):
        return f"{self.brand} {self.model} ({self.plate_number})"