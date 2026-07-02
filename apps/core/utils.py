import math
from decimal import Decimal
from rest_framework_simplejwt.tokens import RefreshToken


def calculate_haversine_distance(lat1: float, lon1: float,
                                  lat2: float, lon2: float) -> float:
    """
    Calcule la distance en km entre deux points GPS.
    Formule de Haversine — précision suffisante pour des distances < 500 km.
    """
    R = 6371  # Rayon terrestre en km

    phi1    = math.radians(lat1)
    phi2    = math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (math.sin(dphi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def calculate_route_distance(gps_points: list) -> float:
    """
    Calcule la distance totale d'un trajet à partir d'une liste de points GPS.
    Chaque point est un dict avec 'latitude' et 'longitude'.
    """
    total = 0.0
    for i in range(1, len(gps_points)):
        total += calculate_haversine_distance(
            float(gps_points[i - 1]['latitude']),
            float(gps_points[i - 1]['longitude']),
            float(gps_points[i]['latitude']),
            float(gps_points[i]['longitude']),
        )
    return round(total, 3)


def estimate_fare(distance_km: float, service_type: str, pricing) -> Decimal:
    """
    Calcule le tarif estimé selon la distance et le type de service.
    """
    if not pricing:
        return Decimal('0')

    if service_type == 'delivery':
        base      = pricing.delivery_base_fare
        per_km    = pricing.delivery_price_per_km
    else:
        base      = pricing.base_fare
        per_km    = pricing.price_per_km

    fare = base + (Decimal(str(distance_km)) * per_km)
    fare = fare * pricing.surge_multiplier

    # Appliquer minimum et maximum
    fare = max(fare, pricing.minimum_fare)
    if pricing.maximum_fare:
        fare = min(fare, pricing.maximum_fare)

    return round(fare, 2)


def find_nearby_drivers(client_lat: float, client_lon: float,
                         radius_km: float = 5.0) -> list:
    """
    Retourne les conducteurs disponibles dans un rayon donné.
    Filtre d'abord en base, puis calcule la distance précise.
    """
    from apps.drivers.models import DriverProfile

    # Filtre approximatif en base (1° ≈ 111 km)
    delta = radius_km / 111.0

    candidates = DriverProfile.objects.filter(
        is_available=True,
        is_on_ride=False,
        validation_status=DriverProfile.ValidationStatus.APPROVED,
        current_latitude__range=(client_lat - delta, client_lat + delta),
        current_longitude__range=(client_lon - delta, client_lon + delta),
        user__status='active',
    ).select_related('user', 'vehicle')

    # Calcul précis de la distance pour chaque candidat
    nearby = []
    for driver in candidates:
        dist = calculate_haversine_distance(
            client_lat, client_lon,
            float(driver.current_latitude),
            float(driver.current_longitude),
        )
        if dist <= radius_km:
            nearby.append({
                'driver': driver,
                'distance_km': round(dist, 2),
            })

    # Trier par distance croissante
    nearby.sort(key=lambda x: x['distance_km'])
    return nearby



def generate_admin_tokens(admin):
        """
        Génère access + refresh tokens pour un admin.
        """
        refresh = RefreshToken.for_user(admin)
        # Tu peux ajouter des claims personnalisés si besoin
        refresh['admin_id'] = str(admin.id)
        refresh['company_id'] = str(admin.company_id)
        refresh['role'] = admin.role

        return {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }

def decode_admin_token(token):
        """
        Décode un refresh token et retourne son payload.
        """
        try:
            refresh = RefreshToken(token)
            return refresh.payload
        except Exception as e:
            raise e