import uuid
from django.db import models


# ─────────────────────────────────────────────────────────────
# 1. COMPANY
# ─────────────────────────────────────────────────────────────
class Company(models.Model):
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name       = models.CharField(max_length=200)
    email      = models.EmailField(unique=True)
    phone      = models.CharField(max_length=20, blank=True, null=True)
    logo       = models.ImageField(upload_to='companies/logos/', blank=True, null=True)
    address    = models.TextField(blank=True, null=True)
    is_active  = models.BooleanField(default=True)
    is_main    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table     = 'companies'
        verbose_name = 'Entreprise'

    def __str__(self):
        return f"{self.name} {'[PRINCIPALE]' if self.is_main else ''}"


# ─────────────────────────────────────────────────────────────
# 2. DELIVERY PRICING GRID
# ─────────────────────────────────────────────────────────────
class DeliveryPricingGrid(models.Model):
    """
    Grille tarifaire livraison multi-critères.

    Formule :
    Tarif = (
      km_collecte × prix_collecte
      + km_livraison × prix_livraison
      + frais_base + supplément_engin
      + majoration_poids + majoration_valeur
    ) × multiplicateur_nature
    → max(résultat, tarif_minimum)
    """
    id                    = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company               = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='delivery_pricing')
    name                  = models.CharField(max_length=100, default="Grille tarifaire SIRA")
    is_active             = models.BooleanField(default=True)
    price_per_km_pickup   = models.DecimalField(max_digits=8, decimal_places=2, default=150)
    price_per_km_delivery = models.DecimalField(max_digits=8, decimal_places=2, default=200)
    base_fare             = models.DecimalField(max_digits=8, decimal_places=2, default=500)
    min_fare              = models.DecimalField(max_digits=8, decimal_places=2, default=800)
    waiting_time_rate     = models.DecimalField(max_digits=6, decimal_places=2, default=50)
    created_at            = models.DateTimeField(auto_now_add=True)
    updated_at            = models.DateTimeField(auto_now=True)

    class Meta:
        db_table     = 'delivery_pricing_grids'
        verbose_name = 'Grille tarifaire livraison'

    def __str__(self):
        return self.name


class WeightSlab(models.Model):
    id        = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    grid      = models.ForeignKey(DeliveryPricingGrid, on_delete=models.CASCADE, related_name='weight_slabs')
    label     = models.CharField(max_length=50)
    max_kg    = models.DecimalField(max_digits=6, decimal_places=2)
    surcharge = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    order     = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'weight_slabs'
        ordering = ['order']


class ValueSlab(models.Model):
    id        = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    grid      = models.ForeignKey(DeliveryPricingGrid, on_delete=models.CASCADE, related_name='value_slabs')
    label     = models.CharField(max_length=50)
    max_value = models.DecimalField(max_digits=12, decimal_places=2)
    surcharge = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    order     = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'value_slabs'
        ordering = ['order']


class PackageNature(models.Model):
    id                  = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    grid                = models.ForeignKey(DeliveryPricingGrid, on_delete=models.CASCADE, related_name='package_natures')
    nature_id           = models.CharField(max_length=20)
    label               = models.CharField(max_length=50)
    icon                = models.CharField(max_length=10, default="📦")
    multiplier          = models.DecimalField(max_digits=4, decimal_places=2, default=1.0)
    compatible_vehicles = models.JSONField(default=list)
    order               = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'package_natures'
        ordering = ['order']


class VehicleType(models.Model):
    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    grid           = models.ForeignKey(DeliveryPricingGrid, on_delete=models.CASCADE, related_name='vehicle_types')
    vehicle_id     = models.CharField(max_length=20)
    label          = models.CharField(max_length=50)
    icon           = models.CharField(max_length=10, default="🛵")
    max_weight_kg  = models.DecimalField(max_digits=7, decimal_places=2)
    max_value_fcfa = models.DecimalField(max_digits=12, decimal_places=2)
    base_surcharge = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    order          = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'vehicle_types'
        ordering = ['order']


# ─────────────────────────────────────────────────────────────
# 3. DISPUTE
# ─────────────────────────────────────────────────────────────
class Dispute(models.Model):

    class DisputeType(models.TextChoices):
        OVERCHARGE      = 'overcharge',      'Surfacturation'
        WRONG_ROUTE     = 'wrong_route',     'Mauvais itinéraire'
        DRIVER_BEHAVIOR = 'driver_behavior', 'Comportement conducteur'
        PAYMENT_ISSUE   = 'payment_issue',   'Problème de paiement'
        LOST_ITEM       = 'lost_item',       'Objet perdu'
        OTHER           = 'other',           'Autre'

    class Status(models.TextChoices):
        OPEN      = 'open',      'Ouvert'
        IN_REVIEW = 'in_review', "En cours d'examen"
        RESOLVED  = 'resolved',  'Résolu'
        CLOSED    = 'closed',    'Fermé'

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ride             = models.ForeignKey('rides.Ride', on_delete=models.CASCADE, related_name='disputes')
    filed_by         = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='disputes_filed')
    dispute_type     = models.CharField(max_length=20, choices=DisputeType.choices)
    description      = models.TextField()
    evidence_file    = models.FileField(upload_to='disputes/evidence/', blank=True, null=True)
    status           = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    resolution_notes = models.TextField(blank=True, null=True)
    refund_amount    = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    resolved_at      = models.DateTimeField(null=True, blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        db_table     = 'disputes'
        ordering     = ['-created_at']
        verbose_name = 'Litige'


# ─────────────────────────────────────────────────────────────
# 4. SYSTEM LOG
# ─────────────────────────────────────────────────────────────
class SystemLog(models.Model):

    class ActionType(models.TextChoices):
        DRIVER_VALIDATED  = 'driver_validated',  'Conducteur validé'
        DRIVER_REJECTED   = 'driver_rejected',   'Conducteur rejeté'
        USER_SUSPENDED    = 'user_suspended',     'Utilisateur suspendu'
        USER_BANNED       = 'user_banned',        'Utilisateur banni'
        FRAUD_DETECTED    = 'fraud_detected',     'Fraude détectée'
        PRICING_UPDATED   = 'pricing_updated',    'Tarif mis à jour'
        ADMIN_LOGIN       = 'admin_login',        'Connexion admin'

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action      = models.CharField(max_length=25, choices=ActionType.choices)
    performed_by= models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='system_logs')
    target_user = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='logs_about')
    description = models.TextField()
    metadata    = models.JSONField(null=True, blank=True)
    ip_address  = models.GenericIPAddressField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'system_logs'
        ordering = ['-created_at']