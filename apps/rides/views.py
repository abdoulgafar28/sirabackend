from django.shortcuts import render
from httpx import request

# Create your views here.
from apps.tracking.ws_utils import notify_driver_new_ride


import logging
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

#from apps.core.permission import IsClient, IsAuthenticated

from apps.core.permission import IsClient   # tes permissions custom
from rest_framework.permissions import IsAuthenticated  # la permission DRF native


from apps.core.utils import (
    calculate_haversine_distance,
    estimate_fare,
    find_nearby_drivers,
)
from apps.drivers.serializer import DriverProfileListSerializer
from apps.notifications.services import NotificationService
from apps.payments.models import PricingSetting
from apps.rides.models import Ride, RideRequest
from apps.rides.serializer import (
    FareEstimateSerializer,
    RideDetailSerializer,
    RideListSerializer,
    RideRequestCreateSerializer,
    RideRequestDetailSerializer,
)
from apps.reviews.serializer import ReviewCreateSerializer, ReviewListSerializer
from apps.reviews.models import Review

logger = logging.getLogger('apps')


# ─────────────────────────────────────────────────────────────
# 1. ESTIMATION TARIFAIRE
# ─────────────────────────────────────────────────────────────

class FareEstimateView(APIView):
    """
    POST → calcule le tarif estimé AVANT de créer la course.
    Appelé dès que le client saisit son point de départ
    et sa destination sur la carte.
    """
    permission_classes = [IsClient]

    def post(self, request):
        serializer = FareEstimateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data

        # Calcul distance Haversine
        distance_km = calculate_haversine_distance(
            float(data['pickup_latitude']),
            float(data['pickup_longitude']),
            float(data['destination_latitude']),
            float(data['destination_longitude']),
        )

        # Récupérer la tarification active
        pricing = PricingSetting.objects.filter(is_active=True).first()
        if not pricing:
            return Response(
                {
                    'success': False,
                    'errors': {'detail': "Aucune tarification active. Contactez l'administrateur."}
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        # Calcul du tarif
        total_fare = estimate_fare(
            distance_km,
            data['service_type'],
            pricing,
        )

        # Estimation durée (vitesse moyenne 30 km/h en ville)
        estimated_duration = int((distance_km / 30) * 60)

        # Détail du calcul
        if data['service_type'] == 'delivery':
            base_fare  = pricing.delivery_base_fare
            per_km     = pricing.delivery_price_per_km
        else:
            base_fare  = pricing.base_fare
            per_km     = pricing.price_per_km

        distance_fare = total_fare - base_fare

        return Response(
            {
                'success': True,
                'data': {
                    'distance_km':       round(distance_km, 2),
                    'base_fare':         str(base_fare),
                    'distance_fare':     str(distance_fare),
                    'total_fare':        str(total_fare),
                    'surge_multiplier':  str(pricing.surge_multiplier),
                    'estimated_duration_min': estimated_duration,
                    'currency':          'XOF',
                    'service_type':      data['service_type'],
                    'pricing_detail': {
                        'base_fare':     str(base_fare),
                        'price_per_km':  str(per_km),
                        'minimum_fare':  str(pricing.minimum_fare),
                    }
                }
            },
            status=status.HTTP_200_OK
        )


# ─────────────────────────────────────────────────────────────
# 2. CONDUCTEURS DISPONIBLES À PROXIMITÉ
# ─────────────────────────────────────────────────────────────

class NearbyDriversView(APIView):
    """
    GET → liste les conducteurs disponibles autour du client.
    Paramètres :
      ?latitude=12.37&longitude=-1.52  → position du client
      ?radius_km=5                     → rayon de recherche (défaut 5km)
    """
    permission_classes = [IsClient]

    def get(self, request):
        # Récupérer la position du client
        try:
            latitude  = float(request.query_params.get('latitude'))
            longitude = float(request.query_params.get('longitude'))
        except (TypeError, ValueError):
            return Response(
                {
                    'success': False,
                    'errors': {'detail': "Paramètres latitude et longitude obligatoires."}
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Valider les coordonnées
        if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
            return Response(
                {
                    'success': False,
                    'errors': {'detail': "Coordonnées GPS invalides."}
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        radius_km = float(request.query_params.get('radius_km', 5.0))
        radius_km = min(radius_km, 20.0)  # Max 20 km

        # Trouver les conducteurs proches
        nearby = find_nearby_drivers(latitude, longitude, radius_km)

        if not nearby:
            return Response(
                {
                    'success': True,
                    'count':   0,
                    'message': "Aucun conducteur disponible dans votre zone pour le moment.",
                    'data':    [],
                },
                status=status.HTTP_200_OK
            )

        # Sérialiser avec la distance
        result = []
        for item in nearby:
            driver_data = DriverProfileListSerializer(item['driver']).data
            driver_data['distance_km'] = item['distance_km']
            # Estimer le temps d'arrivée (30 km/h moyenne)
            driver_data['eta_minutes'] = int((item['distance_km'] / 30) * 60) + 1
            result.append(driver_data)

        return Response(
            {
                'success': True,
                'count':   len(result),
                'data':    result,
            },
            status=status.HTTP_200_OK
        )


# ─────────────────────────────────────────────────────────────
# 3. CRÉATION DEMANDE DE COURSE
# ─────────────────────────────────────────────────────────────

class RideRequestCreateView(APIView):
    """
    POST → client crée une demande de course.
    Le système notifie automatiquement les conducteurs proches.
    """
    permission_classes = [IsClient]

    def post(self, request):
        # Vérifier qu'il n'a pas déjà une course en cours
        active_ride = Ride.objects.filter(
            client=request.user,
            status__in=[
                Ride.Status.ACCEPTED,
                Ride.Status.DRIVER_EN_ROUTE,
                Ride.Status.STARTED,
            ]
        ).first()

        if active_ride:
            return Response(
                {
                    'success': False,
                    'errors': {
                        'detail': (
                            f"Vous avez déjà une course en cours "
                            f"(ID: {active_ride.id}). "
                            f"Terminez-la avant d'en créer une nouvelle."
                        )
                    }
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Vérifier demande en attente existante
        pending_request = RideRequest.objects.filter(
            client=request.user,
            status=RideRequest.Status.PENDING,
            expires_at__gt=timezone.now(),
        ).first()

        if pending_request:
            return Response(
                {
                    'success': False,
                    'errors': {
                        'detail': (
                            f"Vous avez déjà une demande en attente "
                            f"(ID: {pending_request.id}). "
                            f"Attendez ou annulez-la d'abord."
                        )
                    }
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = RideRequestCreateSerializer(
            data=request.data,
            context={'request': request}
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        ride_request = serializer.save()

        # Notifier les conducteurs proches
        nearby_drivers = find_nearby_drivers(
            float(ride_request.pickup_latitude),
            float(ride_request.pickup_longitude),
            radius_km=5.0,
        )

        notified_count = 0
        for item in nearby_drivers:
            driver = item['driver']
            NotificationService.create(
                recipient=driver.user,
                notification_type='ride_request',
                title="Nouvelle course disponible ! 🏍️",
                body=(
                    f"Course à {item['distance_km']} km de vous. "
                    f"Tarif estimé : {ride_request.estimated_price} XOF."
                ),
                data={
                    'ride_request_id':  str(ride_request.id),
                    'pickup_address':   ride_request.pickup_address or '',
                    'destination_address': ride_request.destination_address or '',
                    'estimated_price':  str(ride_request.estimated_price),
                    'distance_km':      str(ride_request.estimated_distance_km),
                    'service_type':     ride_request.service_type,
                },
            )
            notified_count += 1

        logger.info(
            f"Demande {ride_request.id} créée par {request.user.phone_number}. "
            f"{notified_count} conducteurs notifiés."
        )

        return Response(
            {
                'success': True,
                'message': (
                    f"Demande créée. {notified_count} conducteur(s) notifié(s)."
                    if notified_count > 0
                    else "Demande créée. Recherche de conducteurs en cours..."
                ),
                'data': RideRequestDetailSerializer(ride_request).data,
            },
            status=status.HTTP_201_CREATED
        )


class RideRequestCancelView(APIView):
    """
    POST → client annule sa demande en attente.
    """
    permission_classes = [IsClient]

    def post(self, request, request_id):
        try:
            ride_request = RideRequest.objects.get(
                id=request_id,
                client=request.user,
            )
        except RideRequest.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'detail': "Demande introuvable."}},
                status=status.HTTP_404_NOT_FOUND
            )

        # Seules les demandes PENDING peuvent être annulées ici
        if ride_request.status != RideRequest.Status.PENDING:
            return Response(
                {
                    'success': False,
                    'errors': {
                        'detail': (
                            f"Impossible d'annuler une demande avec le statut "
                            f"'{ride_request.status}'. "
                            f"Utilisez l'annulation de course si elle est déjà acceptée."
                        )
                    }
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        ride_request.status           = RideRequest.Status.CANCELLED
        ride_request.cancelled_by     = RideRequest.CancelledBy.CLIENT
        ride_request.cancellation_reason = request.data.get('reason', 'Annulé par le client')
        ride_request.save(update_fields=['status', 'cancelled_by', 'cancellation_reason'])

        logger.info(
            f"Demande {request_id} annulée par client {request.user.phone_number}"
        )

        return Response(
            {'success': True, 'message': "Demande annulée avec succès."},
            status=status.HTTP_200_OK
        )


# ─────────────────────────────────────────────────────────────
# 4. SUIVI DE LA COURSE EN COURS
# ─────────────────────────────────────────────────────────────

class ClientCurrentRideView(APIView):
    """
    GET → course en cours du client avec position du conducteur.
    Utilisé en fallback si WebSocket non disponible (polling).
    """
    permission_classes = [IsClient]

    def get(self, request):
        # Chercher d'abord une course active
        ride = Ride.objects.filter(
            client=request.user,
            status__in=[
                Ride.Status.ACCEPTED,
                Ride.Status.DRIVER_EN_ROUTE,
                Ride.Status.STARTED,
            ]
        ).select_related(
            'driver__user',
            'driver__vehicle',
            'request',
        ).first()

        if not ride:
            # Chercher une demande en attente
            pending = RideRequest.objects.filter(
                client=request.user,
                status=RideRequest.Status.PENDING,
                expires_at__gt=timezone.now(),
            ).first()

            if pending:
                return Response(
                    {
                        'success':    True,
                        'ride_state': 'searching',
                        'message':    "Recherche d'un conducteur en cours...",
                        'data':       RideRequestDetailSerializer(pending).data,
                    },
                    status=status.HTTP_200_OK
                )

            return Response(
                {
                    'success':    True,
                    'ride_state': 'none',
                    'message':    "Aucune course en cours.",
                    'data':       None,
                },
                status=status.HTTP_200_OK
            )

        # Données de la course + position temps réel du conducteur
        ride_data    = RideDetailSerializer(ride).data
        driver       = ride.driver

        # Position actuelle du conducteur
        ride_data['driver_current_location'] = {
            'latitude':    str(driver.current_latitude) if driver.current_latitude else None,
            'longitude':   str(driver.current_longitude) if driver.current_longitude else None,
            'updated_at':  driver.location_updated_at,
        }

        # Infos contact conducteur
        ride_data['driver_contact'] = {
            'name':         driver.user.full_name,
            'phone_number': driver.user.phone_number,
            'photo':        request.build_absolute_uri(driver.user.photo.url)
                            if driver.user.photo else None,
            'vehicle': {
                'brand':        driver.vehicle.brand if hasattr(driver, 'vehicle') else None,
                'model':        driver.vehicle.model if hasattr(driver, 'vehicle') else None,
                'color':        driver.vehicle.color if hasattr(driver, 'vehicle') else None,
                'plate_number': driver.vehicle.plate_number if hasattr(driver, 'vehicle') else None,
            }
        }

        return Response(
            {
                'success':    True,
                'ride_state': ride.status,
                'data':       ride_data,
            },
            status=status.HTTP_200_OK
        )


class ClientCancelRideView(APIView):
    """
    POST → client annule une course déjà acceptée.
    Uniquement possible si la course n'est pas encore démarrée.
    """
    permission_classes = [IsClient]

    def post(self, request, ride_id):
        try:
            ride = Ride.objects.get(
                id=ride_id,
                client=request.user,
            )
        except Ride.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'detail': "Course introuvable."}},
                status=status.HTTP_404_NOT_FOUND
            )

        # Vérifier que la course peut être annulée
        cancellable_statuses = [
            Ride.Status.ACCEPTED,
            Ride.Status.DRIVER_EN_ROUTE,
        ]
        if ride.status not in cancellable_statuses:
            return Response(
                {
                    'success': False,
                    'errors': {
                        'detail': (
                            f"Impossible d'annuler une course avec le statut '{ride.status}'. "
                            f"Une course démarrée ne peut plus être annulée."
                        )
                    }
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        from django.db import transaction
        with transaction.atomic():
            ride.status              = Ride.Status.CANCELLED
            ride.cancelled_at        = timezone.now()
            ride.cancellation_reason = request.data.get('reason', 'Annulé par le client')
            ride.save(update_fields=['status', 'cancelled_at', 'cancellation_reason'])

            # Libérer le conducteur
            driver             = ride.driver
            driver.is_on_ride  = False
            driver.save(update_fields=['is_on_ride'])

            # Mettre à jour la demande
            ride.request.status       = RideRequest.Status.CANCELLED
            ride.request.cancelled_by = RideRequest.CancelledBy.CLIENT
            ride.request.save(update_fields=['status', 'cancelled_by'])

        # Notifier le conducteur
        NotificationService.create(
            recipient=ride.driver.user,
            notification_type='ride_cancelled',
            title="Course annulée ❌",
            body=f"{request.user.full_name} a annulé la course.",
            data={'ride_id': str(ride.id)},
        )

        logger.info(
            f"Course {ride_id} annulée par client {request.user.phone_number}"
        )

        return Response(
            {'success': True, 'message': "Course annulée avec succès."},
            status=status.HTTP_200_OK
        )


# ─────────────────────────────────────────────────────────────
# 5. HISTORIQUE DES COURSES CLIENT
# ─────────────────────────────────────────────────────────────

class ClientRideHistoryView(APIView):
    """
    GET → historique complet des courses du client.
    Filtres : ?status=completed&date_from=2024-01-01&date_to=2024-01-31
    """
    permission_classes = [IsClient]

    def get(self, request):
        rides = Ride.objects.filter(
            client=request.user
        ).order_by('-created_at')

        # Filtres optionnels
        status_filter = request.query_params.get('status')
        date_from     = request.query_params.get('date_from')
        date_to       = request.query_params.get('date_to')

        if status_filter:
            rides = rides.filter(status=status_filter)
        if date_from:
            rides = rides.filter(created_at__date__gte=date_from)
        if date_to:
            rides = rides.filter(created_at__date__lte=date_to)

        # Pagination
        page      = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        start     = (page - 1) * page_size
        end       = start + page_size
        total     = rides.count()

        serializer = RideListSerializer(rides[start:end], many=True)

        return Response(
            {
                'success':     True,
                'count':       total,
                'page':        page,
                'page_size':   page_size,
                'total_pages': (total + page_size - 1) // page_size,
                'data':        serializer.data,
            },
            status=status.HTTP_200_OK
        )


class ClientRideDetailView(APIView):
    """
    GET → détail complet d'une course spécifique.
    """
    permission_classes = [IsClient]

    def get(self, request, ride_id):
        try:
            ride = Ride.objects.get(
                id=ride_id,
                client=request.user,
            )
        except Ride.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'detail': "Course introuvable."}},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response(
            {'success': True, 'data': RideDetailSerializer(ride).data},
            status=status.HTTP_200_OK
        )


# ─────────────────────────────────────────────────────────────
# 6. ÉVALUATION DU CONDUCTEUR
# ─────────────────────────────────────────────────────────────

class ReviewCreateView(APIView):
    """
    POST → client note le conducteur après une course terminée.
    Une seule note par course.
    """
    permission_classes = [IsClient]

    def post(self, request):
        serializer = ReviewCreateSerializer(
            data=request.data,
            context={'request': request}
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        review = serializer.save()

        # Notifier le conducteur
        NotificationService.create(
            recipient=review.driver.user,
            notification_type='general',
            title="Nouvelle évaluation ⭐",
            body=(
                f"{request.user.full_name} vous a donné "
                f"{review.rating}/5 étoiles."
            ),
            data={
                'review_id': str(review.id),
                'rating':    review.rating,
                'ride_id':   str(review.ride.id),
            },
        )

        logger.info(
            f"Évaluation {review.rating}/5 pour conducteur "
            f"{review.driver.user.phone_number} par {request.user.phone_number}"
        )

        return Response(
            {
                'success': True,
                'message': "Merci pour votre évaluation !",
                'data': {
                    'id':        str(review.id),
                    'rating':    review.rating,
                    'comment':   review.comment,
                    'created_at': review.created_at,
                }
            },
            status=status.HTTP_201_CREATED
        )


class DriverReviewsListView(APIView):
    """
    GET → liste des avis reçus par un conducteur.
    Accessible par tous les utilisateurs authentifiés.
    """
    permission_classes = [IsClient]

    def get(self, request, driver_id):
        from apps.drivers.models import DriverProfile
        try:
            driver = DriverProfile.objects.get(id=driver_id)
        except DriverProfile.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'detail': "Conducteur introuvable."}},
                status=status.HTTP_404_NOT_FOUND
            )

        reviews = Review.objects.filter(
            driver=driver
        ).order_by('-created_at')

        # Statistiques
        from django.db.models import Avg, Count
        stats = reviews.aggregate(
            average=Avg('rating'),
            total=Count('id'),
        )

        # Répartition par note
        distribution = {}
        for i in range(1, 6):
            distribution[f'{i}_star'] = reviews.filter(rating=i).count()

        page      = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        start     = (page - 1) * page_size
        end       = start + page_size

        serializer = ReviewListSerializer(reviews[start:end], many=True)

        return Response(
            {
                'success': True,
                'stats': {
                    'average_rating':  round(stats['average'] or 0, 2),
                    'total_reviews':   stats['total'],
                    'distribution':    distribution,
                },
                'count': reviews.count(),
                'data':  serializer.data,
            },
            status=status.HTTP_200_OK
        )