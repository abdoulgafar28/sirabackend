# apps/tracking/ws_utils.py
import re
import logging
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

logger = logging.getLogger('apps')


def safe_group_name(identifier) -> str:
    """Remplace les caractères invalides pour les noms de groupes Channels."""
    identifier = str(identifier)
    return re.sub(r'[^a-zA-Z0-9_\-\.]', '_', identifier)[:100]


async def notify_ride_status_async(ride_id: str, status: str, message: str):
    try:
        channel_layer = get_channel_layer()
        group_name    = f"ride_{safe_group_name(ride_id)}"
        await channel_layer.group_send(
            group_name,
            {
                'type':    'ride_status',
                'status':  status,
                'message': message,
                'ride_id': ride_id,
            }
        )
        logger.info(f"WS notify ride {ride_id} → {status}")
    except Exception as e:
        logger.error(f"notify_ride_status erreur : {e}")


async def notify_driver_new_ride_async(driver_user_id: str, ride_request_data: dict):
    try:
        channel_layer = get_channel_layer()
        group_name    = f"driver_{safe_group_name(driver_user_id)}"
        await channel_layer.group_send(
            group_name,
            {
                'type':                'ride_request',
                'ride_request_id':     ride_request_data['ride_request_id'],
                'client_name':         ride_request_data['client_name'],
                'pickup_address':      ride_request_data.get('pickup_address', ''),
                'destination_address': ride_request_data.get('destination_address', ''),
                'estimated_price':     ride_request_data['estimated_price'],
                'distance_km':         ride_request_data['distance_km'],
                'service_type':        ride_request_data['service_type'],
                'pickup_latitude':     ride_request_data['pickup_latitude'],
                'pickup_longitude':    ride_request_data['pickup_longitude'],
            }
        )
    except Exception as e:
        logger.error(f"notify_driver_new_ride erreur : {e}")


def notify_driver_new_ride(driver_user_id: str, ride_request_data: dict):
    """Synchrone — Version propre et performante avec async_to_sync"""
    try:
        async_to_sync(notify_driver_new_ride_async)(driver_user_id, ride_request_data)
    except Exception as e:
        logger.error(f"notify_driver_new_ride erreur wrapper sync : {e}")


def notify_ride_status(ride_id: str, status: str, message: str):
    """Synchrone — Version propre et performante avec async_to_sync"""
    try:
        async_to_sync(notify_ride_status_async)(ride_id, status, message)
    except Exception as e:
        logger.error(f"notify_ride_status erreur wrapper sync : {e}")