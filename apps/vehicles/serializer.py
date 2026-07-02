from rest_framework import serializers
from apps.vehicles.models import Vehicle
from datetime import date


class VehicleSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model  = Vehicle
        fields = [
            'id', 'brand', 'model', 'color', 'plate_number', 'year',
            'status', 'status_display', 'photo_front', 'photo_side',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'status', 'created_at', 'updated_at']

    def validate_year(self, value):
        current_year = date.today().year
        if value < 1990 or value > current_year:
            raise serializers.ValidationError(
                f"L'année doit être entre 1990 et {current_year}."
            )
        return value

    def validate_plate_number(self, value):
        return value.upper().strip()