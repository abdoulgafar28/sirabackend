# apps/payments/serializer.py
from rest_framework import serializers
from apps.payments.models import (
    SiraWallet,
    WalletTransaction,
    LigdiCashPayin,
    LigdiCashPayout,
    DriverEarning,
    PricingSetting,
    MONTANT_MIN_RETRAIT,
)


class WalletSerializer(serializers.ModelSerializer):
    owner_name   = serializers.CharField(source='user.full_name',    read_only=True)
    owner_phone  = serializers.CharField(source='user.phone_number', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model  = SiraWallet
        fields = [
            'id', 'owner_name', 'owner_phone',
            'balance', 'status', 'status_display',
            'total_credited', 'total_debited',
            'created_at', 'updated_at',
        ]
        read_only_fields = '__all__'


class WalletTransactionSerializer(serializers.ModelSerializer):
    type_display      = serializers.CharField(source='get_transaction_type_display', read_only=True)
    direction_display = serializers.CharField(source='get_direction_display',        read_only=True)

    class Meta:
        model  = WalletTransaction
        fields = [
            'id', 'transaction_type', 'type_display',
            'direction', 'direction_display',
            'amount', 'balance_before', 'balance_after',
            'currency', 'ride', 'status', 'description',
            'created_at',
        ]
        read_only_fields = '__all__'


class LigdiCashPayinSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model  = LigdiCashPayin
        fields = [
            'id', 'phone_number', 'amount', 'currency',
            'operator_name', 'status', 'status_display',
            'wallet_credited', 'expires_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = '__all__'


class LigdiCashPayoutSerializer(serializers.ModelSerializer):
    driver_name    = serializers.CharField(source='driver.user.full_name',    read_only=True)
    status_display = serializers.CharField(source='get_status_display',       read_only=True)

    class Meta:
        model  = LigdiCashPayout
        fields = [
            'id', 'driver_name',
            'amount_requested', 'commission_amount', 'amount_to_receive',
            'recipient_phone', 'operator_name',
            'status', 'status_display',
            'created_at', 'updated_at',
        ]
        read_only_fields = '__all__'


class DriverEarningSerializer(serializers.ModelSerializer):
    class Meta:
        model  = DriverEarning
        fields = [
            'id', 'ride', 'gross_amount',
            'commission_amount', 'net_amount',
            'is_paid', 'paid_at', 'earning_date',
            'created_at',
        ]
        read_only_fields = '__all__'


class DriverEarningsSummarySerializer(serializers.Serializer):
    """Résumé des gains sur une période."""
    period           = serializers.CharField()
    date_from        = serializers.DateField()
    date_to          = serializers.DateField()
    total_rides      = serializers.IntegerField()
    gross_total      = serializers.DecimalField(max_digits=12, decimal_places=2)
    commission_total = serializers.DecimalField(max_digits=12, decimal_places=2)
    net_total        = serializers.DecimalField(max_digits=12, decimal_places=2)
    currency         = serializers.CharField(default='XOF')


class PricingSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model  = PricingSetting
        fields = [
            'id', 'name', 'is_active',
            'base_fare', 'price_per_km', 'price_per_minute',
            'delivery_base_fare', 'delivery_price_per_km',
            'commission_percent', 'minimum_fare', 'maximum_fare',
            'surge_multiplier', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_commission_percent(self, value):
        if not (0 <= value <= 50):
            raise serializers.ValidationError("Commission entre 0% et 50%.")
        return value

    def validate_surge_multiplier(self, value):
        if not (1.0 <= value <= 5.0):
            raise serializers.ValidationError("Multiplicateur entre 1.0 et 5.0.")
        return value


class PayinInitiateSerializer(serializers.Serializer):
    """Initier une recharge Wallet SIRA."""
    phone_number = serializers.CharField(max_length=20)
    amount       = serializers.IntegerField(min_value=100, max_value=500000)

    def validate_phone_number(self, value):
        import re
        if not re.match(r'^(\+226|00226|226)?[0-9]{8}$', value.replace(' ', '')):
            raise serializers.ValidationError("Numéro de téléphone invalide.")
        return value


class PayinValidateSerializer(serializers.Serializer):
    """Valider l'OTP pour confirmer la recharge."""
    payin_id = serializers.UUIDField()
    otp      = serializers.CharField(min_length=4, max_length=10)


class PayoutRequestSerializer(serializers.Serializer):
    """Demande de retrait conducteur."""
    phone_number = serializers.CharField(max_length=20)
    amount       = serializers.DecimalField(max_digits=10, decimal_places=2)

    def validate_amount(self, value):
        if value < MONTANT_MIN_RETRAIT:
            raise serializers.ValidationError(
                f"Montant minimum : {MONTANT_MIN_RETRAIT} FCFA."
            )
        return value

    def validate_phone_number(self, value):
        import re
        if not re.match(r'^(\+226|00226|226)?[0-9]{8}$', value.replace(' ', '')):
            raise serializers.ValidationError("Numéro Mobile Money invalide.")
        return value