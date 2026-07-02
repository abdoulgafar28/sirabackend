

# Create your models here.


import uuid
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from apps.users.models import User
from apps.rides.models import Ride
from apps.drivers.models import DriverProfile


class Review(models.Model):
    """
    Évaluation laissée par un client après une course.
    Un seul avis par course.
    """
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ride        = models.OneToOneField(Ride, on_delete=models.CASCADE, related_name='review')
    client      = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews_given')
    driver      = models.ForeignKey(DriverProfile, on_delete=models.CASCADE, related_name='reviews_received')

    # ─── Note de 1 à 5 ────────────────────────────────────
    rating      = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comment     = models.TextField(blank=True, null=True)

    # ─── Signalement ──────────────────────────────────────
    is_flagged  = models.BooleanField(default=False)   # signalé par admin
    flag_reason = models.CharField(max_length=255, blank=True, null=True)

    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'reviews'
        indexes = [
            models.Index(fields=['driver', 'rating']),
            models.Index(fields=['client']),
        ]
        verbose_name = 'Évaluation'

    def __str__(self):
        return f"Note {self.rating}/5 — {self.client.full_name} → {self.driver.user.full_name}"