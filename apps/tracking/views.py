from django.shortcuts import render
# Create your views here.

import logging
from django.utils import timezone
from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permission import IsDriver, IsClient, IsAdminUser
from apps.core.utils import calculate_route_distance, calculate_haversine_distance
from apps.rides.models import Ride
from apps.tracking.models import GPSPoint, OfflineSyncQueue
from apps.tracking.serializer import (
    GPSPointSerializer,
    GPSPointBulkSerializer,
    DriverLocationUpdateSerializer,
    OfflineSyncQueueSerializer,
)

logger = logging.getLogger('apps')


# ─────────────────────────────────────────────────────────────
# 1. MISE À JOUR POSITION EN TEMPS RÉEL
# ─────────────────────────────────────────────────────────────

class DriverLocationUpdateView(APIView):
    """
    POST → conducteur envoie sa position GPS en temps réel.
    Appelé toutes les 3-5 secondes par Flutter pendant une course.

    2 actions simultanées :
    - Met à jour DriverProfile.current_latitude/longitude
    - Enregistre le point dans GPSPoint si course active
    """
    permission_classes = [IsDriver]

    def post(self, request):
        serializer = DriverLocationUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        data    = serializer.validated_data
        driver  = request.user.driver_profile
        now     = timezone.now()

        # ── Mettre à jour la position du conducteur ───────
        driver.current_latitude     = data['latitude']
        driver.current_longitude    = data['longitude']
        driver.location_updated_at  = now
        driver.save(update_fields=[
            'current_latitude',
            'current_longitude',
            'location_updated_at',
        ])

        # ── Enregistrer le point GPS si course active ─────
        gps_point = None
        active_ride = Ride.objects.filter(
            driver=driver,
            status=Ride.Status.STARTED,
        ).first()

        if active_ride:
            # Numéro de séquence — dernier point + 1
            last_sequence = GPSPoint.objects.filter(
                ride=active_ride
            ).order_by('-sequence').values_list('sequence', flat=True).first()

            sequence = (last_sequence + 1) if last_sequence is not None else 0

            gps_point = GPSPoint.objects.create(
                ride        = active_ride,
                driver      = driver,
                latitude    = data['latitude'],
                longitude   = data['longitude'],
                speed_kmh   = data.get('speed_kmh'),
                bearing     = data.get('bearing'),
                accuracy    = data.get('accuracy'),
                sequence    = sequence,
                recorded_at = now,
                is_offline  = False,
            )

        return Response(
            {
                'success': True,
                'data': {
                    'latitude':       str(data['latitude']),
                    'longitude':      str(data['longitude']),
                    'updated_at':     now,
                    'ride_active':    active_ride is not None,
                    'gps_point_id':   str(gps_point.id) if gps_point else None,
                    'sequence':       gps_point.sequence if gps_point else None,
                }
            },
            status=status.HTTP_200_OK
        )


# ─────────────────────────────────────────────────────────────
# 2. ENVOI GROUPÉ DE POINTS GPS (SYNC OFFLINE)
# ─────────────────────────────────────────────────────────────

class GPSPointBulkSyncView(APIView):
    """
    POST → Flutter envoie tous les points GPS accumulés
    pendant une période offline en une seule requête.

    Flutter gère :
    - Le stockage local (SQLite/Hive)
    - La détection de reconnexion
    - L'envoi groupé

    Le backend gère :
    - La réception et sauvegarde
    - Le recalcul de distance
    - Le marquage was_offline=True sur la course
    """
    permission_classes = [IsDriver]

    def post(self, request):
        serializer = GPSPointBulkSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        data    = serializer.validated_data
        driver  = request.user.driver_profile

        # Vérifier que la course appartient au conducteur
        try:
            ride = Ride.objects.get(
                id=data['ride_id'],
                driver=driver,
            )
        except Ride.DoesNotExist:
            return Response(
                {
                    'success': False,
                    'errors': {'detail': "Course introuvable ou non autorisée."}
                },
                status=status.HTTP_404_NOT_FOUND
            )

        points_data = data['points']

        with transaction.atomic():
            # Récupérer la dernière séquence existante
            last_sequence = GPSPoint.objects.filter(
                ride=ride
            ).order_by('-sequence').values_list('sequence', flat=True).first()

            next_sequence = (last_sequence + 1) if last_sequence is not None else 0

            # Créer tous les points en une seule requête (bulk_create)
            gps_points = []
            for i, point in enumerate(points_data):
                gps_points.append(GPSPoint(
                    ride        = ride,
                    driver      = driver,
                    latitude    = point['latitude'],
                    longitude   = point['longitude'],
                    altitude    = point.get('altitude'),
                    speed_kmh   = point.get('speed_kmh'),
                    bearing     = point.get('bearing'),
                    accuracy    = point.get('accuracy'),
                    sequence    = next_sequence + i,
                    recorded_at = point['recorded_at'],
                    is_offline  = True,
                    synced_at   = timezone.now(),
                ))

            created = GPSPoint.objects.bulk_create(gps_points)

            # Marquer la course comme ayant eu une période offline
            if not ride.was_offline:
                ride.was_offline = True
                ride.save(update_fields=['was_offline'])

            # Enregistrer dans la file de sync pour traçabilité
            OfflineSyncQueue.objects.create(
                driver      = driver,
                ride        = ride,
                data_type   = OfflineSyncQueue.DataType.GPS_POINTS,
                payload     = {
                    'points_count': len(created),
                    'first_sequence': next_sequence,
                    'last_sequence': next_sequence + len(created) - 1,
                },
                sync_status = OfflineSyncQueue.SyncStatus.SYNCED,
                recorded_at = points_data[0]['recorded_at'],
                synced_at   = timezone.now(),
            )

            # Recalculer la distance totale de la course
            updated_distance = self._recalculate_distance(ride)

        logger.info(
            f"Sync offline : {len(created)} points GPS pour course "
            f"{ride.id} par {request.user.phone_number}"
        )

        return Response(
            {
                'success': True,
                'message': f"{len(created)} points GPS synchronisés.",
                'data': {
                    'ride_id':          str(ride.id),
                    'points_synced':    len(created),
                    'updated_distance': updated_distance,
                    'was_offline':      ride.was_offline,
                }
            },
            status=status.HTTP_200_OK
        )

    def _recalculate_distance(self, ride) -> float:
        """Recalcule la distance totale depuis tous les points GPS."""
        points = list(
            GPSPoint.objects.filter(ride=ride)
            .order_by('sequence')
            .values('latitude', 'longitude')
        )

        if len(points) < 2:
            return 0.0

        distance = calculate_route_distance(points)

        # Mettre à jour la course
        ride.actual_distance_km = round(distance, 3)
        ride.save(update_fields=['actual_distance_km'])

        return distance


# ─────────────────────────────────────────────────────────────
# 3. POSITION ACTUELLE D'UN CONDUCTEUR
# ─────────────────────────────────────────────────────────────

class DriverCurrentLocationView(APIView):
    """
    GET → retourne la position actuelle d'un conducteur.
    Utilisé par le client pour voir où est son conducteur.
    Fallback si WebSocket non disponible.
    """
    permission_classes = [IsClient]

    def get(self, request, ride_id):
        # Vérifier que la course appartient au client
        try:
            ride = Ride.objects.get(
                id=ride_id,
                client=request.user,
                status__in=[
                    Ride.Status.ACCEPTED,
                    Ride.Status.DRIVER_EN_ROUTE,
                    Ride.Status.STARTED,
                ]
            )
        except Ride.DoesNotExist:
            return Response(
                {
                    'success': False,
                    'errors': {'detail': "Course introuvable ou déjà terminée."}
                },
                status=status.HTTP_404_NOT_FOUND
            )

        driver = ride.driver

        # Vérifier si la position est récente (< 30 secondes)
        is_fresh = False
        if driver.location_updated_at:
            delta    = timezone.now() - driver.location_updated_at
            is_fresh = delta.total_seconds() < 30

        # Dernier point GPS enregistré
        last_point = GPSPoint.objects.filter(
            ride=ride
        ).order_by('-sequence').first()

        return Response(
            {
                'success': True,
                'data': {
                    'driver_id':   str(driver.id),
                    'driver_name': driver.user.full_name,

                    # Position actuelle du conducteur
                    'current_location': {
                        'latitude':   str(driver.current_latitude),
                        'longitude':  str(driver.current_longitude),
                        'updated_at': driver.location_updated_at,
                        'is_fresh':   is_fresh,
                    },

                    # Dernier point GPS de la course
                    'last_gps_point': {
                        'latitude':    str(last_point.latitude) if last_point else None,
                        'longitude':   str(last_point.longitude) if last_point else None,
                        'speed_kmh':   str(last_point.speed_kmh) if last_point else None,
                        'recorded_at': last_point.recorded_at if last_point else None,
                        'sequence':    last_point.sequence if last_point else None,
                    } if last_point else None,

                    # Distance parcourue jusqu'ici
                    'distance_so_far_km': str(ride.actual_distance_km or 0),
                    'ride_status':        ride.status,
                }
            },
            status=status.HTTP_200_OK
        )


# ─────────────────────────────────────────────────────────────
# 4. HISTORIQUE DES POINTS GPS D'UNE COURSE
# ─────────────────────────────────────────────────────────────

class RideGPSTrailView(APIView):
    """
    GET → tous les points GPS d'une course terminée.
    Utilisé par l'admin pour visualiser le trajet réel
    et détecter les fraudes.
    """
    permission_classes = [IsAdminUser]

    def get(self, request, ride_id):
        try:
            ride = Ride.objects.get(id=ride_id)
        except Ride.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'detail': "Course introuvable."}},
                status=status.HTTP_404_NOT_FOUND
            )

        points = GPSPoint.objects.filter(
            ride=ride
        ).order_by('sequence').values(
            'sequence', 'latitude', 'longitude',
            'speed_kmh', 'bearing', 'accuracy',
            'recorded_at', 'is_offline',
        )

        points_list = list(points)

        # Statistiques du trajet
        stats = self._compute_trail_stats(points_list, ride)

        return Response(
            {
                'success': True,
                'data': {
                    'ride_id':     str(ride.id),
                    'stats':       stats,
                    'points':      points_list,
                    'total_points': len(points_list),
                }
            },
            status=status.HTTP_200_OK
        )

    def _compute_trail_stats(self, points: list, ride) -> dict:
        """Calcule les statistiques du trajet GPS."""
        if not points:
            return {}

        speeds = [
            float(p['speed_kmh'])
            for p in points
            if p['speed_kmh'] is not None
        ]

        offline_points = sum(1 for p in points if p['is_offline'])

        return {
            'total_points':      len(points),
            'offline_points':    offline_points,
            'online_points':     len(points) - offline_points,
            'max_speed_kmh':     max(speeds) if speeds else None,
            'avg_speed_kmh':     round(sum(speeds) / len(speeds), 2) if speeds else None,
            'gps_distance_km':   str(ride.actual_distance_km or 0),
            'had_offline_period': ride.was_offline,
            'duration_minutes':  ride.actual_duration_min,
        }


# ─────────────────────────────────────────────────────────────
# 5. CALCUL DISTANCE FINALE
# ─────────────────────────────────────────────────────────────

class RideDistanceCalculateView(APIView):
    """
    POST → (re)calcule la distance réelle d'une course
    depuis ses points GPS.
    Utilisé par l'admin ou en cas de correction.
    """
    permission_classes = [IsAdminUser]

    def post(self, request, ride_id):
        try:
            ride = Ride.objects.get(id=ride_id)
        except Ride.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'detail': "Course introuvable."}},
                status=status.HTTP_404_NOT_FOUND
            )

        points = list(
            GPSPoint.objects.filter(ride=ride)
            .order_by('sequence')
            .values('latitude', 'longitude')
        )

        if len(points) < 2:
            return Response(
                {
                    'success': False,
                    'errors': {
                        'detail': f"Pas assez de points GPS ({len(points)}) pour calculer la distance."
                    }
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Distance GPS réelle (somme des segments)
        gps_distance = calculate_route_distance(points)

        # Distance théorique (vol d'oiseau départ → arrivée)
        theoretical_distance = calculate_haversine_distance(
            float(points[0]['latitude']),
            float(points[0]['longitude']),
            float(points[-1]['latitude']),
            float(points[-1]['longitude']),
        )

        # Écart en pourcentage
        if theoretical_distance > 0:
            deviation = ((gps_distance - theoretical_distance) / theoretical_distance) * 100
        else:
            deviation = 0

        # Mettre à jour la course
        old_distance             = ride.actual_distance_km
        ride.actual_distance_km  = round(gps_distance, 3)
        ride.save(update_fields=['actual_distance_km'])

        logger.info(
            f"Distance recalculée pour course {ride_id} : "
            f"{old_distance} → {gps_distance} km"
        )

        return Response(
            {
                'success': True,
                'data': {
                    'ride_id':               str(ride.id),
                    'total_gps_points':      len(points),
                    'gps_distance_km':       round(gps_distance, 3),
                    'theoretical_distance_km': round(theoretical_distance, 3),
                    'deviation_percent':     round(deviation, 2),
                    'old_distance_km':       str(old_distance),
                    'updated':               True,
                }
            },
            status=status.HTTP_200_OK
        )