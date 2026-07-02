# Create your models here.
import uuid
from django.db import models
from apps.users.models import User


class DriverProfile(models.Model):
    """
    Profil étendu d'un conducteur.
    Créé automatiquement quand un user s'inscrit comme conducteur.
    """
    class ValidationStatus(models.TextChoices):
        PENDING  = 'pending',  'En attente'
        APPROVED = 'approved', 'Approuvé'
        REJECTED = 'rejected', 'Rejeté'

    id                  = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user                = models.OneToOneField(User, on_delete=models.CASCADE, related_name='driver_profile')

    # ─── Statut de validation admin ───────────────────────
    validation_status   = models.CharField(max_length=10, choices=ValidationStatus.choices, default=ValidationStatus.PENDING)
    validated_by        = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='validated_drivers')
    validated_at        = models.DateTimeField(null=True, blank=True)
    rejection_reason    = models.TextField(blank=True, null=True)

    # ─── Disponibilité en temps réel ──────────────────────
    is_available        = models.BooleanField(default=False)
    is_on_ride          = models.BooleanField(default=False)

    # ─── Position GPS actuelle du conducteur ──────────────
    current_latitude    = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    current_longitude   = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    location_updated_at = models.DateTimeField(null=True, blank=True)

    # ─── Statistiques ─────────────────────────────────────
    total_rides         = models.PositiveIntegerField(default=0)
    total_earnings      = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    average_rating      = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    total_reviews       = models.PositiveIntegerField(default=0)

    # ─── Zone d'activité (pour les cartes offline) ────────
    activity_zone       = models.CharField(max_length=100, blank=True, null=True)

    # ─── Timestamps ───────────────────────────────────────
    created_at          = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)

    company = models.ForeignKey(
        'admin_panel.Company',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='drivers'
    )

    class Meta:
        db_table = 'driver_profiles'
        indexes = [
            models.Index(fields=['validation_status']),
            models.Index(fields=['is_available', 'is_on_ride']),
            models.Index(fields=['current_latitude', 'current_longitude']),
        ]
        verbose_name = 'Profil conducteur'

    def __str__(self):
        return f"Driver: {self.user.full_name} — {self.validation_status}"


class DriverDocument(models.Model):
    """
    Documents d'inscription du conducteur.
    Chaque document est stocké séparément pour faciliter la validation admin.
    """
    class DocumentType(models.TextChoices):
        CNI          = 'cni',          "Carte Nationale d'Identité"
        PERMIS       = 'permis',       'Permis de conduire'
        CARTE_GRISE  = 'carte_grise',  'Carte grise'
        PHOTO_DRIVER = 'photo_driver', 'Photo du conducteur'
        PHOTO_MOTO   = 'photo_moto',   'Photo de la moto'

    class VerificationStatus(models.TextChoices):
        PENDING  = 'pending',  'En attente'
        VERIFIED = 'verified', 'Vérifié'
        REJECTED = 'rejected', 'Rejeté'

    id                  = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    driver              = models.ForeignKey(DriverProfile, on_delete=models.CASCADE, related_name='documents')
    document_type       = models.CharField(max_length=20, choices=DocumentType.choices)
    file                = models.FileField(upload_to='drivers/documents/')
    verification_status = models.CharField(max_length=10, choices=VerificationStatus.choices, default=VerificationStatus.PENDING)
    rejection_reason    = models.TextField(blank=True, null=True)
    verified_at         = models.DateTimeField(null=True, blank=True)
    expires_at          = models.DateField(null=True, blank=True)   # expiration permis/CNI
    created_at          = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)


    class DocumentType(models.TextChoices):
        CNI          = 'cni',        "Carte Nationale d'Identité"
        PERMIS       = 'permis',     'Permis de conduire'
        CARTE_GRISE  = 'carte_grise','Carte grise'
        ASSURANCE    = 'assurance',  'Assurance moto'      # ← remplace photo_driver/photo_moto
        PHOTO_DRIVER = 'photo_driver','Photo du conducteur' # ← garder pour compatibilité
        PHOTO_MOTO   = 'photo_moto', 'Photo de la moto'    # ← garder pour compatibilité





    class Meta:
        db_table = 'driver_documents'
        unique_together = ['driver', 'document_type']
        indexes = [models.Index(fields=['driver', 'verification_status'])]
        verbose_name = 'Document conducteur'

    def __str__(self):
        return f"{self.driver.user.full_name} — {self.document_type}"