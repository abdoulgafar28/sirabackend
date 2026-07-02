from gc import get_stats
from pyexpat.errors import messages

from django.shortcuts import render

# Create your views here.



import logging
from datetime import date, timedelta

# Après mise à jour du statut
from apps.tracking.ws_utils import notify_ride_status

from django.contrib.auth import get_user_model
from django.db.models import Sum, Count, Avg
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from apps.core.permission import IsDriver, IsAdminUser, IsOwnerOrAdmin
from apps.core.utils import find_nearby_drivers
from apps.drivers.models import DriverProfile, DriverDocument
from apps.drivers.serializer import (
    DriverProfileDetailSerializer,
    DriverProfileListSerializer,
    DriverDocumentSerializer,
    DriverDocumentUploadSerializer,
    DriverAvailabilitySerializer,
    DriverValidationSerializer,
)
from apps.payments.models import DriverEarning
from apps.payments.serializer import (
    DriverEarningSerializer,
    DriverEarningsSummarySerializer,
)
from apps.rides.models import Ride, RideRequest
from apps.rides.serializer import RideListSerializer, RideRequestListSerializer
from apps.users.serializer import UserRegistrationSerializer
from apps.notifications.services import NotificationService

User = get_user_model()
logger = logging.getLogger('apps')


# ─────────────────────────────────────────────────────────────
# INSCRIPTION CONDUCTEUR
# ─────────────────────────────────────────────────────────────

class DriverRegisterView(APIView):
    """
    Inscription en 2 étapes :
    Étape 1 — POST /drivers/register/     → crée le compte user + profil driver
    Étape 2 — POST /drivers/documents/    → upload des documents
    """
    permission_classes = []  # public
    parser_classes     = [JSONParser]

    def post(self, request):
        # Forcer le rôle driver
        data = request.data.copy()
        data['role'] = 'driver'

        serializer = UserRegistrationSerializer(data=data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = serializer.save()

        # Créer automatiquement le profil conducteur
        driver_profile = DriverProfile.objects.create(user=user)

        logger.info(f"Nouveau conducteur inscrit : {user.phone_number}")

        return Response(
            {
                'success': True,
                'message': (
                    "Compte conducteur créé. "
                    "Uploadez vos documents pour validation."
                ),
                'data': {
                    'user_id':    str(user.id),
                    'driver_id':  str(driver_profile.id),
                    'phone_number': user.phone_number,
                    'next_step':  'Upload documents via POST /api/v1/drivers/documents/',
                }
            },
            status=status.HTTP_201_CREATED
        )


# ─────────────────────────────────────────────────────────────
# PROFIL CONDUCTEUR
# ─────────────────────────────────────────────────────────────

class DriverProfileView(APIView):
    """
    GET   → profil complet du conducteur connecté
    PATCH → mise à jour partielle (zone d'activité, etc.)
    """
    permission_classes = [IsDriver]

    def get(self, request):
        profile = request.user.driver_profile
        serializer = DriverProfileDetailSerializer(profile)
        return Response(
            {'success': True, 'data': serializer.data},
            status=status.HTTP_200_OK
        )

    def patch(self, request):
        profile    = request.user.driver_profile
        serializer = DriverProfileDetailSerializer(
            profile,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        serializer.save()
        return Response(
            {'success': True, 'message': "Profil mis à jour.", 'data': serializer.data},
            status=status.HTTP_200_OK
        )


# ─────────────────────────────────────────────────────────────
# DOCUMENTS
# ─────────────────────────────────────────────────────────────

class DriverDocumentListView(APIView):
    """
    GET  → liste tous les documents du conducteur
    POST → upload d'un nouveau document
    """
    permission_classes = [IsDriver]
    parser_classes     = [MultiPartParser, FormParser]

    def get(self, request):
        docs = DriverDocument.objects.filter(
            driver=request.user.driver_profile
        )
        serializer = DriverDocumentSerializer(docs, many=True)
        return Response(
            {'success': True, 'data': serializer.data},
            status=status.HTTP_200_OK
        )

    def post(self, request):
        driver = request.user.driver_profile
        serializer = DriverDocumentUploadSerializer(
            data=request.data,
            context={'driver': driver}
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        document = serializer.save(driver=driver)

        # Vérifier si tous les documents obligatoires sont uploadés
        self._check_all_documents_uploaded(driver)

        logger.info(
            f"Document uploadé : {document.document_type} "
            f"par {request.user.phone_number}"
        )

        return Response(
            {
                'success': True,
                'message': f"Document '{document.get_document_type_display()}' uploadé.",
                'data': DriverDocumentSerializer(document).data,
            },
            status=status.HTTP_201_CREATED
        )

    def _check_all_documents_uploaded(self, driver):
        """
        Vérifie si tous les documents obligatoires sont présents.
        Si oui, notifie l'admin pour validation.
        """
        required = {
            DriverDocument.DocumentType.CNI,
            DriverDocument.DocumentType.PERMIS,
            DriverDocument.DocumentType.CARTE_GRISE,
            DriverDocument.DocumentType.PHOTO_DRIVER,
            DriverDocument.DocumentType.PHOTO_MOTO,
        }
        uploaded = set(
            DriverDocument.objects.filter(driver=driver)
            .values_list('document_type', flat=True)
        )
        if required.issubset(uploaded):
            # Notifier les admins
            admins = User.objects.filter(role=User.Role.ADMIN)
            for admin in admins:
                NotificationService.create(
                    recipient=admin,
                    notification_type='general',
                    title="Nouveau dossier conducteur à valider",
                    body=(
                        f"{driver.user.full_name} a soumis tous ses documents. "
                        f"Dossier en attente de validation."
                    ),
                    data={'driver_id': str(driver.id)},
                )


class DriverDocumentDetailView(APIView):
    """
    PUT → remplacer un document existant
    DELETE → supprimer un document
    """
    permission_classes = [IsDriver]
    parser_classes     = [MultiPartParser, FormParser]

    def get_object(self, request, document_type):
        try:
            return DriverDocument.objects.get(
                driver=request.user.driver_profile,
                document_type=document_type,
            )
        except DriverDocument.DoesNotExist:
            return None

    def put(self, request, document_type):
        doc = self.get_object(request, document_type)
        if not doc:
            return Response(
                {'success': False, 'errors': {'detail': 'Document introuvable.'}},
                status=status.HTTP_404_NOT_FOUND
            )

        # Réinitialiser le statut de vérification
        doc.file                = request.data.get('file', doc.file)
        doc.verification_status = DriverDocument.VerificationStatus.PENDING
        doc.rejection_reason    = None
        doc.verified_at         = None
        doc.save()

        return Response(
            {
                'success': True,
                'message': "Document remplacé. En attente de vérification.",
                'data': DriverDocumentSerializer(doc).data,
            },
            status=status.HTTP_200_OK
        )

    def delete(self, request, document_type):
        doc = self.get_object(request, document_type)
        if not doc:
            return Response(
                {'success': False, 'errors': {'detail': 'Document introuvable.'}},
                status=status.HTTP_404_NOT_FOUND
            )
        doc.delete()
        return Response(
            {'success': True, 'message': "Document supprimé."},
            status=status.HTTP_204_NO_CONTENT
        )


# ─────────────────────────────────────────────────────────────
# DISPONIBILITÉ ET POSITION GPS
# ─────────────────────────────────────────────────────────────

class DriverAvailabilityView(APIView):
    """
    PATCH → activer/désactiver disponibilité + mettre à jour position GPS.
    Appelé par Flutter quand le conducteur appuie sur le bouton ON/OFF.
    """
    permission_classes = [IsDriver]

    def patch(self, request):
        profile    = request.user.driver_profile

        # Un conducteur en course ne peut pas se mettre indisponible
        if profile.is_on_ride and not request.data.get('is_available', True):
            return Response(
                {
                    'success': False,
                    'errors': {
                        'detail': "Impossible de se mettre indisponible pendant une course."
                    }
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = DriverAvailabilitySerializer(
            profile,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        profile = serializer.save()
        profile.location_updated_at = timezone.now()
        profile.save(update_fields=['location_updated_at'])

        # Mettre à jour aussi sur le User
        request.user.last_latitude  = profile.current_latitude
        request.user.last_longitude = profile.current_longitude
        request.user.last_seen_at   = timezone.now()
        request.user.save(update_fields=[
            'last_latitude', 'last_longitude', 'last_seen_at'
        ])

        logger.info(
            f"Conducteur {request.user.phone_number} "
            f"→ disponible={profile.is_available}"
        )

        return Response(
            {
                'success': True,
                'message': (
                    "Vous êtes maintenant disponible." if profile.is_available
                    else "Vous êtes maintenant indisponible."
                ),
                'data': {
                    'is_available':   profile.is_available,
                    'latitude':       str(profile.current_latitude),
                    'longitude':      str(profile.current_longitude),
                    'updated_at':     profile.location_updated_at,
                }
            },
            status=status.HTTP_200_OK
        )


# ─────────────────────────────────────────────────────────────
# DEMANDES DE COURSE
# ─────────────────────────────────────────────────────────────

class DriverRideRequestsView(APIView):
    """
    GET → liste les demandes de course à proximité du conducteur.
    Utilisé quand le conducteur n'a pas de WebSocket actif.
    (Fallback polling ou premier chargement)
    """
    permission_classes = [IsDriver]

    def get(self, request):
        profile = request.user.driver_profile

        if not profile.is_available:
            return Response(
                {
                    'success': False,
                    'errors': {
                        'detail': "Activez votre disponibilité pour voir les demandes."
                    }
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        if not profile.current_latitude or not profile.current_longitude:
            return Response(
                {
                    'success': False,
                    'errors': {'detail': "Position GPS non disponible."}
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Rayon de recherche (paramètre optionnel, défaut 5 km)
        radius = float(request.query_params.get('radius_km', 5.0))
        radius = min(radius, 20.0)  # Maximum 20 km

        # Trouver les demandes en attente dans le rayon
        pending_requests = RideRequest.objects.filter(
            status=RideRequest.Status.PENDING,
            expires_at__gt=timezone.now(),
        ).exclude(
            client=request.user
        )

        # Filtrer par distance
        nearby = []
        for ride_req in pending_requests:
            from apps.core.utils import calculate_haversine_distance
            dist = calculate_haversine_distance(
                float(profile.current_latitude),
                float(profile.current_longitude),
                float(ride_req.pickup_latitude),
                float(ride_req.pickup_longitude),
            )
            if dist <= radius:
                nearby.append({
                    'request': ride_req,
                    'distance_to_pickup_km': round(dist, 2),
                })

        nearby.sort(key=lambda x: x['distance_to_pickup_km'])

        # Sérialiser avec distance
        result = []
        for item in nearby:
            data = RideRequestListSerializer(item['request']).data
            data['distance_to_pickup_km'] = item['distance_to_pickup_km']
            result.append(data)

        return Response(
            {
                'success': True,
                'count':   len(result),
                'data':    result,
            },
            status=status.HTTP_200_OK
        )


class DriverAcceptRideView(APIView):
    """
    POST → conducteur accepte une demande de course.
    Crée l'objet Ride et notifie le client.
    """
    permission_classes = [IsDriver]

    def post(self, request, request_id):
        profile = request.user.driver_profile

        # Vérifications préalables
        if profile.is_on_ride:
            return Response(
                {
                    'success': False,
                    'errors': {'detail': "Vous avez déjà une course en cours."}
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            ride_request = RideRequest.objects.get(
                id=request_id,
                status=RideRequest.Status.PENDING,
            )
        except RideRequest.DoesNotExist:
            return Response(
                {
                    'success': False,
                    'errors': {'detail': "Demande introuvable ou déjà prise."}
                },
                status=status.HTTP_404_NOT_FOUND
            )

        # Vérifier que la demande n'est pas expirée
        if ride_request.expires_at < timezone.now():
            ride_request.status = RideRequest.Status.EXPIRED
            ride_request.save(update_fields=['status'])
            return Response(
                {
                    'success': False,
                    'errors': {'detail': "Cette demande a expiré."}
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Accepter la demande (transaction atomique)
        from django.db import transaction
        with transaction.atomic():
            # Mettre à jour la demande
            ride_request.status = RideRequest.Status.ACCEPTED
            ride_request.driver = profile
            ride_request.save(update_fields=['status', 'driver'])

            # Créer la course
            ride = Ride.objects.create(
                request=ride_request,
                client=ride_request.client,
                driver=profile,
                status=Ride.Status.ACCEPTED,
                base_fare=ride_request.estimated_price,
            )

            # Marquer le conducteur comme occupé
            profile.is_on_ride = True
            profile.save(update_fields=['is_on_ride'])

        # Notifier le client
        NotificationService.create(
            recipient=ride_request.client,
            notification_type='ride_accepted',
            title="Course acceptée ! 🏍️",
            body=(
                f"{request.user.full_name} a accepté votre course. "
                f"Il arrive bientôt."
            ),
            data={
                'ride_id':    str(ride.id),
                'driver_id':  str(profile.id),
                'driver_name': request.user.full_name,
                'driver_phone': request.user.phone_number,
            },
        )

        logger.info(
            f"Course {ride.id} acceptée par {request.user.phone_number}"
        )

        return Response(
            {
                'success': True,
                'message': "Course acceptée.",
                'data': {
                    'ride_id':         str(ride.id),
                    'client_name':     ride_request.client.full_name,
                    'client_phone':    ride_request.client.phone_number,
                    'pickup_latitude':       str(ride_request.pickup_latitude),
                    'pickup_longitude':      str(ride_request.pickup_longitude),
                    'pickup_address':        ride_request.pickup_address,
                    'destination_latitude':  str(ride_request.destination_latitude),
                    'destination_longitude': str(ride_request.destination_longitude),
                    'destination_address':   ride_request.destination_address,
                    'estimated_price':       str(ride_request.estimated_price),
                    'service_type':          ride_request.service_type,
                }
            },
            status=status.HTTP_200_OK
        )


class DriverRejectRideView(APIView):
    """
    POST → conducteur refuse une demande de course.
    La demande reste PENDING pour d'autres conducteurs.
    """
    permission_classes = [IsDriver]

    def post(self, request, request_id):
        try:
            ride_request = RideRequest.objects.get(
                id=request_id,
                status=RideRequest.Status.PENDING,
            )
        except RideRequest.DoesNotExist:
            return Response(
                {
                    'success': False,
                    'errors': {'detail': "Demande introuvable."}
                },
                status=status.HTTP_404_NOT_FOUND
            )

        # On ne change pas le statut — la demande reste disponible
        # pour d'autres conducteurs. On log juste le refus.
        logger.info(
            f"Demande {request_id} refusée par {request.user.phone_number}"
        )

        return Response(
            {'success': True, 'message': "Demande refusée."},
            status=status.HTTP_200_OK
        )


# ─────────────────────────────────────────────────────────────
# GESTION DE LA COURSE EN COURS
# ─────────────────────────────────────────────────────────────

class DriverCurrentRideView(APIView):
    """
    GET → course en cours du conducteur.
    """
    permission_classes = [IsDriver]

    def get(self, request):
        try:
            ride = Ride.objects.get(
                driver=request.user.driver_profile,
                status__in=[
                    Ride.Status.ACCEPTED,
                    Ride.Status.DRIVER_EN_ROUTE,
                    Ride.Status.STARTED,
                ]
            )
        except Ride.DoesNotExist:
            return Response(
                {'success': True, 'data': None, 'message': "Aucune course en cours."},
                status=status.HTTP_200_OK
            )

        from apps.rides.serializer import RideDetailSerializer
        return Response(
            {'success': True, 'data': RideDetailSerializer(ride).data},
            status=status.HTTP_200_OK
        )


class DriverUpdateRideStatusView(APIView):
    """
    PATCH → conducteur met à jour le statut de la course.

    Transitions autorisées :
    accepted → driver_en_route → started → completed
    accepted/driver_en_route   → cancelled
    """
    permission_classes = [IsDriver]

    TRANSITIONS = {
        Ride.Status.ACCEPTED:        [Ride.Status.DRIVER_EN_ROUTE, Ride.Status.CANCELLED],
        Ride.Status.DRIVER_EN_ROUTE: [Ride.Status.STARTED, Ride.Status.CANCELLED],
        Ride.Status.STARTED:         [Ride.Status.COMPLETED],
    }

    

    def patch(self, request, ride_id):
        try:
            ride = Ride.objects.get(
                id=ride_id,
                driver=request.user.driver_profile,
            )
        except Ride.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'detail': "Course introuvable."}},
                status=status.HTTP_404_NOT_FOUND
            )

        new_status = request.data.get('status')
        allowed    = self.TRANSITIONS.get(ride.status, [])

        if new_status not in allowed:
            return Response(
                {
                    'success': False,
                    'errors': {
                        'detail': (
                            f"Transition impossible : {ride.status} → {new_status}. "
                            f"Autorisées : {allowed}"
                        )
                    }
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        from django.db import transaction
        with transaction.atomic():
            old_status = ride.status
            ride.status = new_status

            # Horodatage selon le statut
            now = timezone.now()
            if new_status == Ride.Status.DRIVER_EN_ROUTE:
                ride.driver_arrived_at = None  # réinitialisé

            elif new_status == Ride.Status.STARTED:
                ride.started_at = now
                # Enregistrer le point GPS de départ réel
                ride.actual_pickup_latitude  = request.user.driver_profile.current_latitude
                ride.actual_pickup_longitude = request.user.driver_profile.current_longitude

            elif new_status == Ride.Status.COMPLETED:
                ride.completed_at = now
                ride.actual_dropoff_latitude  = request.user.driver_profile.current_latitude
                ride.actual_dropoff_longitude = request.user.driver_profile.current_longitude
                # Calculer la distance et le tarif final
                self._finalize_ride(ride)

            elif new_status == Ride.Status.CANCELLED:
                ride.cancelled_at         = now
                ride.cancellation_reason  = request.data.get('reason', '')
                # Libérer le conducteur
                request.user.driver_profile.is_on_ride = False
                request.user.driver_profile.save(update_fields=['is_on_ride'])

            ride.save()

        # Notifier le client
        self._notify_client(ride, new_status)

        logger.info(
            f"Course {ride_id} : {old_status} → {new_status} "
            f"par {request.user.phone_number}"
        )

        notify_ride_status(
            ride_id = str(ride.id),
            status  = ride.status,
            message = messages.get(ride.status, ''),
        )

        from apps.rides.serializer import RideDetailSerializer
        return Response(
            {
                'success': True,
                'message': f"Statut mis à jour : {new_status}",
                'data':    RideDetailSerializer(ride).data,
            },
            status=status.HTTP_200_OK
        )
    

    def _finalize_ride(self, ride):
        """
        Calcule la distance réelle, le tarif final
        et crée les objets Payment + DriverEarning.
        """
        from apps.core.utils import calculate_route_distance
        from apps.payments.models import PricingSetting

        # Distance réelle depuis les points GPS
        gps_points = list(
            ride.gps_points.order_by('sequence').values('latitude', 'longitude')
        )

        if len(gps_points) >= 2:
            actual_distance = calculate_route_distance(gps_points)
        else:
            # Fallback : distance directe départ → arrivée
            from apps.core.utils import calculate_haversine_distance
            actual_distance = calculate_haversine_distance(
                float(ride.actual_pickup_latitude),
                float(ride.actual_pickup_longitude),
                float(ride.actual_dropoff_latitude),
                float(ride.actual_dropoff_longitude),
            )

        # Durée réelle
        if ride.started_at and ride.completed_at:
            duration = int(
                (ride.completed_at - ride.started_at).total_seconds() / 60
            )
        else:
            duration = 0

        # Tarification
        pricing = PricingSetting.objects.filter(is_active=True).first()
        from apps.core.utils import estimate_fare
        total_fare = estimate_fare(
            actual_distance,
            ride.request.service_type,
            pricing,
        )

        commission = (
            total_fare * pricing.commission_percent / 100
            if pricing else 0
        )
        driver_earning_amount = total_fare - commission

        # Mettre à jour la course
        ride.actual_distance_km  = round(actual_distance, 2)
        ride.actual_duration_min = duration
        ride.total_fare          = total_fare
        ride.base_fare           = pricing.base_fare if pricing else 0
        ride.distance_fare       = total_fare - (pricing.base_fare if pricing else 0)
        ride.driver_earning      = driver_earning_amount
        ride.platform_commission = commission

        # Libérer le conducteur
        driver = ride.driver
        driver.is_on_ride    = False
        driver.total_rides   += 1
        driver.total_earnings = float(driver.total_earnings) + float(driver_earning_amount)
        driver.save(update_fields=['is_on_ride', 'total_rides', 'total_earnings'])

        # Créer le gain conducteur
        DriverEarning.objects.create(
            driver=driver,
            ride=ride,
            gross_amount=total_fare,
            commission_amount=commission,
            net_amount=driver_earning_amount,
            earning_date=date.today(),
        )

        # Lancer la vérification anti-fraude en arrière-plan
        self._trigger_fraud_check(ride)

    def _trigger_fraud_check(self, ride):
        """Lance l'analyse anti-fraude après la course."""
        try:
            from apps.fraud_detection.services import FraudDetectionService
            FraudDetectionService.analyze(ride)
        except Exception as e:
            logger.error(f"Erreur fraud check course {ride.id} : {e}")

    def _notify_client(self, ride, new_status):
        """Notifie le client selon le nouveau statut."""
        messages = {
            Ride.Status.DRIVER_EN_ROUTE: (
                "Votre conducteur est en route 🏍️",
                "Il arrive bientôt à votre position."
            ),
            Ride.Status.STARTED: (
                "Course démarrée ! 🚀",
                "Bonne route !"
            ),
            Ride.Status.COMPLETED: (
                "Course terminée ✅",
                f"Montant : {ride.total_fare} XOF. Merci d'utiliser SIRA !"
            ),
            Ride.Status.CANCELLED: (
                "Course annulée ❌",
                "Le conducteur a annulé la course."
            ),
        }

        if new_status in messages:
            title, body = messages[new_status]
            NotificationService.create(
                recipient=ride.client,
                notification_type=f'ride_{new_status}',
                title=title,
                body=body,
                data={'ride_id': str(ride.id)},
            )


# ─────────────────────────────────────────────────────────────
# HISTORIQUE ET GAINS
# ─────────────────────────────────────────────────────────────

class DriverRideHistoryView(APIView):
    """
    GET → historique complet des courses du conducteur.
    Filtres : ?status=completed&date_from=2024-01-01&date_to=2024-01-31
    """
    permission_classes = [IsDriver]

    def get(self, request):
        rides = Ride.objects.filter(
            driver=request.user.driver_profile
        ).order_by('-created_at')

        # Filtres optionnels
        status_filter    = request.query_params.get('status')
        date_from        = request.query_params.get('date_from')
        date_to          = request.query_params.get('date_to')

        if status_filter:
            rides = rides.filter(status=status_filter)
        if date_from:
            rides = rides.filter(created_at__date__gte=date_from)
        if date_to:
            rides = rides.filter(created_at__date__lte=date_to)

        # Pagination manuelle simple
        page      = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        start     = (page - 1) * page_size
        end       = start + page_size

        total      = rides.count()
        rides_page = rides[start:end]

        serializer = RideListSerializer(rides_page, many=True)

        return Response(
            {
                'success':    True,
                'count':      total,
                'page':       page,
                'page_size':  page_size,
                'total_pages': (total + page_size - 1) // page_size,
                'data':       serializer.data,
            },
            status=status.HTTP_200_OK
        )


class DriverEarningsView(APIView):
    """
    GET → gains du conducteur avec agrégats.
    ?period=today | week | month | custom
    ?date_from=2024-01-01&date_to=2024-01-31 (si period=custom)
    """
    permission_classes = [IsDriver]

    def get(self, request):
        driver = request.user.driver_profile
        period = request.query_params.get('period', 'today')
        today  = date.today()

        # Définir la période
        if period == 'today':
            date_from = today
            date_to   = today
        elif period == 'week':
            date_from = today - timedelta(days=today.weekday())
            date_to   = today
        elif period == 'month':
            date_from = today.replace(day=1)
            date_to   = today
        elif period == 'custom':
            try:
                from datetime import datetime
                date_from = datetime.strptime(
                    request.query_params.get('date_from', str(today)), '%Y-%m-%d'
                ).date()
                date_to = datetime.strptime(
                    request.query_params.get('date_to', str(today)), '%Y-%m-%d'
                ).date()
            except ValueError:
                return Response(
                    {
                        'success': False,
                        'errors': {'detail': "Format de date invalide. Utilisez YYYY-MM-DD."}
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            date_from = today
            date_to   = today

        # Agrégats
        earnings = DriverEarning.objects.filter(
            driver=driver,
            earning_date__range=(date_from, date_to),
        )

        aggregates = earnings.aggregate(
            total_rides=Count('id'),
            gross_total=Sum('gross_amount'),
            commission_total=Sum('commission_amount'),
            net_total=Sum('net_amount'),
        )

        # Détail des gains
        earnings_detail = DriverEarningSerializer(
            earnings.order_by('-earning_date'), many=True
        ).data

        return Response(
            {
                'success': True,
                'data': {
                    'summary': {
                        'period':           period,
                        'date_from':        str(date_from),
                        'date_to':          str(date_to),
                        'total_rides':      aggregates['total_rides'] or 0,
                        'gross_total':      str(aggregates['gross_total'] or 0),
                        'commission_total': str(aggregates['commission_total'] or 0),
                        'net_total':        str(aggregates['net_total'] or 0),
                        'currency':         'XOF',
                    },
                    'earnings': earnings_detail,
                }
            },
            status=status.HTTP_200_OK
        )