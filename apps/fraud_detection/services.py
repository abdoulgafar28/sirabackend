# apps/fraud_detection/services.py
import logging
from decimal import Decimal

from apps.core.utils import calculate_haversine_distance, calculate_route_distance
from apps.fraud_detection.models import FraudCheck
from apps.fraud_detection.osrm_service import OSRMService
from apps.notifications.services import NotificationService
from apps.tracking.models import GPSPoint

logger = logging.getLogger('apps')

# ─── Seuils de détection ──────────────────────────────────────────────
SEUIL_ECART_ALERTE         = 30.0   # % écart distance → alerte
SEUIL_ECART_AVERTISSEMENT  = 15.0   # % écart distance → avertissement
SEUIL_VITESSE_MAX          = 50.0   # km/h limite Ouagadougou
SEUIL_VITESSE_CRITIQUE     = 80.0   # km/h → alerte immédiate
SEUIL_GPS_GAP_SECONDES     = 60     # secondes sans GPS → gap suspect
SCORE_ECART_DISTANCE       = 30     # points de score
SCORE_VITESSE_EXCESSIVE    = 25     # points de score
SCORE_VITESSE_CRITIQUE     = 40     # points de score
SCORE_GAP_GPS              = 20     # points de score
SCORE_DETOUR               = 15     # points de score


class FraudDetectionService:
    """
    Service d'analyse anti-fraude automatique.
    Lancé automatiquement à la fin de chaque course.
    """

    @staticmethod
    def analyze(ride) -> FraudCheck:
        """
        Point d'entrée principal.
        Analyse complète d'une course et crée le FraudCheck.
        """
        logger.info(f"Analyse anti-fraude course {ride.id}")

        # Récupérer tous les points GPS de la course
        gps_points = list(
            GPSPoint.objects.filter(ride=ride)
            .order_by('sequence')
            .values('latitude', 'longitude', 'speed_kmh', 'recorded_at')
        )

        # ── 1. Calcul distance GPS réelle ─────────────────
        gps_distance = FraudDetectionService._calc_gps_distance(
            gps_points, ride
        )

        # ── 2. Distance théorique via OSRM ────────────────
        theoretical_distance = FraudDetectionService._calc_theoretical_distance(ride)

        # ── 3. Calcul écart ───────────────────────────────
        deviation = FraudDetectionService._calc_deviation(
            gps_distance, theoretical_distance
        )

        # ── 4. Analyse vitesse ────────────────────────────
        speed_data = FraudDetectionService._analyze_speed(gps_points)

        # ── 5. Analyse détour ─────────────────────────────
        detour_data = FraudDetectionService._analyze_detour(
            gps_distance, theoretical_distance
        )

        # ── 6. Détection trous GPS ────────────────────────
        has_gps_gaps = FraudDetectionService._detect_gps_gaps(gps_points)

        # ── 7. Calcul score de fraude ─────────────────────
        fraud_score, incidents = FraudDetectionService._calc_fraud_score(
            deviation        = deviation,
            max_speed        = speed_data['max_speed'],
            speed_violations = speed_data['violations'],
            has_gps_gaps     = has_gps_gaps,
            detour_km        = detour_data['detour_km'],
        )

        # ── 8. Déterminer statut final ────────────────────
        statut, risk_level = FraudDetectionService._determine_statut(
            fraud_score, deviation, speed_data['max_speed']
        )

        # ── 9. Créer ou mettre à jour le FraudCheck ───────
        fraud_check, _ = FraudCheck.objects.update_or_create(
            ride=ride,
            defaults={
                'driver':                   ride.driver,
                'gps_distance_km':          gps_distance,
                'theoretical_distance_km':  theoretical_distance,
                'distance_deviation_percent': deviation,
                'max_speed_kmh':            speed_data['max_speed'],
                'average_speed_kmh':        speed_data['avg_speed'],
                'speed_limit_zone':         SEUIL_VITESSE_MAX,
                'speed_violations_count':   speed_data['violations'],
                'detour_km':                detour_data['detour_km'],
                'detour_justified':         detour_data['justified'],
                'has_gps_gaps':             has_gps_gaps,
                'has_route_deviation':      deviation > SEUIL_ECART_AVERTISSEMENT,
                'has_speed_anomaly':        speed_data['max_speed'] > SEUIL_VITESSE_MAX,
                'has_distance_mismatch':    deviation > SEUIL_ECART_ALERTE,
                'incidents':                incidents,
                'statut':                   statut,
                'risk_level':               risk_level,
                'check_status':             FraudCheck.CheckStatus.PENDING,
                'fraud_score':              fraud_score,
            }
        )

        # ── 10. Notifier l'admin si nécessaire ────────────
        FraudDetectionService._notify_admin(fraud_check)

        logger.info(
            f"FraudCheck créé — Course {ride.id} "
            f"Score: {fraud_score} Statut: {statut}"
        )

        return fraud_check

    # ─────────────────────────────────────────────────────
    # MÉTHODES PRIVÉES
    # ─────────────────────────────────────────────────────

    @staticmethod
    def _calc_gps_distance(gps_points: list, ride) -> float:
        """Calcule la distance réelle depuis les points GPS."""
        if len(gps_points) >= 2:
            return calculate_route_distance(gps_points)

        # Fallback : distance directe si pas assez de points GPS
        if ride.actual_pickup_latitude and ride.actual_dropoff_latitude:
            return calculate_haversine_distance(
                float(ride.actual_pickup_latitude),
                float(ride.actual_pickup_longitude),
                float(ride.actual_dropoff_latitude),
                float(ride.actual_dropoff_longitude),
            )
        return 0.0

    @staticmethod
    def _calc_theoretical_distance(ride) -> float:
        """
        Appelle OSRM pour obtenir la distance théorique par la route.
        Fallback sur Haversine si OSRM échoue.
        """
        if not ride.actual_pickup_latitude:
            return 0.0

        osrm_result = OSRMService.get_route_distance(
            pickup_lat = float(ride.actual_pickup_latitude),
            pickup_lng = float(ride.actual_pickup_longitude),
            dropoff_lat= float(ride.actual_dropoff_latitude),
            dropoff_lng= float(ride.actual_dropoff_longitude),
        )

        if osrm_result['success']:
            return osrm_result['distance_km']

        # Fallback Haversine si OSRM indisponible
        logger.warning(f"OSRM indisponible — fallback Haversine pour course {ride.id}")
        return calculate_haversine_distance(
            float(ride.actual_pickup_latitude),
            float(ride.actual_pickup_longitude),
            float(ride.actual_dropoff_latitude),
            float(ride.actual_dropoff_longitude),
        )

    @staticmethod
    def _calc_deviation(gps_distance: float, theoretical_distance: float) -> float:
        """Calcule l'écart en pourcentage entre GPS et théorique."""
        if not theoretical_distance or theoretical_distance == 0:
            return 0.0
        deviation = ((gps_distance - theoretical_distance) / theoretical_distance) * 100
        return round(max(deviation, 0), 2)

    @staticmethod
    def _analyze_speed(gps_points: list) -> dict:
        """Analyse les données de vitesse sur tous les points GPS."""
        speeds = [
            float(p['speed_kmh'])
            for p in gps_points
            if p['speed_kmh'] is not None
        ]

        if not speeds:
            return {
                'max_speed': 0.0,
                'avg_speed': 0.0,
                'violations': 0,
            }

        violations = sum(1 for s in speeds if s > SEUIL_VITESSE_MAX)

        return {
            'max_speed':  round(max(speeds), 2),
            'avg_speed':  round(sum(speeds) / len(speeds), 2),
            'violations': violations,
        }

    @staticmethod
    def _analyze_detour(gps_distance: float, theoretical_distance: float) -> dict:
        """Calcule le détour en km."""
        detour_km = max(0, round(gps_distance - theoretical_distance, 3))
        # Détour > 20% de la distance théorique → non justifié
        justified = detour_km <= (theoretical_distance * 0.20)

        return {
            'detour_km': detour_km,
            'justified': justified,
        }

    @staticmethod
    def _detect_gps_gaps(gps_points: list) -> bool:
        """
        Détecte les trous dans le tracking GPS.
        Un gap > 60 secondes entre deux points est suspect.
        """
        if len(gps_points) < 2:
            return False

        for i in range(1, len(gps_points)):
            t1 = gps_points[i - 1]['recorded_at']
            t2 = gps_points[i]['recorded_at']
            if t1 and t2:
                delta = (t2 - t1).total_seconds()
                if delta > SEUIL_GPS_GAP_SECONDES:
                    return True
        return False

    @staticmethod
    def _calc_fraud_score(
        deviation: float,
        max_speed: float,
        speed_violations: int,
        has_gps_gaps: bool,
        detour_km: float,
    ) -> tuple:
        """
        Calcule le score de fraude (0-100) et la liste des incidents.
        Plus le score est élevé, plus la fraude est probable.
        """
        score     = 0
        incidents = []

        # ── Écart de distance ─────────────────────────────
        if deviation > SEUIL_ECART_ALERTE:
            score += SCORE_ECART_DISTANCE
            incidents.append(
                f"Distance parcourue anormale (+{deviation:.1f}% vs théorique)"
            )
        elif deviation > SEUIL_ECART_AVERTISSEMENT:
            score += SCORE_ECART_DISTANCE // 2
            incidents.append(
                f"Légère déviation de distance (+{deviation:.1f}% vs théorique)"
            )

        # ── Vitesse excessive ─────────────────────────────
        if max_speed > SEUIL_VITESSE_CRITIQUE:
            score += SCORE_VITESSE_CRITIQUE
            incidents.append(
                f"Vitesse critique ({max_speed:.0f} km/h en zone {SEUIL_VITESSE_MAX:.0f})"
            )
        elif max_speed > SEUIL_VITESSE_MAX:
            score += SCORE_VITESSE_EXCESSIVE
            incidents.append(
                f"Vitesse excessive ({max_speed:.0f} km/h en zone {SEUIL_VITESSE_MAX:.0f})"
            )

        # ── Violations répétées ───────────────────────────
        if speed_violations > 5:
            score += 10
            incidents.append(
                f"Violations répétées de vitesse ({speed_violations} fois)"
            )

        # ── Trous GPS ─────────────────────────────────────
        if has_gps_gaps:
            score += SCORE_GAP_GPS
            incidents.append(
                f"Trous GPS détectés (> {SEUIL_GPS_GAP_SECONDES}s sans signal)"
            )

        # ── Détour non justifié ───────────────────────────
        if detour_km > 2.0:
            score += SCORE_DETOUR
            incidents.append(
                f"Détour injustifié de {detour_km:.1f} km"
            )
        elif detour_km > 1.0:
            score += SCORE_DETOUR // 2
            incidents.append(
                f"Détour partiel non justifié ({detour_km:.1f} km)"
            )

        return min(score, 100), incidents

    @staticmethod
    def _determine_statut(
        fraud_score: int,
        deviation: float,
        max_speed: float,
    ) -> tuple:
        """Détermine le statut et le niveau de risque."""

        # Alerte → score élevé OU vitesse critique OU écart énorme
        if (fraud_score >= 40 or
                max_speed > SEUIL_VITESSE_CRITIQUE or
                deviation > SEUIL_ECART_ALERTE):
            return FraudCheck.Statut.ALERTE, FraudCheck.RiskLevel.HIGH

        # Avertissement → score moyen OU vitesse excessive OU écart modéré
        if (fraud_score >= 20 or
                max_speed > SEUIL_VITESSE_MAX or
                deviation > SEUIL_ECART_AVERTISSEMENT):
            return FraudCheck.Statut.AVERTISSEMENT, FraudCheck.RiskLevel.MEDIUM

        # OK
        return FraudCheck.Statut.OK, FraudCheck.RiskLevel.LOW

    @staticmethod
    def _notify_admin(fraud_check: FraudCheck):
        """Notifie les admins si le statut est alerte ou avertissement."""
        from apps.users.models import User

        if fraud_check.statut == FraudCheck.Statut.OK:
            return

        admins = User.objects.filter(role=User.Role.ADMIN)

        emoji   = "🚨" if fraud_check.statut == FraudCheck.Statut.ALERTE else "⚠️"
        titre   = f"{emoji} Fraude détectée — {fraud_check.statut.upper()}"
        message = (
            f"Course {fraud_check.ride_id} — "
            f"Conducteur : {fraud_check.driver.user.full_name} — "
            f"Score : {fraud_check.fraud_score}/100 — "
            f"Incidents : {len(fraud_check.incidents)}"
        )

        for admin in admins:
            NotificationService.create(
                recipient         = admin,
                notification_type = 'general',
                title             = titre,
                body              = message,
                data = {
                    'fraud_check_id': str(fraud_check.id),
                    'ride_id':        str(fraud_check.ride_id),
                    'driver_id':      str(fraud_check.driver_id),
                    'fraud_score':    fraud_check.fraud_score,
                    'statut':         fraud_check.statut,
                },
            )