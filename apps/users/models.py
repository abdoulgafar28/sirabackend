# Create your models here.
"""import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

from django.contrib.auth.models import AbstractUser
from django.db import models


class UserManager(BaseUserManager):

    def create_user(self, phone_number, password=None, **extra_fields):
        if not phone_number:
            raise ValueError("Le numéro de téléphone est obligatoire")
        user = self.model(phone_number=phone_number, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        # Générer client_code automatiquement
        user.client_code = f"CLT-{str(user.pk)[:6].upper()}"
        user.save(update_fields=['client_code'])
        return user

    def create_superuser(self, phone_number, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_verified', True)
        extra_fields.setdefault('role', User.Role.ADMIN)
        return self.create_user(phone_number, password, **extra_fields)

    def create_admin_by_email(self, email, password, **extra_fields):
        #Créer un admin avec email comme identifiant principal.
        if not email:
            raise ValueError("L'email est obligatoire pour un admin")
        extra_fields.setdefault('role', User.Role.ADMIN)
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_verified', True)
        extra_fields.setdefault('status', User.Status.ACTIVE)
        # Générer un phone_number fictif unique pour l'admin
        import uuid
        fake_phone = f"ADMIN-{str(uuid.uuid4())[:8]}"
        user = self.model(
            phone_number=fake_phone,
            email=email,
            **extra_fields
        )
        user.set_password(password)
        user.save(using=self._db)
        return user


class User(AbstractBaseUser, PermissionsMixin):

    class Role(models.TextChoices):
        CLIENT   = 'client',     'Client'
        DRIVER   = 'driver',     'Conducteur'
        ADMIN    = 'admin',      'Administrateur'

    class Status(models.TextChoices):
        ACTIVE    = 'active',    'Actif'
        SUSPENDED = 'suspended', 'Suspendu'
        BANNED    = 'banned',    'Banni'
        PENDING   = 'pending',   'En attente'


    email       = models.EmailField(
        blank=True, null=True,
        unique=True,
        db_index=True
    )
    client_code = models.CharField(
        max_length=20,
        unique=True,
        blank=True,
        null=True
    )

    # ─── Identifiant ──────────────────────────────────────
    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # ─── Informations de base ──────────────────────────────
    phone_number   = models.CharField(max_length=20, unique=True, db_index=True)
    first_name     = models.CharField(max_length=100)
    last_name      = models.CharField(max_length=100)
    email          = models.EmailField(blank=True, null=True)
    photo          = models.ImageField(upload_to='users/photos/', blank=True, null=True)

    # ─── Rôle et statut ───────────────────────────────────
    role           = models.CharField(max_length=10, choices=Role.choices, default=Role.CLIENT)
    status         = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    # ─── Vérification ─────────────────────────────────────
    is_verified    = models.BooleanField(default=False)   # téléphone vérifié
    is_active      = models.BooleanField(default=True)
    is_staff       = models.BooleanField(default=False)

    # ─── Mobile Money ─────────────────────────────────────
    mobile_money_number   = models.CharField(max_length=20, blank=True, null=True)
    mobile_money_operator = models.CharField(
        max_length=20,
        choices=[('orange', 'Orange Money'), ('moov', 'Moov Money')],
        blank=True, null=True
    )

    # ─── Localisation dernière position ───────────────────
    last_latitude  = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_seen_at   = models.DateTimeField(null=True, blank=True)

    # ─── Raison suspension/bannissement ───────────────────
    suspension_reason = models.TextField(blank=True, null=True)

    # ─── Timestamps ───────────────────────────────────────
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD  = 'phone_number'
    REQUIRED_FIELDS = ['first_name', 'last_name']

    company     = models.ForeignKey(
        'admin_panel.Company',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='users'
    )
    client_code = models.CharField(
        max_length=20, unique=True,
        blank=True, null=True
    )  # Ex: CLT-001

    class Meta:
        db_table = 'users'
        indexes = [
            models.Index(fields=['phone_number']),
            models.Index(fields=['role', 'status']),
        ]
        verbose_name = 'Utilisateur'
        verbose_name_plural = 'Utilisateurs'

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.phone_number})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def is_driver(self):
        return self.role == self.Role.DRIVER

    @property
    def is_client(self):
        return self.role == self.Role.CLIENT


class OTPVerification(models.Model):
    
    #Codes OTP pour vérification du numéro de téléphone
    #Utilisé à l'inscription et à la connexion
    
    class Purpose(models.TextChoices):
        REGISTRATION = 'registration', 'Inscription'
        LOGIN        = 'login',        'Connexion'
        RESET        = 'reset',        'Réinitialisation'

    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user           = models.ForeignKey(User, on_delete=models.CASCADE, related_name='otp_codes')
    code           = models.CharField(max_length=6)
    purpose        = models.CharField(max_length=15, choices=Purpose.choices)
    is_used        = models.BooleanField(default=False)
    expires_at     = models.DateTimeField()
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'otp_verifications'
        indexes = [models.Index(fields=['user', 'purpose', 'is_used'])]

    def __str__(self):
        return f"OTP {self.purpose} - {self.user.phone_number}"""






import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

class UserManager(BaseUserManager):

    def create_user(self, phone_number, password=None, **extra_fields):
        if not phone_number:
            raise ValueError("Le numéro de téléphone est obligatoire")
        user = self.model(phone_number=phone_number, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone_number, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_verified', True)
        extra_fields.setdefault('role', User.Role.ADMIN)
        return self.create_user(phone_number, password, **extra_fields)

    def create_admin_by_email(self, email, password, company_name=None, **extra_fields):
        """Créer un admin avec email comme identifiant."""
        if not email:
            raise ValueError("L'email est obligatoire pour un admin")
        extra_fields.setdefault('role', User.Role.ADMIN)
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_verified', True)
        extra_fields.setdefault('status', User.Status.ACTIVE)
        
        fake_phone = f"ADMIN-{str(uuid.uuid4())[:8]}"
        user = self.model(
            phone_number=fake_phone,
            email=email,
            **extra_fields
        )
        user.set_password(password)
        user.save(using=self._db)
        return user


class User(AbstractBaseUser, PermissionsMixin):

    class Role(models.TextChoices):
        CLIENT = 'client', 'Client'
        DRIVER = 'driver', 'Conducteur'
        ADMIN  = 'admin',  'Administrateur'

    class Status(models.TextChoices):
        ACTIVE    = 'active',    'Actif'
        SUSPENDED = 'suspended', 'Suspendu'
        BANNED    = 'banned',    'Banni'
        PENDING   = 'pending',   'En attente'

    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone_number   = models.CharField(max_length=20, unique=True, db_index=True)
    first_name     = models.CharField(max_length=100)
    last_name      = models.CharField(max_length=100)
    email          = models.EmailField(blank=True, null=True, unique=True, db_index=True)
    photo          = models.ImageField(upload_to='users/photos/', blank=True, null=True)
    
    role           = models.CharField(max_length=10, choices=Role.choices, default=Role.CLIENT)
    status         = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    
    is_verified    = models.BooleanField(default=False)
    is_active      = models.BooleanField(default=True)
    is_staff       = models.BooleanField(default=False)
    
    mobile_money_number   = models.CharField(max_length=20, blank=True, null=True)
    mobile_money_operator = models.CharField(
        max_length=20,
        choices=[('orange', 'Orange Money'), ('moov', 'Moov Money')],
        blank=True, null=True
    )
    
    last_latitude  = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_seen_at   = models.DateTimeField(null=True, blank=True)
    
    suspension_reason = models.TextField(blank=True, null=True)
    client_code = models.CharField(max_length=20, unique=True, blank=True, null=True)
    
    company = models.ForeignKey(
        'admin_panel.Company',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='users'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD  = 'phone_number'
    REQUIRED_FIELDS = ['first_name', 'last_name']

    class Meta:
        db_table = 'users'
        indexes = [
            models.Index(fields=['phone_number']),
            models.Index(fields=['role', 'status']),
        ]
        verbose_name = 'Utilisateur'
        verbose_name_plural = 'Utilisateurs'

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.phone_number})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def is_driver(self):
        return self.role == self.Role.DRIVER

    @property
    def is_client(self):
        return self.role == self.Role.CLIENT


class OTPVerification(models.Model):
    class Purpose(models.TextChoices):
        REGISTRATION = 'registration', 'Inscription'
        LOGIN        = 'login',        'Connexion'
        RESET        = 'reset',        'Réinitialisation'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='otp_codes')
    code       = models.CharField(max_length=6)
    purpose    = models.CharField(max_length=15, choices=Purpose.choices)
    is_used    = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'otp_verifications'
        indexes = [models.Index(fields=['user', 'purpose', 'is_used'])]

    def __str__(self):
        return f"OTP {self.purpose} - {self.user.phone_number}"
