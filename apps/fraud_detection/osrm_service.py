# apps/fraud_detection/osrm_service.py
import logging
import requests

logger = logging.getLogger('apps')

# API OSRM publique — gratuite, sans clé API
OSRM_BASE_URL = "http://router.project-osrm.org/route/v1/driving"
OSRM_TIMEOUT  = 10  # secondes


class OSRMService:
    """
    Service d'appel à l'API OSRM pour calculer
    la distance théorique par la route entre deux points GPS.
    """

    @staticmethod
    def get_route_distance(
        pickup_lat: float, pickup_lng: float,
        dropoff_lat: float, dropoff_lng: float,
    ) -> dict:
        """
        Appelle OSRM et retourne la distance et durée par la route.

        Format URL OSRM :
        /route/v1/driving/{lng1},{lat1};{lng2},{lat2}

        Retourne :
        {
            'distance_km': float,
            'duration_min': float,
            'success': bool,
        }
        """
        try:
            # OSRM attend : longitude,latitude (ordre inversé vs GPS)
            url = (
                f"{OSRM_BASE_URL}/"
                f"{pickup_lng},{pickup_lat};"
                f"{dropoff_lng},{dropoff_lat}"
                f"?overview=false&alternatives=false"
            )

            response = requests.get(url, timeout=OSRM_TIMEOUT)
            response.raise_for_status()
            data = response.json()

            if data.get('code') != 'Ok' or not data.get('routes'):
                logger.warning(f"OSRM réponse invalide : {data.get('code')}")
                return {'distance_km': None, 'duration_min': None, 'success': False}

            route        = data['routes'][0]
            distance_km  = round(route['distance'] / 1000, 3)   # mètres → km
            duration_min = round(route['duration'] / 60, 1)     # secondes → minutes

            logger.info(f"OSRM → {distance_km} km / {duration_min} min")

            return {
                'distance_km':  distance_km,
                'duration_min': duration_min,
                'success':      True,
            }

        except requests.exceptions.Timeout:
            logger.error("OSRM timeout — fallback Haversine")
            return {'distance_km': None, 'duration_min': None, 'success': False}

        except requests.exceptions.RequestException as e:
            logger.error(f"OSRM erreur réseau : {e}")
            return {'distance_km': None, 'duration_min': None, 'success': False}

        except Exception as e:
            logger.error(f"OSRM erreur inattendue : {e}")
            return {'distance_km': None, 'duration_min': None, 'success': False}