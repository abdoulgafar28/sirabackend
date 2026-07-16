from decimal import Decimal
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from apps.admin_panel.models import (
    Company, DeliveryPricingGrid,
    WeightSlab, ValueSlab,
    PackageNature, VehicleType,
    Dispute, SystemLog,
)
from apps.drivers.models import DriverProfile, DriverDocument
from apps.payments.models import PricingSetting, SiraWallet, WalletTransaction
from apps.rides.models import Ride
from apps.users.models import User


# ─────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────

class AdminTokenObtainSerializer(TokenObtainPairSerializer):
    """
    Override SimpleJWT pour permettre la connexion
    des admins par EMAIL au lieu de téléphone.
    """
    username_field = 'email'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email']    = serializers.EmailField()
        self.fields['password'] = serializers.CharField(write_only=True)
        # Supprimer le champ phone_number hérité
        self.fields.pop('phone_number', None)

    def validate(self, attrs):
        email    = attrs.get('email', '').strip().lower()
        password = attrs.get('password', '').strip()

        try:
            user = User.objects.get(email=email, role=User.Role.ADMIN)
        except User.DoesNotExist:
            raise serializers.ValidationError(
                {'detail': 'Email ou mot de passe incorrect.'}
            )

        if not user.check_password(password):
            raise serializers.ValidationError(
                {'detail': 'Email ou mot de passe incorrect.'}
            )

        if not user.is_active:
            raise serializers.ValidationError(
                {'detail': 'Compte désactivé.'}
            )

        if user.status == User.Status.SUSPENDED:
            raise serializers.ValidationError(
                {'detail': 'Compte suspendu.'}
            )

        # Générer les tokens via SimpleJWT
        refresh = self.get_token(user)

        return {
            'refresh': str(refresh),
            'access':  str(refresh.access_token),
            'user': {
                'id':        str(user.id),
                'email':     user.email,
                'full_name': user.full_name,
                'role':      user.role,
            }
        }

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # Ajouter des claims personnalisés
        token['role']  = user.role
        token['email'] = user.email
        return token


class AdminRegisterSerializer(serializers.Serializer):
    """Inscription d'une nouvelle entreprise + admin."""
    company_name = serializers.CharField(max_length=200)
    email        = serializers.EmailField()
    password     = serializers.CharField(min_length=8, write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Cet email est déjà utilisé.")
        return value.lower()

    def validate(self, attrs):
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError(
                {'confirm_password': 'Les mots de passe ne correspondent pas.'}
            )
        return attrs

    def create(self, validated_data):
        from django.db import transaction
        with transaction.atomic():
            company = Company.objects.create(
                name     = validated_data['company_name'],
                email    = validated_data['email'],
                is_active= True,
                is_main  = False,
            )
            user = User.objects.create_admin_by_email(
                email        = validated_data['email'],
                password     = validated_data['password'],
                first_name   = "Admin",
                last_name    = validated_data['company_name'],
            )
        return {'company': company, 'user': user}


# ─────────────────────────────────────────────────────────────
# COMPANY
# ─────────────────────────────────────────────────────────────

class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model  = Company
        fields = ['id', 'name', 'email', 'logo', 'is_active', 'is_main']
        read_only_fields = ['id']


# ─────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────

class DashboardStatsSerializer(serializers.Serializer):
    """KPIs principaux."""
    courses    = serializers.DictField()
    conducteurs= serializers.DictField()
    clients    = serializers.DictField()
    finances   = serializers.DictField()
    fraude     = serializers.DictField()


class RecentRideSerializer(serializers.ModelSerializer):
    """Course récente pour le dashboard."""
    conducteur  = serializers.SerializerMethodField()
    client_name = serializers.CharField(source='client.full_name', read_only=True)
    statut      = serializers.SerializerMethodField()
    heure       = serializers.SerializerMethodField()
    distance    = serializers.SerializerMethodField()
    montant     = serializers.SerializerMethodField()
    ini         = serializers.SerializerMethodField()

    class Meta:
        model  = Ride
        fields = [
            'id', 'ini', 'conducteur', 'client_name',
            'distance', 'montant', 'statut', 'heure',
        ]

    def get_conducteur(self, obj):
        return obj.driver.user.full_name if obj.driver else '—'

    def get_ini(self, obj):
        name = obj.driver.user.full_name if obj.driver else 'XX'
        return ''.join([n[0].upper() for n in name.split()[:2]])

    def get_statut(self, obj):
        if obj.disputes.exists():
            return 'litige'
        map_ = {
            'accepted': 'en_cours', 'driver_en_route': 'en_cours',
            'started': 'en_cours', 'completed': 'terminée',
            'cancelled': 'annulée',
        }
        return map_.get(obj.status, obj.status)

    def get_heure(self, obj):
        return obj.created_at.strftime('%H:%M')

    def get_distance(self, obj):
        km = obj.actual_distance_km or (obj.request.estimated_distance_km if obj.request else None)
        return f"{km} km" if km else '—'

    def get_montant(self, obj):
        return str(obj.total_fare or obj.request.estimated_price if obj.request else '—')


# ─────────────────────────────────────────────────────────────
# DRIVERS
# ─────────────────────────────────────────────────────────────

class DriverDocumentAdminSerializer(serializers.ModelSerializer):
    type_display   = serializers.CharField(source='get_document_type_display',       read_only=True)
    status_display = serializers.CharField(source='get_verification_status_display', read_only=True)

    class Meta:
        model  = DriverDocument
        fields = [
            'id', 'document_type', 'type_display',
            'file', 'verification_status', 'status_display',
            'expires_at', 'verified_at', 'rejection_reason',
        ]


class DriverAdminListSerializer(serializers.ModelSerializer):
    """Vue liste — validation conducteurs."""
    nom              = serializers.CharField(source='user.full_name',       read_only=True)
    telephone        = serializers.CharField(source='user.phone_number',    read_only=True)
    email            = serializers.CharField(source='user.email',           read_only=True)
    mobile_money     = serializers.SerializerMethodField()
    statut           = serializers.SerializerMethodField()
    date_inscription = serializers.DateTimeField(source='created_at', format='%d %b %Y', read_only=True)
    ville            = serializers.CharField(source='activity_zone', read_only=True)
    docs             = serializers.SerializerMethodField()
    docs_count       = serializers.SerializerMethodField()
    ini              = serializers.SerializerMethodField()

    class Meta:
        model  = DriverProfile
        fields = [
            'id', 'ini', 'nom', 'telephone', 'email',
            'mobile_money', 'statut', 'date_inscription',
            'ville', 'docs', 'docs_count',
            'average_rating', 'total_rides',
        ]

    def get_ini(self, obj):
        name = obj.user.full_name
        return ''.join([n[0].upper() for n in name.split()[:2]])

    def get_mobile_money(self, obj):
        if obj.user.mobile_money_operator and obj.user.mobile_money_number:
            labels = {'orange': 'Orange Money', 'moov': 'Moov Money'}
            op = labels.get(obj.user.mobile_money_operator, obj.user.mobile_money_operator)
            return f"{op} — {obj.user.mobile_money_number}"
        return '—'

    def get_statut(self, obj):
        labels = {'pending': 'En Attente', 'approved': 'Validé', 'rejected': 'Refusé'}
        return labels.get(obj.validation_status, obj.validation_status)

    def get_docs(self, obj):
        docs = obj.documents.all()
        result = {}
        for doc in docs:
            result[doc.document_type] = {
                'valide': doc.verification_status == 'verified',
                'expiry': doc.expires_at.strftime('%m/%Y') if doc.expires_at else '—',
                'label':  doc.get_document_type_display(),
            }
        return result

    def get_docs_count(self, obj):
        docs    = obj.documents.all()
        total   = docs.count()
        valides = docs.filter(verification_status='verified').count()
        return f"{valides}/{total}"


class DriverValidateSerializer(serializers.Serializer):
    """Validation ou rejet d'un conducteur."""
    action = serializers.ChoiceField(choices=['validate', 'reject'])
    motif  = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs['action'] == 'reject' and not attrs.get('motif'):
            raise serializers.ValidationError(
                {'motif': 'Le motif est obligatoire pour un rejet.'}
            )
        return attrs


# ─────────────────────────────────────────────────────────────
# RIDES
# ─────────────────────────────────────────────────────────────

class RideAdminSerializer(serializers.ModelSerializer):
    conducteurIni  = serializers.SerializerMethodField()
    conducteur     = serializers.SerializerMethodField()
    conducteurTel  = serializers.SerializerMethodField()
    client         = serializers.SerializerMethodField()
    clientTel      = serializers.SerializerMethodField()
    soldeClient    = serializers.SerializerMethodField()
    soldeMinimum   = serializers.SerializerMethodField()
    clientPaye     = serializers.BooleanField(source='is_paid', read_only=True)
    depart         = serializers.SerializerMethodField()
    arrivee        = serializers.SerializerMethodField()
    distance       = serializers.SerializerMethodField()
    montant        = serializers.SerializerMethodField()
    duree          = serializers.SerializerMethodField()
    statut         = serializers.CharField(source='status', read_only=True)
    heure          = serializers.SerializerMethodField()
    date           = serializers.SerializerMethodField()
    note           = serializers.SerializerMethodField()
    vitesse        = serializers.SerializerMethodField()
    progression    = serializers.SerializerMethodField()
    modePaiement   = serializers.SerializerMethodField()
    colis          = serializers.SerializerMethodField()
    coords         = serializers.SerializerMethodField()
    timeline       = serializers.SerializerMethodField()
    motifAnnulation = serializers.SerializerMethodField()
    motifLitige    = serializers.SerializerMethodField()

    class Meta:
        model  = Ride
        fields = [
            'id', 'conducteurIni', 'conducteur', 'conducteurTel',
            'client', 'clientTel', 'soldeClient', 'soldeMinimum', 'clientPaye',
            'depart', 'arrivee', 'distance', 'montant', 'duree',
            'statut', 'heure', 'date', 'note', 'vitesse', 'progression',
            'modePaiement', 'colis', 'coords', 'timeline',
            'motifAnnulation', 'motifLitige',
        ]

    # ─── Conducteur ──────────────────────────────────────
    def get_conducteurIni(self, obj):
        if obj.driver:
            name = obj.driver.user.full_name
            return ''.join([n[0].upper() for n in name.split()[:2]])
        return '??'

    def get_conducteur(self, obj):
        return obj.driver.user.full_name if obj.driver else '—'

    def get_conducteurTel(self, obj):
        return obj.driver.user.phone_number if obj.driver else '—'

    # ─── Client ──────────────────────────────────────────
    def get_client(self, obj):
        return obj.client.full_name if obj.client else '—'

    def get_clientTel(self, obj):
        return obj.client.phone_number if obj.client else '—'

    def get_soldeClient(self, obj):
        try:
            return float(obj.client.wallet.balance) if obj.client and hasattr(obj.client, 'wallet') else 0
        except:
            return 0

    def get_soldeMinimum(self, obj):
        return 500  # Valeur fixe ou à récupérer d'une config

    # ─── Trajet ──────────────────────────────────────────
    def get_depart(self, obj):
        return obj.request.pickup_address if obj.request else '—'

    def get_arrivee(self, obj):
        return obj.request.destination_address if obj.request else '—'

    def get_distance(self, obj):
        if obj.actual_distance_km:
            return f"{obj.actual_distance_km} km"
        if obj.request and obj.request.estimated_distance_km:
            return f"{obj.request.estimated_distance_km} km"
        return '—'

    def get_montant(self, obj):
        return float(obj.total_fare or 0)

    def get_duree(self, obj):
        if obj.actual_duration_min:
            return f"{obj.actual_duration_min} min"
        return '—'

    # ─── Dates ───────────────────────────────────────────
    def get_heure(self, obj):
        return obj.created_at.strftime('%H:%M')

    def get_date(self, obj):
        return obj.created_at.strftime('%d %B %Y')

    # ─── Note ────────────────────────────────────────────
    def get_note(self, obj):
        if obj.driver:
            return float(obj.driver.average_rating or 0)
        return None

    # ─── Vitesse / Progression ───────────────────────────
    def get_vitesse(self, obj):
        from apps.tracking.models import GPSPoint
        point = GPSPoint.objects.filter(
            driver=obj.driver
        ).order_by('-recorded_at').first()
        return float(point.speed_kmh) if point and point.speed_kmh else None

    def get_progression(self, obj):
        # Calcul simple : temps écoulé / temps estimé
        if obj.status in ['accepted', 'driver_en_route', 'started'] and obj.started_at:
            elapsed = (timezone.now() - obj.started_at).total_seconds() / 60
            estimated = obj.request.estimated_duration_min if obj.request else 20
            if estimated > 0:
                return min(int(elapsed / estimated * 100), 95)
        return None

    # ─── Paiement ────────────────────────────────────────
    def get_modePaiement(self, obj):
        if obj.payment_method in ['orange_money', 'moov_money']:
            return 'mobile_money'
        return obj.payment_method or None

    # ─── Colis ───────────────────────────────────────────
    def get_colis(self, obj):
        req = obj.request
        if not req or req.service_type != 'delivery':
            return None
        return {
            'description': req.package_description or '—',
            'poids': f"{req.package_weight_kg or 0} kg",
            'dimensions': req.package_dimensions or '—',
            'fragile': req.package_fragile or False,
            'valeur': req.package_value or 0,
            'instructions': req.delivery_instructions or '—',
        }

    # ─── Coordonnées GPS ─────────────────────────────────
    def get_coords(self, obj):
        points = list(obj.gps_points.order_by('sequence').values('latitude', 'longitude'))
        if len(points) < 2:
            return None
        # Normaliser en x,y entre 0 et 1
        lats = [float(p['latitude']) for p in points]
        lngs = [float(p['longitude']) for p in points]
        min_lat, max_lat = min(lats), max(lats)
        min_lng, max_lng = min(lngs), max(lngs)
        range_lat = max_lat - min_lat or 1
        range_lng = max_lng - min_lng or 1
        return [
            {
                'x': round((float(p['longitude']) - min_lng) / range_lng, 2),
                'y': round((float(p['latitude']) - min_lat) / range_lat, 2),
            }
            for p in points
        ]

    # ─── Timeline ────────────────────────────────────────
    def get_timeline(self, obj):
        timeline = [
            {'label': 'Commande passée', 'heure': obj.created_at.strftime('%H:%M'), 'done': True},
        ]
        if obj.driver:
            timeline.append({
                'label': 'Conducteur assigné',
                'heure': (obj.created_at + timezone.timedelta(minutes=1)).strftime('%H:%M'),
                'done': True,
            })
        if obj.started_at:
            timeline.append({
                'label': 'Prise en charge',
                'heure': obj.started_at.strftime('%H:%M'),
                'done': True,
            })
        if obj.status in ['driver_en_route', 'started', 'completed']:
            timeline.append({
                'label': 'En route',
                'heure': obj.started_at.strftime('%H:%M') if obj.started_at else '—',
                'done': True,
                'active': obj.status in ['driver_en_route', 'started'],
            })
        if obj.completed_at:
            timeline.append({
                'label': 'Terminée',
                'heure': obj.completed_at.strftime('%H:%M'),
                'done': True,
                'active': True,
            })
        if obj.status == 'cancelled':
            timeline.append({
                'label': 'Annulée',
                'heure': obj.cancelled_at.strftime('%H:%M') if obj.cancelled_at else '—',
                'done': True,
                'active': True,
                'error': True,
            })
        if obj.disputes.exists():
            dispute = obj.disputes.first()
            timeline.append({
                'label': 'Litige ouvert',
                'heure': dispute.created_at.strftime('%H:%M'),
                'done': True,
                'active': True,
                'error': True,
            })
        if obj.status in ['accepted', 'driver_en_route', 'started']:
            timeline.append({
                'label': 'Arrivée prévue',
                'heure': '—',
                'done': False,
            })
        return timeline

    # ─── Motifs ──────────────────────────────────────────
    def get_motifAnnulation(self, obj):
        return obj.cancellation_reason if obj.status == 'cancelled' else None

    def get_motifLitige(self, obj):
        if obj.disputes.exists():
            return obj.disputes.first().description
        return None

# ─────────────────────────────────────────────────────────────
# OPERATIONS (WALLETS)
# ─────────────────────────────────────────────────────────────

class WalletTransactionAdminSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(source='get_transaction_type_display', read_only=True)

    class Meta:
        model  = WalletTransaction
        fields = [
            'id', 'transaction_type', 'type_display',
            'direction', 'amount', 'balance_after',
            'status', 'description', 'created_at',
        ]
        read_only_fields = '__all__'


class ClientAdminSerializer(serializers.ModelSerializer):
    """Client avec wallet pour la gestion des opérations."""
    ini           = serializers.SerializerMethodField()
    solde         = serializers.SerializerMethodField()
    statut        = serializers.SerializerMethodField()
    total_courses = serializers.SerializerMethodField()
    operations    = serializers.SerializerMethodField()
    ops_en_attente= serializers.SerializerMethodField()
    depots_total  = serializers.SerializerMethodField()
    retraits_total= serializers.SerializerMethodField()
    inscription   = serializers.DateTimeField(source='created_at', format='%d %b %Y', read_only=True)

    class Meta:
        model  = User
        fields = [
            'id', 'client_code', 'ini', 'first_name', 'last_name',
            'phone_number', 'email', 'solde', 'statut',
            'inscription', 'total_courses',
            'depots_total', 'retraits_total',
            'ops_en_attente', 'operations',
        ]

    def get_ini(self, obj):
        return ''.join([n[0].upper() for n in obj.full_name.split()[:2]])

    def get_solde(self, obj):
        try:
            return float(obj.wallet.balance)
        except Exception:
            return 0.0

    def get_statut(self, obj):
        return {'active': 'actif', 'suspended': 'suspendu'}.get(obj.status, obj.status)

    def get_total_courses(self, obj):
        return obj.rides_as_client.count()

    def get_depots_total(self, obj):
        from django.db.models import Sum
        try:
            total = WalletTransaction.objects.filter(
                wallet=obj.wallet,
                transaction_type='depot',
                status='success',
            ).aggregate(t=Sum('amount'))['t']
            return float(total or 0)
        except Exception:
            return 0.0

    def get_retraits_total(self, obj):
        from django.db.models import Sum
        try:
            total = WalletTransaction.objects.filter(
                wallet=obj.wallet,
                transaction_type='retrait',
                status='success',
            ).aggregate(t=Sum('amount'))['t']
            return float(total or 0)
        except Exception:
            return 0.0

    def get_ops_en_attente(self, obj):
        from apps.payments.models import LigdiCashPayin, LigdiCashPayout
        try:
            return (
                LigdiCashPayin.objects.filter(user=obj, status__in=['otp_sent','pending']).count() +
                LigdiCashPayout.objects.filter(wallet__user=obj, status='pending').count()
            )
        except Exception:
            return 0

    def get_operations(self, obj):
        try:
            txs = WalletTransaction.objects.filter(
                wallet=obj.wallet
            ).order_by('-created_at')[:10]
            return WalletTransactionAdminSerializer(txs, many=True).data
        except Exception:
            return []


# ─────────────────────────────────────────────────────────────
# SURVEILLANCE
# ─────────────────────────────────────────────────────────────

class DriverSurveillanceSerializer(serializers.ModelSerializer):
    """Position conducteur pour la carte temps réel."""
    name     = serializers.CharField(source='user.full_name',    read_only=True)
    phone    = serializers.CharField(source='user.phone_number', read_only=True)
    lat      = serializers.DecimalField(source='current_latitude',  max_digits=9, decimal_places=6, read_only=True)
    lng      = serializers.DecimalField(source='current_longitude', max_digits=9, decimal_places=6, read_only=True)
    status   = serializers.SerializerMethodField()
    speed    = serializers.SerializerMethodField()
    trip_id  = serializers.SerializerMethodField()

    class Meta:
        model  = DriverProfile
        fields = ['id', 'name', 'phone', 'lat', 'lng', 'status', 'speed', 'trip_id']

    def get_status(self, obj):
        fraud_ids = self.context.get('fraud_ids', set())
        if obj.id in fraud_ids:
            return 'alerte'
        return 'en_course' if obj.is_on_ride else 'disponible'

    def get_speed(self, obj):
        from apps.tracking.models import GPSPoint
        point = GPSPoint.objects.filter(driver=obj).order_by('-recorded_at').first()
        return float(point.speed_kmh) if point and point.speed_kmh else 0

    def get_trip_id(self, obj):
        if obj.is_on_ride:
            from apps.rides.models import Ride
            ride = Ride.objects.filter(
                driver=obj,
                status__in=['accepted','driver_en_route','started']
            ).first()
            return str(ride.id)[:8].upper() if ride else None
        return None


# ─────────────────────────────────────────────────────────────
# PRICING
# ─────────────────────────────────────────────────────────────

class WeightSlabSerializer(serializers.ModelSerializer):
    class Meta:
        model  = WeightSlab
        fields = ['label', 'max_kg', 'surcharge', 'order']


class ValueSlabSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ValueSlab
        fields = ['label', 'max_value', 'surcharge', 'order']


class PackageNatureSerializer(serializers.ModelSerializer):
    class Meta:
        model  = PackageNature
        fields = ['nature_id', 'label', 'icon', 'multiplier', 'compatible_vehicles', 'order']


class VehicleTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model  = VehicleType
        fields = ['vehicle_id', 'label', 'icon', 'max_weight_kg', 'max_value_fcfa', 'base_surcharge', 'order']


class DeliveryPricingGridSerializer(serializers.ModelSerializer):
    weight_slabs    = WeightSlabSerializer(many=True, read_only=True)
    value_slabs     = ValueSlabSerializer(many=True, read_only=True)
    package_natures = PackageNatureSerializer(many=True, read_only=True)
    vehicle_types   = VehicleTypeSerializer(many=True, read_only=True)

    class Meta:
        model  = DeliveryPricingGrid
        fields = [
            'id', 'name', 'is_active',
            'price_per_km_pickup', 'price_per_km_delivery',
            'base_fare', 'min_fare', 'waiting_time_rate',
            'weight_slabs', 'value_slabs',
            'package_natures', 'vehicle_types',
            'updated_at',
        ]
        read_only_fields = ['id', 'updated_at', 'weight_slabs', 'value_slabs', 'package_natures', 'vehicle_types']


class PassengerPricingSerializer(serializers.ModelSerializer):
    class Meta:
        model  = PricingSetting
        fields = [
            'id', 'name', 'base_fare', 'price_per_km',
            'price_per_minute', 'minimum_fare',
            'commission_percent', 'surge_multiplier',
            'updated_at',
        ]
        read_only_fields = ['id', 'updated_at']


class PricingSimulateSerializer(serializers.Serializer):
    km_pickup      = serializers.FloatField(min_value=0)
    km_delivery    = serializers.FloatField(min_value=0)
    weight_kg      = serializers.FloatField(min_value=0)
    nature         = serializers.CharField(default='standard')
    declared_value = serializers.FloatField(min_value=0, default=0)
    vehicle        = serializers.CharField(default='moto')





    