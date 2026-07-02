from decimal import Decimal
from rest_framework import serializers
from apps.rides.models import Ride, RideRequest
from apps.drivers.serializer import DriverProfileListSerializer
from apps.users.serializer import UserPublicSerializer
from apps.core.utils import calculate_haversine_distance, estimate_fare


class RideRequestCreateSerializer(serializers.ModelSerializer):
    """Client crée une demande de course."""
    class Meta:
        model  = RideRequest
        fields = [
            'service_type',
            'pickup_latitude', 'pickup_longitude', 'pickup_address',
            'destination_latitude', 'destination_longitude', 'destination_address',
            'recipient_name', 'recipient_phone', 'package_description',
        ]

    def validate(self, attrs):
        # Vérification que départ ≠ destination
        if (attrs['pickup_latitude'] == attrs['destination_latitude'] and
                attrs['pickup_longitude'] == attrs['destination_longitude']):
            raise serializers.ValidationError(
                "Le point de départ et la destination ne peuvent pas être identiques."
            )

        # Champs obligatoires pour livraison
        if attrs.get('service_type') == RideRequest.ServiceType.DELIVERY:
            if not attrs.get('recipient_name') or not attrs.get('recipient_phone'):
                raise serializers.ValidationError(
                    "Nom et téléphone du destinataire obligatoires pour une livraison."
                )
        return attrs

    def create(self, validated_data):
        from django.utils import timezone
        from datetime import timedelta
        from apps.payments.models import PricingSetting

        client = self.context['request'].user

        # Calcul distance et estimation tarif
        distance = calculate_haversine_distance(
            float(validated_data['pickup_latitude']),
            float(validated_data['pickup_longitude']),
            float(validated_data['destination_latitude']),
            float(validated_data['destination_longitude']),
        )

        pricing = PricingSetting.objects.filter(is_active=True).first()
        estimated_price = estimate_fare(distance, validated_data['service_type'], pricing)

        return RideRequest.objects.create(
            client=client,
            estimated_distance_km=round(distance, 2),
            estimated_price=estimated_price,
            expires_at=timezone.now() + timedelta(minutes=5),
            **validated_data
        )


class RideRequestListSerializer(serializers.ModelSerializer):
    """Liste des demandes de course — vue conducteur."""
    client_name          = serializers.CharField(source='client.full_name', read_only=True)
    service_type_display = serializers.CharField(source='get_service_type_display', read_only=True)
    status_display       = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model  = RideRequest
        fields = [
            'id', 'client_name', 'service_type', 'service_type_display',
            'pickup_latitude', 'pickup_longitude', 'pickup_address',
            'destination_latitude', 'destination_longitude', 'destination_address',
            'estimated_distance_km', 'estimated_price',
            'status', 'status_display', 'expires_at', 'created_at',
        ]


class RideRequestDetailSerializer(serializers.ModelSerializer):
    """Détail complet d'une demande."""
    client               = UserPublicSerializer(read_only=True)
    service_type_display = serializers.CharField(source='get_service_type_display', read_only=True)
    status_display       = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model  = RideRequest
        fields = '__all__'


class RideListSerializer(serializers.ModelSerializer):
    """Liste des courses — historique."""
    client_name    = serializers.CharField(source='client.full_name', read_only=True)
    driver_name    = serializers.CharField(source='driver.user.full_name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model  = Ride
        fields = [
            'id', 'client_name', 'driver_name',
            'status', 'status_display',
            'actual_distance_km', 'total_fare',
            'payment_method', 'is_paid',
            'started_at', 'completed_at', 'created_at',
        ]


class RideDetailSerializer(serializers.ModelSerializer):
    """Détail complet d'une course."""
    client         = UserPublicSerializer(read_only=True)
    driver         = DriverProfileListSerializer(read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    payment_method_display = serializers.CharField(
        source='get_payment_method_display', read_only=True
    )
    duration_minutes = serializers.SerializerMethodField()

    class Meta:
        model  = Ride
        fields = [
            'id', 'client', 'driver', 'status', 'status_display',
            'actual_pickup_latitude', 'actual_pickup_longitude',
            'actual_dropoff_latitude', 'actual_dropoff_longitude',
            'actual_distance_km', 'actual_duration_min',
            'base_fare', 'distance_fare', 'total_fare',
            'driver_earning', 'platform_commission',
            'payment_method', 'payment_method_display', 'is_paid',
            'was_offline', 'synced_at',
            'started_at', 'completed_at', 'created_at',
            'duration_minutes',
        ]

    def get_duration_minutes(self, obj):
        if obj.started_at and obj.completed_at:
            delta = obj.completed_at - obj.started_at
            return int(delta.total_seconds() / 60)
        return None


class RideStatusUpdateSerializer(serializers.ModelSerializer):
    """Conducteur met à jour le statut d'une course."""
    class Meta:
        model  = Ride
        fields = ['status']

    ALLOWED_TRANSITIONS = {
        Ride.Status.ACCEPTED:        [Ride.Status.DRIVER_EN_ROUTE, Ride.Status.CANCELLED],
        Ride.Status.DRIVER_EN_ROUTE: [Ride.Status.STARTED, Ride.Status.CANCELLED],
        Ride.Status.STARTED:         [Ride.Status.COMPLETED],
        Ride.Status.COMPLETED:       [],
        Ride.Status.CANCELLED:       [],
    }

    def validate_status(self, value):
        current = self.instance.status
        allowed = self.ALLOWED_TRANSITIONS.get(current, [])
        if value not in allowed:
            raise serializers.ValidationError(
                f"Transition impossible : {current} → {value}. "
                f"Transitions autorisées : {allowed}"
            )
        return value


class FareEstimateSerializer(serializers.Serializer):
    """Estimation de tarif avant réservation."""
    pickup_latitude       = serializers.DecimalField(max_digits=9, decimal_places=6)
    pickup_longitude      = serializers.DecimalField(max_digits=9, decimal_places=6)
    destination_latitude  = serializers.DecimalField(max_digits=9, decimal_places=6)
    destination_longitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    service_type          = serializers.ChoiceField(choices=RideRequest.ServiceType.choices)