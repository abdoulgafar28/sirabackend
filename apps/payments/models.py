import uuid
from django.db import models
from django.core.validators import MinValueValidator
from apps.users.models import User
from apps.drivers.models import DriverProfile


# ─── Constantes globales ──────────────────────────────────
COMMISSION_COURSE_PERCENT  = 10   # 10% par course
COMMISSION_RETRAIT_PERCENT = 1    # 1% sur retrait
FRAIS_ANNULATION_PERCENT   = 5    # 5% si annulation non valable
MONTANT_MIN_RETRAIT        = 500  # 500 FCFA minimum
DELAI_PAIEMENT_MINUTES     = 30   # 30 min pour payer après course


# ─────────────────────────────────────────────────────────
# 1. SIRA WALLET
# ─────────────────────────────────────────────────────────
class SiraWallet(models.Model):

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Actif'
        FROZEN = 'frozen', 'Gelé'
        CLOSED = 'closed', 'Fermé'

    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user           = models.OneToOneField(User, on_delete=models.CASCADE, related_name='wallet')
    balance        = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    status         = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    freeze_reason  = models.TextField(blank=True, null=True)
    total_credited = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_debited  = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        db_table     = 'sira_wallets'
        verbose_name = 'Wallet SIRA'

    def __str__(self):
        return f"Wallet {self.user.full_name} — {self.balance} XOF"

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE


# ─────────────────────────────────────────────────────────
# 2. TRANSACTIONS WALLET (mouvements internes)
# ─────────────────────────────────────────────────────────
class WalletTransaction(models.Model):

    class Type(models.TextChoices):
        DEPOT              = 'depot',              'Dépôt Mobile Money'
        RETRAIT            = 'retrait',            'Retrait Mobile Money'
        PAIEMENT_COURSE    = 'paiement_course',    'Paiement course'
        RECEPTION_COURSE   = 'reception_course',   'Réception course'
        COMMISSION         = 'commission',         'Commission SIRA'
        REMBOURSEMENT      = 'remboursement',      'Remboursement'
        FRAIS_ANNULATION   = 'frais_annulation',   'Frais annulation'
        COMMISSION_RETRAIT = 'commission_retrait', 'Commission retrait'

    class Direction(models.TextChoices):
        CREDIT = 'credit', 'Crédit'
        DEBIT  = 'debit',  'Débit'

    class Status(models.TextChoices):
        SUCCESS  = 'success',  'Réussi'
        FAILED   = 'failed',   'Échoué'
        REVERSED = 'reversed', 'Annulé'

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet           = models.ForeignKey(SiraWallet, on_delete=models.CASCADE, related_name='transactions')
    transaction_type = models.CharField(max_length=20, choices=Type.choices)
    direction        = models.CharField(max_length=6, choices=Direction.choices)
    amount           = models.DecimalField(max_digits=10, decimal_places=2)
    balance_before   = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after    = models.DecimalField(max_digits=12, decimal_places=2)
    currency         = models.CharField(max_length=5, default='XOF')
    ride             = models.ForeignKey('rides.Ride', on_delete=models.SET_NULL, null=True, blank=True, related_name='wallet_transactions')
    related_transaction = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)
    status           = models.CharField(max_length=10, choices=Status.choices, default=Status.SUCCESS)
    description      = models.CharField(max_length=255)
    metadata         = models.JSONField(null=True, blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'wallet_transactions'
        indexes  = [
            models.Index(fields=['wallet', 'created_at']),
            models.Index(fields=['transaction_type', 'status']),
            models.Index(fields=['ride']),
        ]
        ordering     = ['-created_at']
        verbose_name = 'Transaction Wallet'

    def __str__(self):
        return f"{self.direction} {self.amount} XOF — {self.transaction_type}"


# ─────────────────────────────────────────────────────────
# 3. LIGDICASH PAYIN (recharge wallet via LigdiCash)
# ─────────────────────────────────────────────────────────
class LigdiCashPayin(models.Model):
    """
    Trace chaque tentative de recharge du Wallet SIRA
    via l'API LigdiCash.
    """
    class Status(models.TextChoices):
        OTP_SENT   = 'otp_sent',   'OTP envoyé'
        PENDING    = 'pending',    'En attente de validation'
        COMPLETED  = 'completed',  'Complété'
        FAILED     = 'failed',     'Échoué'
        EXPIRED    = 'expired',    'Expiré'

    id                = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet            = models.ForeignKey(SiraWallet, on_delete=models.CASCADE, related_name='ligdicash_payins')
    user              = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ligdicash_payins')

    # ─── Infos transaction ────────────────────────────
    phone_number      = models.CharField(max_length=20)     # numéro Mobile Money du client
    amount            = models.DecimalField(max_digits=10, decimal_places=2)
    currency          = models.CharField(max_length=5, default='XOF')

    # ─── LigdiCash ────────────────────────────────────
    invoice_token     = models.CharField(max_length=500, blank=True, null=True)  # token retourné par LigdiCash
    ligdicash_transaction_id = models.CharField(max_length=100, blank=True, null=True)
    operator_name     = models.CharField(max_length=50, blank=True, null=True)   # ORANGE BURKINA / MOOV BURKINA

    # ─── Statut ───────────────────────────────────────
    status            = models.CharField(max_length=15, choices=Status.choices, default=Status.OTP_SENT)
    failure_reason    = models.TextField(blank=True, null=True)

    # ─── Callback ─────────────────────────────────────
    callback_received = models.BooleanField(default=False)
    callback_data     = models.JSONField(null=True, blank=True)

    # ─── Wallet crédité ? ─────────────────────────────
    wallet_credited   = models.BooleanField(default=False)

    # ─── Expiration OTP ───────────────────────────────
    expires_at        = models.DateTimeField()

    created_at        = models.DateTimeField(auto_now_add=True)
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ligdicash_payins'
        indexes  = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['invoice_token']),
            models.Index(fields=['status', 'created_at']),
        ]
        ordering     = ['-created_at']
        verbose_name = 'LigdiCash Payin'

    def __str__(self):
        return f"Payin {self.amount} XOF — {self.phone_number} [{self.status}]"


# ─────────────────────────────────────────────────────────
# 4. LIGDICASH PAYOUT (retrait conducteur via LigdiCash)
# ─────────────────────────────────────────────────────────
class LigdiCashPayout(models.Model):
    """
    Trace chaque retrait du Wallet SIRA
    vers le Mobile Money du conducteur via LigdiCash.
    """
    class Status(models.TextChoices):
        PENDING   = 'pending',   'En attente'
        COMPLETED = 'completed', 'Complété'
        FAILED    = 'failed',    'Échoué'

    id                       = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet                   = models.ForeignKey(SiraWallet, on_delete=models.CASCADE, related_name='ligdicash_payouts')
    driver                   = models.ForeignKey(DriverProfile, on_delete=models.CASCADE, related_name='ligdicash_payouts')

    # ─── Montants ─────────────────────────────────────
    amount_requested         = models.DecimalField(max_digits=10, decimal_places=2)
    commission_amount        = models.DecimalField(max_digits=10, decimal_places=2)  # 1%
    amount_to_receive        = models.DecimalField(max_digits=10, decimal_places=2)  # 99%

    # ─── Destination ──────────────────────────────────
    recipient_phone          = models.CharField(max_length=20)

    # ─── LigdiCash ────────────────────────────────────
    withdrawal_token         = models.CharField(max_length=500, blank=True, null=True)
    ligdicash_transaction_id = models.CharField(max_length=100, blank=True, null=True)
    operator_name            = models.CharField(max_length=50, blank=True, null=True)

    # ─── Statut ───────────────────────────────────────
    status                   = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    failure_reason           = models.TextField(blank=True, null=True)

    # ─── Callback ─────────────────────────────────────
    callback_received        = models.BooleanField(default=False)
    callback_data            = models.JSONField(null=True, blank=True)

    created_at               = models.DateTimeField(auto_now_add=True)
    updated_at               = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ligdicash_payouts'
        indexes  = [
            models.Index(fields=['driver', 'status']),
            models.Index(fields=['withdrawal_token']),
        ]
        ordering     = ['-created_at']
        verbose_name = 'LigdiCash Payout'

    def __str__(self):
        return f"Payout {self.amount_requested} XOF → {self.recipient_phone} [{self.status}]"


# ─────────────────────────────────────────────────────────
# 5. PRICING SETTING (tarification)
# ─────────────────────────────────────────────────────────
class PricingSetting(models.Model):

    id                    = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name                  = models.CharField(max_length=100)
    is_active             = models.BooleanField(default=True)
    base_fare             = models.DecimalField(max_digits=8, decimal_places=2)
    price_per_km          = models.DecimalField(max_digits=6, decimal_places=2)
    price_per_minute      = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    delivery_base_fare    = models.DecimalField(max_digits=8, decimal_places=2)
    delivery_price_per_km = models.DecimalField(max_digits=6, decimal_places=2)
    commission_percent    = models.DecimalField(max_digits=4, decimal_places=2, default=10)
    minimum_fare          = models.DecimalField(max_digits=8, decimal_places=2)
    maximum_fare          = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    surge_multiplier      = models.DecimalField(max_digits=3, decimal_places=2, default=1.0)
    created_by            = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at            = models.DateTimeField(auto_now_add=True)
    updated_at            = models.DateTimeField(auto_now=True)

    class Meta:
        db_table     = 'pricing_settings'
        verbose_name = 'Paramètre tarifaire'

    def __str__(self):
        return f"{self.name} — {self.price_per_km} FCFA/km"


# ─────────────────────────────────────────────────────────
# 6. DRIVER EARNING (gains conducteur par course)
# ─────────────────────────────────────────────────────────
class DriverEarning(models.Model):

    id                = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    gross_amount      = models.DecimalField(max_digits=10, decimal_places=2)
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2)
    net_amount        = models.DecimalField(max_digits=10, decimal_places=2)
    is_paid           = models.BooleanField(default=False)
    paid_at           = models.DateTimeField(null=True, blank=True)
    earning_date      = models.DateField()
    created_at        = models.DateTimeField(auto_now_add=True)


    driver = models.ForeignKey("drivers.DriverProfile", on_delete=models.CASCADE)
    ride = models.ForeignKey("rides.Ride", on_delete=models.CASCADE, related_name="earnings")
    
    created_at = models.DateTimeField(auto_now_add=True)


    class Meta:
        db_table = 'driver_earnings'
        indexes  = [
            models.Index(fields=['driver', 'earning_date']),
            models.Index(fields=['driver', 'is_paid']),
        ]
        verbose_name = 'Gain conducteur'

    def __str__(self):
        return f"Gain {self.net_amount} XOF — {self.driver.user.full_name}"




 