import re
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers
from apps.users.models import OTPVerification

User = get_user_model()


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def validate_phone_number(value):
    """Valide un numéro de téléphone burkinabè."""
    pattern = r'^(\+226|00226)?[0-9]{8}$'
    if not re.match(pattern, value.replace(' ', '')):
        raise serializers.ValidationError(
            "Numéro invalide. Format attendu : 7X XXX XXX ou +226 7X XXX XXX"
        )
    return value


# ─────────────────────────────────────────────────────────────
# INSCRIPTION
# ─────────────────────────────────────────────────────────────

class UserRegistrationSerializer(serializers.ModelSerializer):
    password         = serializers.CharField(write_only=True, min_length=6)
    confirm_password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = [
            'email',  # ✅ AJOUTER ICI
            'phone_number', 'first_name', 'last_name',
            'password', 'confirm_password', 'role',
            #'mobile_money_number', 'mobile_money_operator',
        ]


    def validate_phone_number(self, value):
        return validate_phone_number(value)

    def validate_role(self, value):
        """Empêche l'auto-attribution du rôle admin."""
        if value == User.Role.ADMIN:
            raise serializers.ValidationError("Ce rôle n'est pas autorisé à l'inscription.")
        return value

    def validate(self, attrs):
        if attrs['password'] != attrs.pop('confirm_password'):
            raise serializers.ValidationError({'confirm_password': 'Les mots de passe ne correspondent pas.'})
        return attrs

    def create(self, validated_data):
        user = User.objects.create_user(**validated_data)
        return user
    


# ─────────────────────────────────────────────────────────────
# CONNEXION / OTP
# ─────────────────────────────────────────────────────────────

class OTPRequestSerializer(serializers.Serializer):
    phone_number = serializers.CharField()
    purpose      = serializers.ChoiceField(choices=OTPVerification.Purpose.choices)

    def validate_phone_number(self, value):
        return validate_phone_number(value)


class OTPVerifySerializer(serializers.Serializer):
    identifier = serializers.CharField()
    code       = serializers.CharField(min_length=6, max_length=6)
    purpose    = serializers.ChoiceField(choices=OTPVerification.Purpose.choices)
    password   = serializers.CharField(write_only=True, required=False)

    def validate(self, attrs):
        identifier = attrs.get('identifier')
        purpose    = attrs.get('purpose')

        # Chercher par email OU téléphone
        user = User.objects.filter(
            Q(email=identifier) | Q(phone_number=identifier)
        ).first()

        if not user:
            raise serializers.ValidationError({'identifier': 'Utilisateur introuvable.'})

        # ✅ Mot de passe obligatoire pour la connexion
        if purpose == OTPVerification.Purpose.LOGIN:
            password = attrs.get('password')
            if not password:
                raise serializers.ValidationError({'password': 'Le mot de passe est requis.'})
            if not user.check_password(password):
                raise serializers.ValidationError({'password': 'Mot de passe incorrect.'})

        otp = OTPVerification.objects.filter(
            user=user,
            code=attrs['code'],
            purpose=attrs['purpose'],
            is_used=False,
            expires_at__gt=timezone.now()
        ).last()

        if not otp:
            raise serializers.ValidationError({'code': 'Code invalide ou expiré.'})

        attrs['user'] = user
        attrs['otp']  = otp
        return attrs


# ─────────────────────────────────────────────────────────────
# PROFIL UTILISATEUR
# ─────────────────────────────────────────────────────────────

class UserPublicSerializer(serializers.ModelSerializer):
    """Données publiques minimales — affichées aux conducteurs/clients."""
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model  = User
        fields = ['id', 'full_name', 'photo', 'average_rating']

    # average_rating vient du DriverProfile — géré dans to_representation
    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.is_driver and hasattr(instance, 'driver_profile'):
            data['average_rating'] = str(instance.driver_profile.average_rating)
        else:
            data.pop('average_rating', None)
        return data


class UserProfileSerializer(serializers.ModelSerializer):
    """Profil complet — utilisé par le propriétaire du compte."""
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model  = User
        fields = [
            'id', 'phone_number', 'first_name', 'last_name',
            'full_name', 'email', 'photo', 'role', 'status',
            'is_verified', 'mobile_money_number',
            'mobile_money_operator', 'created_at',
        ]
        read_only_fields = [
            'id', 'phone_number', 'role', 'status',
            'is_verified', 'created_at',
        ]


class UserUpdateSerializer(serializers.ModelSerializer):
    """Mise à jour partielle du profil."""
    class Meta:
        model  = User
        fields = [
            'first_name', 'last_name', 'email', 'photo',
            'mobile_money_number', 'mobile_money_operator',
        ]

    def validate_photo(self, value):
        max_size = 5 * 1024 * 1024  # 5 MB
        if value.size > max_size:
            raise serializers.ValidationError("La photo ne doit pas dépasser 5 MB.")
        return value


class UserAdminSerializer(serializers.ModelSerializer):
    """Vue admin complète avec champs sensibles."""
    class Meta:
        model  = User
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class ChangePasswordSerializer(serializers.Serializer):
    old_password     = serializers.CharField(write_only=True)
    new_password     = serializers.CharField(write_only=True, min_length=6)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError({'confirm_password': 'Les mots de passe ne correspondent pas.'})
        return attrs

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Mot de passe actuel incorrect.")
        return value