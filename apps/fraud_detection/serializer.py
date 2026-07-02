from rest_framework import serializers
from apps.fraud_detection.models import FraudCheck


class FraudCheckListSerializer(serializers.ModelSerializer):
    """Vue liste — tableau de bord admin."""
    driver_name   = serializers.CharField(source='driver.user.full_name',    read_only=True)
    driver_phone  = serializers.CharField(source='driver.user.phone_number', read_only=True)
    client_name   = serializers.CharField(source='ride.client.full_name',    read_only=True)
    client_phone  = serializers.CharField(source='ride.client.phone_number', read_only=True)
    ride_id       = serializers.UUIDField(source='ride.id',                  read_only=True)
    statut_display       = serializers.CharField(source='get_statut_display',       read_only=True)
    check_status_display = serializers.CharField(source='get_check_status_display', read_only=True)

    class Meta:
        model  = FraudCheck
        fields = [
            # Course
            'id', 'ride_id',
            # Acteurs
            'driver_name', 'driver_phone',
            'client_name', 'client_phone',
            # Distances
            'gps_distance_km', 'theoretical_distance_km',
            'distance_deviation_percent',
            # Vitesse
            'max_speed_kmh', 'average_speed_kmh',
            'speed_limit_zone', 'speed_violations_count',
            # Détour
            'detour_km', 'detour_justified',
            # Résultat
            'statut', 'statut_display',
            'check_status', 'check_status_display',
            'fraud_score', 'incidents',
            'created_at',
        ]


class FraudCheckDetailSerializer(serializers.ModelSerializer):
    """Vue détail complète."""
    driver_name   = serializers.CharField(source='driver.user.full_name',    read_only=True)
    driver_phone  = serializers.CharField(source='driver.user.phone_number', read_only=True)
    client_name   = serializers.CharField(source='ride.client.full_name',    read_only=True)
    client_phone  = serializers.CharField(source='ride.client.phone_number', read_only=True)
    ride_id       = serializers.UUIDField(source='ride.id',                  read_only=True)

    class Meta:
        model  = FraudCheck
        fields = '__all__'


class FraudCheckResolveSerializer(serializers.ModelSerializer):
    """Admin résout un contrôle anti-fraude."""
    class Meta:
        model  = FraudCheck
        fields = ['check_status', 'notes']

    def validate_check_status(self, value):
        allowed = [
            FraudCheck.CheckStatus.CLEARED,
            FraudCheck.CheckStatus.CONFIRMED,
        ]
        if value not in allowed:
            raise serializers.ValidationError(
                f"Statut invalide. Choisir parmi : {allowed}"
            )
        return value