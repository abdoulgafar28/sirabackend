from rest_framework import serializers
from apps.tracking.models import GPSPoint, OfflineSyncQueue


class GPSPointSerializer(serializers.Serializer):
    """
    Simple Serializer (pas ModelSerializer) — pas de champ 'ride'.
    Le ride est géré au niveau du BulkSerializer.
    """
    latitude    = serializers.DecimalField(max_digits=9, decimal_places=6)
    longitude   = serializers.DecimalField(max_digits=9, decimal_places=6)
    altitude    = serializers.DecimalField(max_digits=7, decimal_places=2, required=False, allow_null=True)
    speed_kmh   = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    bearing     = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    accuracy    = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    recorded_at = serializers.DateTimeField()

    def validate_speed_kmh(self, value):
        if value and value > 120:
            raise serializers.ValidationError(f"Vitesse anormale : {value} km/h.")
        return value

    def validate_latitude(self, value):
        if not (-90 <= float(value) <= 90):
            raise serializers.ValidationError("Latitude invalide.")
        return value

    def validate_longitude(self, value):
        if not (-180 <= float(value) <= 180):
            raise serializers.ValidationError("Longitude invalide.")
        return value


class GPSPointBulkSerializer(serializers.Serializer):
    ride_id = serializers.UUIDField()
    points  = GPSPointSerializer(many=True)

    def validate_points(self, value):
        if len(value) == 0:
            raise serializers.ValidationError("La liste de points GPS est vide.")
        if len(value) > 5000:
            raise serializers.ValidationError("Maximum 5000 points par synchronisation.")
        return value


class OfflineSyncQueueSerializer(serializers.ModelSerializer):
    class Meta:
        model  = OfflineSyncQueue
        fields = [
            'id', 'ride', 'data_type', 'payload',
            'sync_status', 'retry_count', 'error_message',
            'recorded_at', 'synced_at',
        ]
        read_only_fields = ['id', 'sync_status', 'retry_count', 'error_message', 'synced_at']


class DriverLocationUpdateSerializer(serializers.Serializer):
    latitude  = serializers.DecimalField(max_digits=9, decimal_places=6)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    speed_kmh = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    bearing   = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    accuracy  = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)

    def validate_latitude(self, value):
        if not (-90 <= float(value) <= 90):
            raise serializers.ValidationError("Latitude invalide.")
        return value

    def validate_longitude(self, value):
        if not (-180 <= float(value) <= 180):
            raise serializers.ValidationError("Longitude invalide.")
        return value