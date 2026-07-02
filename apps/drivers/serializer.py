from rest_framework import serializers
from apps.drivers.models import DriverProfile, DriverDocument
from apps.users.serializer import UserProfileSerializer
from apps.vehicles.serializer import VehicleSerializer


class DriverDocumentSerializer(serializers.ModelSerializer):
    document_type_display       = serializers.CharField(source='get_document_type_display', read_only=True)
    verification_status_display = serializers.CharField(source='get_verification_status_display', read_only=True)

    class Meta:
        model  = DriverDocument
        fields = [
            'id', 'document_type', 'document_type_display',
            'file', 'verification_status', 'verification_status_display',
            'rejection_reason', 'expires_at', 'verified_at', 'created_at',
        ]
        read_only_fields = [
            'id', 'verification_status', 'rejection_reason',
            'verified_at', 'created_at',
        ]

    def validate_file(self, value):
        max_size = 10 * 1024 * 1024  # 10 MB
        allowed  = ['image/jpeg', 'image/png', 'application/pdf']
        if value.size > max_size:
            raise serializers.ValidationError("Fichier trop volumineux (max 10 MB).")
        if value.content_type not in allowed:
            raise serializers.ValidationError("Format accepté : JPG, PNG, PDF.")
        return value


class DriverDocumentUploadSerializer(serializers.ModelSerializer):
    """Utilisé pour l'upload d'un document spécifique."""
    class Meta:
        model  = DriverDocument
        fields = ['document_type', 'file', 'expires_at']

    def validate_document_type(self, value):
        driver = self.context['driver']
        if DriverDocument.objects.filter(driver=driver, document_type=value).exists():
            raise serializers.ValidationError(
                f"Un document de type '{value}' existe déjà. Utilisez la mise à jour."
            )
        return value


class DriverProfileListSerializer(serializers.ModelSerializer):
    """Serializer léger pour les listes (recherche conducteurs proches)."""
    full_name        = serializers.CharField(source='user.full_name', read_only=True)
    photo            = serializers.ImageField(source='user.photo', read_only=True)
    phone_number     = serializers.CharField(source='user.phone_number', read_only=True)
    vehicle_info     = serializers.SerializerMethodField()

    class Meta:
        model  = DriverProfile
        fields = [
            'id', 'full_name', 'photo', 'phone_number',
            'average_rating', 'total_rides',
            'current_latitude', 'current_longitude',
            'is_available', 'vehicle_info',
        ]

    def get_vehicle_info(self, obj):
        if hasattr(obj, 'vehicle'):
            v = obj.vehicle
            return {
                'brand': v.brand,
                'model': v.model,
                'color': v.color,
                'plate_number': v.plate_number,
            }
        return None


class DriverProfileDetailSerializer(serializers.ModelSerializer):
    """Serializer complet pour la page profil conducteur."""
    user             = UserProfileSerializer(read_only=True)
    documents        = DriverDocumentSerializer(many=True, read_only=True)
    vehicle          = VehicleSerializer(read_only=True)
    validation_status_display = serializers.CharField(
        source='get_validation_status_display', read_only=True
    )

    class Meta:
        model  = DriverProfile
        fields = [
            'id', 'user', 'validation_status', 'validation_status_display',
            'rejection_reason', 'validated_at',
            'is_available', 'is_on_ride',
            'current_latitude', 'current_longitude', 'location_updated_at',
            'total_rides', 'total_earnings', 'average_rating', 'total_reviews',
            'activity_zone', 'documents', 'vehicle',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'validation_status', 'rejection_reason', 'validated_at',
            'total_rides', 'total_earnings', 'average_rating', 'total_reviews',
            'created_at', 'updated_at',
        ]


class DriverAvailabilitySerializer(serializers.ModelSerializer):
    """Mise à jour disponibilité + position GPS."""
    class Meta:
        model  = DriverProfile
        fields = ['is_available', 'current_latitude', 'current_longitude']

    def validate(self, attrs):
        # Si le conducteur se met disponible, la position GPS est obligatoire
        if attrs.get('is_available'):
            if not attrs.get('current_latitude') or not attrs.get('current_longitude'):
                raise serializers.ValidationError(
                    "La position GPS est obligatoire pour activer la disponibilité."
                )
        return attrs


class DriverValidationSerializer(serializers.ModelSerializer):
    """Réservé à l'admin — valider ou rejeter un conducteur."""
    class Meta:
        model  = DriverProfile
        fields = ['validation_status', 'rejection_reason']

    def validate(self, attrs):
        if attrs.get('validation_status') == DriverProfile.ValidationStatus.REJECTED:
            if not attrs.get('rejection_reason'):
                raise serializers.ValidationError(
                    {'rejection_reason': 'La raison du rejet est obligatoire.'}
                )
        return attrs


class DriverEarningsSummarySerializer(serializers.Serializer):
    """Résumé des gains — pas lié à un modèle directement."""
    period          = serializers.CharField()       # 'today', 'week', 'month'
    total_rides     = serializers.IntegerField()
    gross_earnings  = serializers.DecimalField(max_digits=12, decimal_places=2)
    commission      = serializers.DecimalField(max_digits=12, decimal_places=2)
    net_earnings    = serializers.DecimalField(max_digits=12, decimal_places=2)
    currency        = serializers.CharField(default='XOF')