from cmath import e

from django.shortcuts import render

# Create your views here.


import logging
from datetime import date
from decimal import Decimal

from django.core.mail import EmailMessage
from smtplib import SMTPException


import sys

from django.db.models import Sum, Count, Q
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from django.conf import settings

from apps.admin_panel.models import (
    Company, DeliveryPricingGrid,
    WeightSlab, ValueSlab, PackageNature,
    VehicleType, Dispute, SystemLog,
)
from apps.admin_panel.serializer import (
    AdminTokenObtainSerializer, AdminRegisterSerializer,
    CompanySerializer, DashboardStatsSerializer,
    RecentRideSerializer, DriverAdminListSerializer,
    DriverValidateSerializer, RideAdminSerializer,
    ClientAdminSerializer, DriverSurveillanceSerializer,
    DeliveryPricingGridSerializer, PassengerPricingSerializer,
    PricingSimulateSerializer,
)
from apps.core.permission import IsAdminUser
from apps.drivers.models import DriverProfile
from apps.fraud_detection.models import FraudCheck
from apps.notifications.services import NotificationService
from apps.payments.models import (
    PricingSetting, SiraWallet,
    LigdiCashPayin, LigdiCashPayout,
    WalletTransaction,
)
from apps.rides.models import Ride
from apps.users.models import OTPVerification, User

import random
import string
from datetime import timedelta
from django.core.mail import send_mail
from django.utils import timezone
from django.conf import settings

from apps.users.views import get_tokens_for_user


logger = logging.getLogger('apps')


# ─────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────

# ─── Helper OTP ───────────────────────────────────────────
def generate_otp_code(length=6) -> str:
    """Génère un code OTP numérique à 6 chiffres."""
    return ''.join(random.choices(string.digits, k=length))



# ─────────────────────────────────────────────────────────────
# AUTH — 2FA
# ─────────────────────────────────────────────────────────────

"""class AdminLoginView(TokenObtainPairView):
    serializer_class = AdminTokenObtainSerializer
    permission_classes = []

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except Exception:
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_401_UNAUTHORIZED
            )

        email = request.data.get('email', '').strip().lower()
        
        try:
            user = User.objects.get(email=email, role=User.Role.ADMIN)
        except User.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'detail': 'Utilisateur introuvable.'}},
                status=status.HTTP_401_UNAUTHORIZED
            )

        # Générer OTP
        code = generate_otp_code()
        
        OTPVerification.objects.filter(
            user=user, purpose=OTPVerification.Purpose.LOGIN, is_used=False
        ).update(is_used=True)

        otp = OTPVerification.objects.create(
            user=user,
            code=code,
            purpose=OTPVerification.Purpose.LOGIN,
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        # Envoyer email (avec fallback)
        subject = "SiRA Admin — Code de vérification"
        message = f"Bonjour {user.full_name},\n\nVotre code de connexion SiRA Admin est : {code}\n\nCe code est valable 10 minutes.\n\nCordialement,\nL'équipe SiRA"
        
        try:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )
        except Exception as e:
            print(f"[EMAIL FALLBACK] Code OTP pour {user.email}: {code}")

        # Log
        SystemLog.objects.create(
            action=SystemLog.ActionType.ADMIN_LOGIN,
            performed_by=user,
            description=f"Tentative de connexion admin — OTP envoyé à {user.email}",
            ip_address=request.META.get('REMOTE_ADDR'),
        )

        # ✅ TOUJOURS retourner une réponse
        return Response({
            'success': True,
            'message': f"Un code de vérification a été envoyé à {user.email}.",
            'data': {
                'email': user.email,
                'expires_in': 600,
            }
        })"""


class AdminLoginView(TokenObtainPairView):
    """
    Connexion admin intelligente :
    - Première connexion OU inactif > 5 mois → envoie code OTP
    - Connexion régulière → tokens JWT directs
    """
    serializer_class = AdminTokenObtainSerializer
    permission_classes = []

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except Exception:
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_401_UNAUTHORIZED
            )

        email = request.data.get('email', '').strip().lower()
        user = User.objects.get(email=email, role=User.Role.ADMIN)

        # Vérifier si la 2FA est nécessaire
        needs_2fa = self._needs_2fa(user)

        if needs_2fa:
            return self._send_otp(user, request)

        # Connexion directe
        user.last_seen_at = timezone.now()
        user.save(update_fields=['last_seen_at'])

        tokens = get_tokens_for_user(user)

        SystemLog.objects.create(
            action=SystemLog.ActionType.ADMIN_LOGIN,
            performed_by=user,
            description=f"Connexion admin directe : {user.email}",
            ip_address=request.META.get('REMOTE_ADDR'),
        )

        return Response({
            'success': True,
            'message': f"Bienvenue {user.full_name} 👋",
            'data': {
                'refresh': tokens['refresh'],
                'access': tokens['access'],
                'user': {
                    'id': str(user.id),
                    'email': user.email,
                    'full_name': user.full_name,
                    'role': user.role,
                }
            }
        })

    def _needs_2fa(self, user):
        """Détermine si la 2FA est nécessaire."""
        # Première connexion (last_seen_at est None)
        if user.last_seen_at is None:
            return True

        # Inactif depuis plus de 5 mois
        five_months_ago = timezone.now() - timedelta(days=150)
        if user.last_seen_at < five_months_ago:
            return True

        return False

    def _send_otp(self, user, request):
        """Envoie le code OTP et retourne la réponse."""
        code = generate_otp_code()

        # Invalider les anciens OTP
        OTPVerification.objects.filter(
            user=user, purpose=OTPVerification.Purpose.LOGIN, is_used=False
        ).update(is_used=True)

        # Créer le nouvel OTP
        OTPVerification.objects.create(
            user=user,
            code=code,
            purpose=OTPVerification.Purpose.LOGIN,
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        # Envoyer par email
        subject = "SiRA Admin — Code de vérification"
        message = f"Bonjour {user.full_name},\n\nVotre code de connexion SiRA Admin est : {code}\n\nCe code est valable 10 minutes.\n\nCordialement,\nL'équipe SiRA"

        try:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )
        except Exception as e:
            print(f"[EMAIL FALLBACK] Code OTP pour {user.email}: {code}")

        SystemLog.objects.create(
            action=SystemLog.ActionType.ADMIN_LOGIN,
            performed_by=user,
            description=f"Tentative de connexion admin — OTP envoyé à {user.email}",
            ip_address=request.META.get('REMOTE_ADDR'),
        )

        return Response({
            'success': True,
            'needs_2fa': True,
            'message': f"Un code de vérification a été envoyé à {user.email}.",
            'data': {
                'email': user.email,
                'expires_in': 600,
            }
        })






class AdminVerify2FAView(APIView):
    """
    ÉTAPE 2 : Vérification du code OTP reçu par email.
    Si le code est valide → délivre les vrais tokens JWT.
    """
    permission_classes = []  # Public

    def post(self, request):
        email = request.data.get('email', '').strip().lower()
        code = request.data.get('code', '').strip()

        if not email or not code:
            return Response(
                {'success': False, 'errors': {'detail': 'Email et code obligatoires.'}},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Vérifier que l'utilisateur existe
        try:
            user = User.objects.get(email=email, role=User.Role.ADMIN)
        except User.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'detail': 'Utilisateur introuvable.'}},
                status=status.HTTP_404_NOT_FOUND
            )

        # Vérifier le code OTP
        otp = OTPVerification.objects.filter(
            user=user,
            code=code,
            purpose=OTPVerification.Purpose.LOGIN,
            is_used=False,
            expires_at__gt=timezone.now()
        ).last()

        if not otp:
            return Response(
                {'success': False, 'errors': {'detail': 'Code invalide ou expiré.'}},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Marquer l'OTP comme utilisé
        otp.is_used = True
        otp.save(update_fields=['is_used'])

        # Mettre à jour last_seen
        user.last_seen_at = timezone.now()
        user.save(update_fields=['last_seen_at'])

        # Générer les vrais tokens JWT
        tokens = get_tokens_for_user(user)

        # Log succès
        SystemLog.objects.create(
            action=SystemLog.ActionType.ADMIN_LOGIN,
            performed_by=user,
            description=f"Connexion admin réussie (2FA validé) : {user.email}",
            ip_address=request.META.get('REMOTE_ADDR'),
        )

        return Response({
            'success': True,
            'message': f"Bienvenue {user.full_name} 👋",
            'data': {
                'refresh': tokens['refresh'],
                'access': tokens['access'],
                'user': {
                    'id': str(user.id),
                    'email': user.email,
                    'full_name': user.full_name,
                    'role': user.role,
                }
            }
        })


class AdminRegisterViewSet(viewsets.ViewSet):
    """POST → inscription entreprise + admin."""
    permission_classes = []

    def create(self, request):
        serializer = AdminRegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        result = serializer.save()
        return Response(
            {
                'success': True,
                'message': f"Entreprise '{result['company'].name}' créée !",
                'data': {
                    'company_id': str(result['company'].id),
                    'email':      result['user'].email,
                }
            },
            status=status.HTTP_201_CREATED
        )


# ─────────────────────────────────────────────────────────────
# COMPANIES
# ─────────────────────────────────────────────────────────────

class CompanyViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /admin/companies/      → liste pour le dropdown login
    GET /admin/companies/<id>/ → détail
    """
    permission_classes = []   # Public — pour le dropdown login
    serializer_class   = CompanySerializer
    queryset           = Company.objects.filter(is_active=True)


# ─────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────

class DashboardViewSet(viewsets.ViewSet):
    """
    Toutes les données du tableau de bord admin.
    Un seul ViewSet — plusieurs actions.
    """
    permission_classes = [IsAdminUser]

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        """KPIs principaux."""
        today     = date.today()
        now       = timezone.now()

        rides_today = Ride.objects.filter(created_at__date=today)
        rides_month = Ride.objects.filter(created_at__date__gte=today.replace(day=1))

        stats_courses = rides_today.aggregate(
            en_cours  = Count('id', filter=Q(status__in=['accepted','driver_en_route','started'])),
            terminees = Count('id', filter=Q(status='completed')),
            annulees  = Count('id', filter=Q(status='cancelled')),
        )

        return Response({
            'success': True,
            'data': {
                'courses': {
                    'total_today': rides_today.count(),
                    'total_month': rides_month.count(),
                    **stats_courses,
                    'litiges': Ride.objects.filter(disputes__isnull=False).distinct().count(),
                },
                'conducteurs': {
                    'actifs':              DriverProfile.objects.filter(is_available=True, validation_status='approved').count(),
                    'en_course':           DriverProfile.objects.filter(is_on_ride=True).count(),
                    'validations_pending': DriverProfile.objects.filter(validation_status='pending').count(),
                },
                'clients': {
                    'total_actifs': User.objects.filter(role='client', status='active').count(),
                },
                'finances': {
                    'revenus_jour': str(rides_today.filter(is_paid=True).aggregate(t=Sum('total_fare'))['t'] or 0),
                    'revenus_mois': str(rides_month.filter(is_paid=True).aggregate(t=Sum('total_fare'))['t'] or 0),
                    'currency':     'XOF',
                },
                'fraude': {
                    'alertes_pending': FraudCheck.objects.filter(statut__in=['alerte','avertissement'], check_status='pending').count(),
                },
            }
        })

    @action(detail=False, methods=['get'], url_path='recent-rides')
    def recent_rides(self, request):
        """8 dernières courses."""
        rides = Ride.objects.select_related(
            'driver__user', 'client', 'request'
        ).prefetch_related('disputes').order_by('-created_at')[:8]

        serializer = RecentRideSerializer(rides, many=True)
        return Response({'success': True, 'data': serializer.data})

    @action(detail=False, methods=['get'], url_path='alerts')
    def alerts(self, request):
        """Alertes récentes."""
        alerts = []

        # Fraudes
        for fc in FraudCheck.objects.filter(statut='alerte', check_status='pending').select_related('driver__user')[:3]:
            alerts.append({
                'id':     str(fc.id), 'niveau': 'red', 'icon': '⚠',
                'titre':  'Trajet anormal',
                'detail': f"{fc.driver.user.full_name} — écart {fc.distance_deviation_percent}%",
                'temps':  _time_ago(fc.created_at), 'type': 'fraud',
            })

        # Documents expirés
        from apps.drivers.models import DriverDocument
        for doc in DriverDocument.objects.filter(expires_at__lte=date.today()).select_related('driver__user')[:2]:
            alerts.append({
                'id':     str(doc.id), 'niveau': 'amber', 'icon': '📋',
                'titre':  'Document expiré',
                'detail': f"{doc.driver.user.full_name} — {doc.get_document_type_display()}",
                'temps':  _time_ago(doc.updated_at), 'type': 'document',
            })

        # Litiges
        for d in Dispute.objects.filter(status='open').select_related('filed_by')[:2]:
            alerts.append({
                'id':     str(d.id), 'niveau': 'blue', 'icon': '💬',
                'titre':  'Réclamation client',
                'detail': d.get_dispute_type_display(),
                'temps':  _time_ago(d.created_at), 'type': 'dispute',
            })

        return Response({'success': True, 'data': alerts[:8]})

    @action(detail=False, methods=['get'], url_path='ride-distribution')
    def ride_distribution(self, request):
        """Répartition des courses."""
        total     = Ride.objects.count() or 1
        terminees = Ride.objects.filter(status='completed').count()
        en_cours  = Ride.objects.filter(status__in=['accepted','driver_en_route','started']).count()
        annulees  = Ride.objects.filter(status='cancelled').count()
        litiges   = Ride.objects.filter(disputes__isnull=False).distinct().count()

        return Response({'success': True, 'data': [
            {'label': 'Terminées', 'count': terminees, 'pct': round(terminees/total*100), 'color': 'green'},
            {'label': 'En cours',  'count': en_cours,  'pct': round(en_cours/total*100),  'color': 'blue'},
            {'label': 'Annulées',  'count': annulees,  'pct': round(annulees/total*100),  'color': 'red'},
            {'label': 'Litiges',   'count': litiges,   'pct': round(litiges/total*100),   'color': 'amber'},
        ]})

    @action(detail=False, methods=['get'], url_path='pending-validations')
    def pending_validations(self, request):
        """Conducteurs en attente."""
        profiles = DriverProfile.objects.filter(
            validation_status='pending'
        ).select_related('user').prefetch_related('documents').order_by('-created_at')[:5]

        data = []
        for p in profiles:
            total   = p.documents.count()
            valides = p.documents.filter(verification_status='verified').count()
            ini     = ''.join([n[0].upper() for n in p.user.full_name.split()[:2]])
            data.append({
                'id':          str(p.id),
                'ini':         ini,
                'nom':         p.user.full_name,
                'tel':         p.user.phone_number,
                'docs_status': 'complets' if valides == total and total >= 4 else 'manquants',
                'docs_count':  f"{valides}/{total}",
                'date':        p.created_at.strftime('%d %b'),
            })

        return Response({'success': True, 'data': data})


# ─────────────────────────────────────────────────────────────
# CONDUCTEURS
# ─────────────────────────────────────────────────────────────

class DriverAdminViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /admin/drivers/          → liste
    GET /admin/drivers/<id>/     → détail
    POST /admin/drivers/<id>/validate/ → valider
    POST /admin/drivers/<id>/reject/   → rejeter
    """
    permission_classes = [IsAdminUser]
    serializer_class   = DriverAdminListSerializer

    def get_queryset(self):
        qs = DriverProfile.objects.select_related('user').prefetch_related('documents').order_by('-created_at')

        statut = self.request.query_params.get('status')
        search = self.request.query_params.get('search', '').strip()

        STATUS_MAP = {'En Attente': 'pending', 'Validé': 'approved', 'Refusé': 'rejected'}
        if statut in STATUS_MAP:
            qs = qs.filter(validation_status=STATUS_MAP[statut])

        if search:
            qs = qs.filter(
                Q(user__first_name__icontains=search) |
                Q(user__last_name__icontains=search)  |
                Q(user__phone_number__icontains=search)
            )
        return qs

    def list(self, request):
        qs = self.get_queryset()
        page      = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        total     = qs.count()
        data      = self.get_serializer(qs[(page-1)*page_size:page*page_size], many=True).data

        return Response({
            'success': True,
            'stats': {
                'tous':       DriverProfile.objects.count(),
                'en_attente': DriverProfile.objects.filter(validation_status='pending').count(),
                'valides':    DriverProfile.objects.filter(validation_status='approved').count(),
                'refuses':    DriverProfile.objects.filter(validation_status='rejected').count(),
            },
            'count':       total,
            'page':        page,
            'total_pages': (total + page_size - 1) // page_size,
            'data':        data,
        })

    @action(detail=True, methods=['patch'], url_path='validate')
    def validate_driver(self, request, pk=None):
        """Valider ou rejeter un conducteur."""
        profile    = self.get_object()
        serializer = DriverValidateSerializer(data=request.data)

        if not serializer.is_valid():
            return Response({'success': False, 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        if data['action'] == 'validate':
            profile.validation_status = DriverProfile.ValidationStatus.APPROVED
            profile.validated_at      = timezone.now()
            profile.save(update_fields=['validation_status', 'validated_at'])
            profile.user.status      = User.Status.ACTIVE
            profile.user.is_verified = True
            profile.user.save(update_fields=['status', 'is_verified'])

            NotificationService.create(
                recipient=profile.user, notification_type='account_approved',
                title="Compte approuvé ✅",
                body="Votre dossier a été validé. Vous pouvez recevoir des courses.",
            )
            SystemLog.objects.create(
                action=SystemLog.ActionType.DRIVER_VALIDATED,
                performed_by=request.user, target_user=profile.user,
                description=f"Conducteur validé : {profile.user.full_name}",
            )
            msg = f"{profile.user.full_name} validé avec succès."

        else:  # reject
            profile.validation_status = DriverProfile.ValidationStatus.REJECTED
            profile.rejection_reason  = data['motif']
            profile.save(update_fields=['validation_status', 'rejection_reason'])

            NotificationService.create(
                recipient=profile.user, notification_type='general',
                title="Dossier non validé ❌",
                body=f"Votre dossier n'a pas été accepté. Motif : {data['motif']}",
            )
            SystemLog.objects.create(
                action=SystemLog.ActionType.DRIVER_REJECTED,
                performed_by=request.user, target_user=profile.user,
                description=f"Conducteur rejeté : {profile.user.full_name}. Motif: {data['motif']}",
            )
            msg = f"{profile.user.full_name} rejeté."

        logger.info(f"Driver {data['action']} : {profile.user.phone_number}")
        return Response({'success': True, 'message': msg})


# ─────────────────────────────────────────────────────────────
# COURSES
# ─────────────────────────────────────────────────────────────

class RideAdminViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /admin/rides/      → liste avec filtres
    GET /admin/rides/<id>/ → détail
    """
    permission_classes = [IsAdminUser]
    serializer_class   = RideAdminSerializer

    def get_queryset(self):
        qs = Ride.objects.select_related(
            'driver__user', 'client', 'request'
        ).prefetch_related('disputes').order_by('-created_at')

        statut = self.request.query_params.get('status')
        search = self.request.query_params.get('search', '').strip()

        TAB_MAP = {
            'En cours':  ['accepted','driver_en_route','started'],
            'Terminées': ['completed'],
            'Annulées':  ['cancelled'],
        }

        if statut == 'Litiges':
            qs = qs.filter(disputes__isnull=False).distinct()
        elif statut in TAB_MAP:
            qs = qs.filter(status__in=TAB_MAP[statut])

        if search:
            qs = qs.filter(
                Q(client__first_name__icontains=search) |
                Q(client__last_name__icontains=search)  |
                Q(driver__user__first_name__icontains=search) |
                Q(driver__user__last_name__icontains=search)
            )
        return qs

    def list(self, request):
        qs        = self.get_queryset()
        page      = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        total     = qs.count()

        return Response({
            'success': True,
            'stats': {
                'en_cours':  Ride.objects.filter(status__in=['accepted','driver_en_route','started']).count(),
                'terminees': Ride.objects.filter(status='completed').count(),
                'annulees':  Ride.objects.filter(status='cancelled').count(),
                'litiges':   Ride.objects.filter(disputes__isnull=False).distinct().count(),
            },
            'count':       total,
            'page':        page,
            'total_pages': (total + page_size - 1) // page_size,
            'data':        self.get_serializer(qs[(page-1)*page_size:page*page_size], many=True).data,
        })


# ─────────────────────────────────────────────────────────────
# OPERATIONS
# ─────────────────────────────────────────────────────────────

class OperationsViewSet(viewsets.ViewSet):
    """
    Gestion des clients et wallets.
    GET /admin/operations/stats/
    GET /admin/operations/clients/
    PATCH /admin/operations/clients/<id>/suspend/
    """
    permission_classes = [IsAdminUser]

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        today = date.today()
        return Response({'success': True, 'data': {
            'total_soldes':   str(SiraWallet.objects.aggregate(t=Sum('balance'))['t'] or 0),
            'depots_mois':    str(LigdiCashPayin.objects.filter(status='completed', created_at__date__gte=today.replace(day=1)).aggregate(t=Sum('amount'))['t'] or 0),
            'retraits_mois':  str(LigdiCashPayout.objects.filter(status='completed', created_at__date__gte=today.replace(day=1)).aggregate(t=Sum('amount_requested'))['t'] or 0),
            'ops_en_attente': LigdiCashPayin.objects.filter(status__in=['otp_sent','pending']).count() + LigdiCashPayout.objects.filter(status='pending').count(),
            'currency':       'XOF',
        }})

    @action(detail=False, methods=['get'], url_path='clients')
    def clients(self, request):
        qs = User.objects.filter(role='client').select_related('wallet').order_by('-created_at')

        statut = request.query_params.get('status')
        search = request.query_params.get('search', '').strip()

        if statut == 'Actifs':     qs = qs.filter(status='active')
        elif statut == 'Suspendus': qs = qs.filter(status='suspended')
        elif statut == 'En attente': qs = qs.filter(status='pending')

        if search:
            qs = qs.filter(Q(first_name__icontains=search) | Q(last_name__icontains=search) | Q(phone_number__icontains=search))

        page      = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        total     = qs.count()

        return Response({
            'success':     True,
            'count':       total,
            'page':        page,
            'total_pages': (total + page_size - 1) // page_size,
            'data':        ClientAdminSerializer(qs[(page-1)*page_size:page*page_size], many=True).data,
        })

    @action(detail=False, methods=['patch'], url_path='clients/(?P<user_id>[^/.]+)/suspend')
    def suspend_client(self, request, user_id=None):
        try:
            user = User.objects.get(id=user_id, role='client')
        except User.DoesNotExist:
            return Response({'success': False, 'errors': {'detail': 'Client introuvable.'}}, status=status.HTTP_404_NOT_FOUND)

        action_  = request.data.get('action')
        reason   = request.data.get('reason', '')

        if action_ == 'suspend':
            user.status            = User.Status.SUSPENDED
            user.suspension_reason = reason
            user.save(update_fields=['status','suspension_reason'])
            SystemLog.objects.create(action=SystemLog.ActionType.USER_SUSPENDED, performed_by=request.user, target_user=user, description=f"Client suspendu. Motif: {reason}")
            return Response({'success': True, 'message': f"{user.full_name} suspendu."})

        elif action_ == 'activate':
            user.status = User.Status.ACTIVE
            user.suspension_reason = None
            user.save(update_fields=['status','suspension_reason'])
            return Response({'success': True, 'message': f"{user.full_name} réactivé."})

        return Response({'success': False, 'errors': {'detail': "Action invalide."}}, status=status.HTTP_400_BAD_REQUEST)


# ─────────────────────────────────────────────────────────────
# SURVEILLANCE
# ─────────────────────────────────────────────────────────────

class SurveillanceViewSet(viewsets.ViewSet):
    """
    GET /admin/surveillance/ → positions conducteurs temps réel.
    """
    permission_classes = [IsAdminUser]

    def list(self, request):
        status_filter = request.query_params.get('status')

        qs = DriverProfile.objects.filter(
            validation_status='approved',
            user__status='active',
            current_latitude__isnull=False,
            current_longitude__isnull=False,
        ).select_related('user')

        if status_filter == 'En course':    qs = qs.filter(is_on_ride=True)
        elif status_filter == 'Disponible': qs = qs.filter(is_available=True, is_on_ride=False)

        # IDs en alerte fraude
        fraud_ids = set(FraudCheck.objects.filter(
            statut='alerte', check_status='pending'
        ).values_list('driver_id', flat=True))

        if status_filter == 'Alertes':
            qs = qs.filter(id__in=fraud_ids)

        total      = qs.count()
        en_course  = qs.filter(is_on_ride=True).count()
        disponible = qs.filter(is_available=True, is_on_ride=False).count()
        alertes    = qs.filter(id__in=fraud_ids).count()

        serializer = DriverSurveillanceSerializer(
            qs, many=True, context={'fraud_ids': fraud_ids}
        )

        return Response({
            'success': True,
            'stats': {
                'total_actifs': total,
                'disponibles':  disponible,
                'en_course':    en_course,
                'alertes':      alertes,
            },
            'drivers': serializer.data,
        })


# ─────────────────────────────────────────────────────────────
# PRICING
# ─────────────────────────────────────────────────────────────

class PricingViewSet(viewsets.ViewSet):
    """
    GET/PUT /admin/pricing/passenger/ → tarif passager
    GET/PUT /admin/pricing/delivery/  → grille livraison
    POST    /admin/pricing/simulate/  → simulateur
    """
    permission_classes = [IsAdminUser]

    @action(detail=False, methods=['get','put'], url_path='passenger')
    def passenger(self, request):
        pricing = PricingSetting.objects.filter(is_active=True).first()
        if not pricing:
            return Response({'success': False, 'errors': {'detail': 'Aucune tarification active.'}}, status=status.HTTP_404_NOT_FOUND)

        if request.method == 'GET':
            return Response({'success': True, 'data': PassengerPricingSerializer(pricing).data})

        serializer = PassengerPricingSerializer(pricing, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response({'success': False, 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        serializer.save()
        SystemLog.objects.create(action=SystemLog.ActionType.PRICING_UPDATED, performed_by=request.user, description="Tarification passager mise à jour")
        return Response({'success': True, 'message': "Tarification mise à jour.", 'data': serializer.data})

    @action(detail=False, methods=['get','put'], url_path='delivery')
    def delivery(self, request):
        grid = DeliveryPricingGrid.objects.filter(is_active=True).prefetch_related(
            'weight_slabs', 'value_slabs', 'package_natures', 'vehicle_types'
        ).first()

        if not grid:
            return Response({'success': False, 'errors': {'detail': 'Aucune grille active.'}}, status=status.HTTP_404_NOT_FOUND)

        if request.method == 'GET':
            return Response({'success': True, 'data': DeliveryPricingGridSerializer(grid).data})

        data = request.data

        # Mise à jour distances
        for field in ['price_per_km_pickup','price_per_km_delivery','base_fare','min_fare','waiting_time_rate']:
            if field in data:
                setattr(grid, field, Decimal(str(data[field])))
        grid.save()

        # Mise à jour tranches poids
        for i, s in enumerate(data.get('weightSlabs', [])):
            WeightSlab.objects.filter(grid=grid, order=i).update(surcharge=s.get('surcharge', 0))

        # Mise à jour tranches valeur
        for i, s in enumerate(data.get('valueSlabs', [])):
            ValueSlab.objects.filter(grid=grid, order=i).update(surcharge=s.get('surcharge', 0))

        # Mise à jour natures
        for n in data.get('packageNatures', []):
            PackageNature.objects.filter(grid=grid, nature_id=n.get('id')).update(
                multiplier=n.get('multiplier', 1.0),
                compatible_vehicles=n.get('compatible', []),
            )

        # Mise à jour véhicules
        for v in data.get('vehicles', []):
            VehicleType.objects.filter(grid=grid, vehicle_id=v.get('id')).update(
                max_weight_kg=v.get('maxWeight', 15),
                max_value_fcfa=v.get('maxValue', 500000),
                base_surcharge=v.get('baseSurcharge', 0),
            )

        SystemLog.objects.create(action=SystemLog.ActionType.PRICING_UPDATED, performed_by=request.user, description="Grille livraison mise à jour")
        grid.refresh_from_db()
        return Response({'success': True, 'message': "Grille enregistrée.", 'data': DeliveryPricingGridSerializer(grid).data})

    @action(detail=False, methods=['post'], url_path='simulate')
    def simulate(self, request):
        """Simulateur de tarif livraison."""
        serializer = PricingSimulateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        d    = serializer.validated_data
        grid = DeliveryPricingGrid.objects.filter(is_active=True).prefetch_related(
            'weight_slabs', 'value_slabs', 'package_natures', 'vehicle_types'
        ).first()

        if not grid:
            return Response({'success': False, 'errors': {'detail': 'Aucune grille active.'}}, status=status.HTTP_404_NOT_FOUND)

        nat = grid.package_natures.filter(nature_id=d['nature']).first()
        veh = grid.vehicle_types.filter(vehicle_id=d['vehicle']).first()

        if not nat or not veh:
            return Response({'success': False, 'errors': {'detail': 'Nature ou véhicule invalide.'}}, status=status.HTTP_400_BAD_REQUEST)

        if d['vehicle'] not in nat.compatible_vehicles:
            return Response({'success': True, 'data': {'compatible': False, 'tarif_estime': 0}})

        # Tranche poids
        weight_surcharge = 0
        for slab in grid.weight_slabs.all():
            if d['weight_kg'] <= float(slab.max_kg):
                weight_surcharge = float(slab.surcharge)
                break

        # Tranche valeur
        value_surcharge = 0
        for slab in grid.value_slabs.all():
            if d['declared_value'] <= float(slab.max_value):
                value_surcharge = float(slab.surcharge)
                break

        dist    = d['km_pickup'] * float(grid.price_per_km_pickup) + d['km_delivery'] * float(grid.price_per_km_delivery)
        base    = float(grid.base_fare) + float(veh.base_surcharge)
        sub     = (dist + base + weight_surcharge + value_surcharge) * float(nat.multiplier)
        tarif   = max(sub, float(grid.min_fare))

        return Response({'success': True, 'data': {
            'compatible':   True,
            'tarif_estime': round(tarif),
            'detail': {
                'collecte':        round(d['km_pickup']   * float(grid.price_per_km_pickup)),
                'livraison':       round(d['km_delivery'] * float(grid.price_per_km_delivery)),
                'poids':           weight_surcharge,
                'valeur':          value_surcharge,
                'multiplicateur':  float(nat.multiplier),
                'base':            float(grid.base_fare),
                'surcharge_engin': float(veh.base_surcharge),
            }
        }})


# ─────────────────────────────────────────────────────────────
# ANTI-FRAUDE — réutilisation étape 10
# ─────────────────────────────────────────────────────────────

class FraudAdminViewSet(viewsets.ViewSet):
    """
    GET  /admin/fraud/               → liste
    GET  /admin/fraud/<id>/          → détail
    PATCH /admin/fraud/<id>/resolve/ → résoudre
    POST /admin/fraud/trigger/<id>/  → déclencher manuellement
    """
    permission_classes = [IsAdminUser]

    def list(self, request):
        from apps.fraud_detection.views import FraudCheckListView
        return FraudCheckListView.as_view()(request._request)

    def retrieve(self, request, pk=None):
        from apps.fraud_detection.views import FraudCheckDetailView
        return FraudCheckDetailView.as_view()(request._request, fraud_id=pk)

    @action(detail=True, methods=['patch'], url_path='resolve')
    def resolve(self, request, pk=None):
        from apps.fraud_detection.views import FraudCheckResolveView
        return FraudCheckResolveView.as_view()(request._request, fraud_id=pk)


# ─── Helper ───────────────────────────────────────────────────

def _time_ago(dt) -> str:
    if not dt:
        return '—'
    diff    = timezone.now() - dt
    minutes = int(diff.total_seconds() / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h"
    return f"{diff.days} j"


class AdminForgotPasswordView(APIView):
    """
    ÉTAPE 1 : Demande de réinitialisation de mot de passe.
    Envoie un lien de réinitialisation par email.
    """
    permission_classes = []  # Public

    def post(self, request):
        email = request.data.get('email', '').strip().lower()

        if not email:
            return Response(
                {'success': False, 'errors': {'detail': 'Email obligatoire.'}},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user = User.objects.get(email=email, role=User.Role.ADMIN)

            msg = EmailMessage(
                subject=subject,
                body=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email],
                connection=None,  # utilisera le backend par défaut
            )
            result = msg.send(fail_silently=False)
            print(f">>> EMAIL SENT: {result}", file=sys.stderr)
        except SMTPException as e:
            print(f"!!! SMTP ERROR: {e}", file=sys.stderr)
        except Exception as e:
            print(f"!!! UNEXPECTED ERROR: {e}", file=sys.stderr)
        except User.DoesNotExist:
            # Pour des raisons de sécurité, ne pas révéler si l'email existe
            return Response({
                'success': True,
                'message': 'Si cet email existe, un lien de réinitialisation a été envoyé.'
            })

        # Générer un token de réinitialisation SimpleJWT à courte durée
        from rest_framework_simplejwt.tokens import AccessToken
        reset_token = str(AccessToken.for_user(user))
        
        # Invalider les anciens tokens de reset
        OTPVerification.objects.filter(
            user=user,
            purpose='reset',
            is_used=False
        ).update(is_used=True)

        # Stocker le token
        OTPVerification.objects.create(
            user=user,
            code=reset_token[:6],  # On stocke les 6 premiers caractères comme référence
            purpose='reset',
            expires_at=timezone.now() + timedelta(minutes=30),
        )

        # Envoyer l'email avec le lien
      
        reset_link = f"{getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')}/resetpassword?token={reset_token}&email={email}"
        
        subject = "SiRA Admin — Réinitialisation de mot de passe"
        message = f"""
Bonjour {user.full_name},

Vous avez demandé la réinitialisation de votre mot de passe SiRA Admin.

Cliquez sur le lien ci-dessous pour créer un nouveau mot de passe :
{reset_link}

Ce lien expire dans 30 minutes.

Si vous n'avez pas demandé cette réinitialisation, ignorez cet email.

Cordialement,
L'équipe SiRA
        """
        
        try:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )
        except Exception:
            logger.error(f"[EMAIL ERROR] Échec d'envoi à {user.email} : {e}")
            print(f"!!! SMTP ERROR for {user.email}: {e}", file=sys.stderr)

        SystemLog.objects.create(
            action=SystemLog.ActionType.ADMIN_LOGIN,
            performed_by=user,
            description=f"Demande de réinitialisation de mot de passe : {user.email}",
            ip_address=request.META.get('REMOTE_ADDR'),
        )

        return Response({
            'success': True,
            'message': 'Si cet email existe, un lien de réinitialisation a été envoyé.'
        })


class AdminResetPasswordView(APIView):
     permission_classes = []

    def post(self, request):
        email = request.data.get('email', '').strip().lower()

        if not email:
            return Response(
                {'success': False, 'errors': {'detail': 'Email obligatoire.'}},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Vérifier si l'utilisateur existe
        try:
            user = User.objects.get(email=email, role=User.Role.ADMIN)
        except User.DoesNotExist:
            # Par sécurité, on ne révèle pas que l'email n'existe pas
            return Response({
                'success': True,
                'message': 'Si cet email existe, un lien de réinitialisation a été envoyé.'
            })

        # À partir d'ici, 'user' est défini
        from rest_framework_simplejwt.tokens import AccessToken
        reset_token = str(AccessToken.for_user(user))

        # Invalider les anciens tokens de reset
        OTPVerification.objects.filter(
            user=user, purpose='reset', is_used=False
        ).update(is_used=True)

        # Stocker une trace
        OTPVerification.objects.create(
            user=user,
            code=reset_token[:6],
            purpose='reset',
            expires_at=timezone.now() + timedelta(minutes=30),
        )

        # Lien dynamique
        from django.conf import settings
        frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
        reset_link = f"{frontend_url}/resetpassword?token={reset_token}&email={email}"

        subject = "SiRA Admin — Réinitialisation de mot de passe"
        message = f"""
Bonjour {user.full_name},

Vous avez demandé la réinitialisation de votre mot de passe SiRA Admin.

Cliquez sur le lien ci-dessous pour créer un nouveau mot de passe :
{reset_link}

Ce lien expire dans 30 minutes.

Si vous n'avez pas demandé cette réinitialisation, ignorez cet email.

Cordialement,
L'équipe SiRA
        """

        try:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )
        except Exception as e:
            import sys
            print(f"!!! SMTP ERROR for {user.email}: {e}", file=sys.stderr)

        SystemLog.objects.create(
            action=SystemLog.ActionType.ADMIN_LOGIN,
            performed_by=user,
            description=f"Demande de réinitialisation de mot de passe : {user.email}",
            ip_address=request.META.get('REMOTE_ADDR'),
        )

        return Response({
            'success': True,
            'message': 'Si cet email existe, un lien de réinitialisation a été envoyé.'
        })