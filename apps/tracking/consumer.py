# apps/tracking/consumers.py
import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone

logger = logging.getLogger('apps')


def safe_group_name(name: str) -> str:
    """Remplace les tirets par des underscores pour les noms de groupes."""
    return name.replace('-', '_')


class DriverConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        user = self.scope.get('user')

        logger.info(f"WS Driver connect — user: {user}, auth: {getattr(user, 'is_authenticated', False)}")

        if not user or not user.is_authenticated:
            logger.warning("WebSocket Driver : connexion non authentifiée refusée")
            await self.close(code=4001)
            return

        if user.role not in ['driver', 'admin']:
            logger.warning(f"WebSocket Driver : {user.phone_number} n'est ni conducteur ni admin")
            await self.close(code=4003)
            return

        self.user       = user
        self.driver_id  = str(user.id)
        self.group_name = f"driver_{safe_group_name(self.driver_id)}"
        self.is_admin   = (user.role == 'admin')

        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name,
        )

        # Si admin, rejoindre le groupe de surveillance
        if self.is_admin:
            await self.channel_layer.group_add("surveillance_admin", self.channel_name)

        await self.accept()

        await self.send(text_data=json.dumps({
            'type':      'connection_established',
            'message':   'Connexion WebSocket établie.',
            'driver_id': self.driver_id,
            'role':      user.role,
        }))

        # Si admin, envoyer les positions initiales de tous les conducteurs
        if self.is_admin:
            await self.send_initial_surveillance_data()



        logger.info(f"Driver WebSocket connecté : {user.phone_number}")

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name,
            )
            await self.update_driver_offline()

        logger.info(
            f"Driver WebSocket déconnecté : "
            f"{getattr(self, 'driver_id', 'inconnu')} "
            f"(code: {close_code})"
        )

    async def receive(self, text_data):
        try:
            data         = json.loads(text_data)
            message_type = data.get('type')

            if message_type == 'location_update':
                await self.handle_location_update(data)
            elif message_type == 'ride_accepted':
                await self.handle_ride_accepted(data)
            elif message_type == 'ride_rejected':
                await self.handle_ride_rejected(data)
            elif message_type == 'status_update':
                await self.handle_status_update(data)
            else:
                await self.send(text_data=json.dumps({
                    'type':    'error',
                    'message': f"Type inconnu : {message_type}",
                }))

        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                'type':    'error',
                'message': 'Format JSON invalide.',
            }))
        except Exception as e:
            logger.error(f"Driver WebSocket erreur receive : {e}")

    async def handle_location_update(self, data):
        latitude  = data.get('latitude')
        longitude = data.get('longitude')
        speed_kmh = data.get('speed_kmh')
        bearing   = data.get('bearing')
        accuracy  = data.get('accuracy')

        if not latitude or not longitude:
            await self.send(text_data=json.dumps({
                'type':    'error',
                'message': 'latitude et longitude obligatoires.',
            }))
            return

        ride = await self.save_location(latitude, longitude, speed_kmh, bearing, accuracy)

        if ride:
            """await self.channel_layer.group_send(
                f"ride_{safe_group_name(ride['id'])}",
                {
                    'type':      'driver_location',
                    'latitude':  str(latitude),
                    'longitude': str(longitude),
                    'speed_kmh': str(speed_kmh) if speed_kmh else None,
                    'bearing':   str(bearing) if bearing else None,
                    'timestamp': timezone.now().isoformat(),
                }
            )"""

            await self.channel_layer.group_send(
                "surveillance_admin",
                {
                    'type': 'surveillance_update',
                    'data': {
                        'driver_id': self.driver_id,
                        'name':      self.user.full_name,
                        'lat':       float(latitude),
                        'lng':       float(longitude),
                        'status':    'en_course' if ride else 'disponible',
                        'speed':     float(speed_kmh) if speed_kmh else 0,
                    }
                }
            )

        await self.send(text_data=json.dumps({
            'type':        'location_saved',
            'ride_active': ride is not None,
            'timestamp':   timezone.now().isoformat(),
        }))

    async def handle_ride_accepted(self, data):
        request_id = data.get('ride_request_id')
        if not request_id:
            return

        result = await self.accept_ride_request(request_id)

        if result['success']:
            await self.channel_layer.group_send(
                f"ride_{safe_group_name(result['ride_id'])}",
                {
                    'type':        'ride_status',
                    'status':      'accepted',
                    'message':     'Votre course a été acceptée !',
                    'driver_name': self.user.full_name,
                    'driver_phone': self.user.phone_number,
                    'ride_id':     result['ride_id'],
                }
            )
            await self.send(text_data=json.dumps({
                'type':    'ride_accepted_confirmed',
                'ride_id': result['ride_id'],
                'message': 'Course acceptée avec succès.',
            }))
        else:
            await self.send(text_data=json.dumps({
                'type':    'error',
                'message': result.get('error', "Erreur lors de l'acceptation."),
            }))

    async def handle_ride_rejected(self, data):
        request_id = data.get('ride_request_id')
        if request_id:
            await self.reject_ride_request(request_id)
            await self.send(text_data=json.dumps({
                'type':    'ride_rejected_confirmed',
                'message': 'Course refusée.',
            }))

    async def handle_status_update(self, data):
        ride_id    = data.get('ride_id')
        new_status = data.get('status')

        if not ride_id or not new_status:
            return

        result = await self.update_ride_status(ride_id, new_status)

        if result['success']:
            messages = {
                'driver_en_route': 'Votre conducteur est en route 🏍️',
                'started':         'Course démarrée ! 🚀',
                'completed':       'Course terminée ✅',
                'cancelled':       'Course annulée ❌',
            }
            await self.channel_layer.group_send(
                f"ride_{safe_group_name(ride_id)}",
                {
                    'type':    'ride_status',
                    'status':  new_status,
                    'message': messages.get(new_status, f"Statut : {new_status}"),
                    'ride_id': ride_id,
                }
            )
            await self.send(text_data=json.dumps({
                'type':    'status_updated',
                'status':  new_status,
                'message': f"Statut mis à jour : {new_status}",
            }))
        else:
            await self.send(text_data=json.dumps({
                'type':    'error',
                'message': result.get('error'),
            }))

    # ─── Channel layer handlers ────────────────────────────

    async def ride_request(self, event):
        await self.send(text_data=json.dumps({
            'type':                'ride_request',
            'ride_request_id':     event['ride_request_id'],
            'client_name':         event['client_name'],
            'pickup_address':      event.get('pickup_address', ''),
            'destination_address': event.get('destination_address', ''),
            'estimated_price':     event['estimated_price'],
            'distance_km':         event['distance_km'],
            'service_type':        event['service_type'],
            'pickup_latitude':     event['pickup_latitude'],
            'pickup_longitude':    event['pickup_longitude'],
        }))

    async def driver_location(self, event):
        await self.send(text_data=json.dumps(event))

    async def ride_status(self, event):
        await self.send(text_data=json.dumps(event))

    # ─── Méthodes base de données ──────────────────────────

    @database_sync_to_async
    def save_location(self, latitude, longitude, speed_kmh, bearing, accuracy):
        try:
            from apps.drivers.models import DriverProfile
            from apps.rides.models import Ride
            from apps.tracking.models import GPSPoint

            driver = DriverProfile.objects.get(user=self.user)
            driver.current_latitude    = latitude
            driver.current_longitude   = longitude
            driver.location_updated_at = timezone.now()
            driver.save(update_fields=['current_latitude', 'current_longitude', 'location_updated_at'])

            active_ride = Ride.objects.filter(driver=driver, status=Ride.Status.STARTED).first()

            if active_ride:
                last_seq = GPSPoint.objects.filter(
                    ride=active_ride
                ).order_by('-sequence').values_list('sequence', flat=True).first()

                sequence = (last_seq + 1) if last_seq is not None else 0

                GPSPoint.objects.create(
                    ride=active_ride, driver=driver,
                    latitude=latitude, longitude=longitude,
                    speed_kmh=speed_kmh, bearing=bearing, accuracy=accuracy,
                    sequence=sequence, recorded_at=timezone.now(), is_offline=False,
                )
                return {'id': str(active_ride.id)}

            return None

        except Exception as e:
            logger.error(f"save_location erreur : {e}")
            return None

    @database_sync_to_async
    def accept_ride_request(self, request_id):
        try:
            from apps.rides.models import RideRequest, Ride
            from apps.drivers.models import DriverProfile
            from django.db import transaction

            driver = DriverProfile.objects.get(user=self.user)

            if driver.is_on_ride:
                return {'success': False, 'error': 'Vous avez déjà une course.'}

            ride_request = RideRequest.objects.get(id=request_id, status=RideRequest.Status.PENDING)

            if ride_request.expires_at < timezone.now():
                return {'success': False, 'error': 'Demande expirée.'}

            with transaction.atomic():
                ride_request.status = RideRequest.Status.ACCEPTED
                ride_request.driver = driver
                ride_request.save(update_fields=['status', 'driver'])

                ride = Ride.objects.create(
                    request=ride_request, client=ride_request.client,
                    driver=driver, status=Ride.Status.ACCEPTED,
                    base_fare=ride_request.estimated_price,
                )
                driver.is_on_ride = True
                driver.save(update_fields=['is_on_ride'])

            return {'success': True, 'ride_id': str(ride.id)}

        except RideRequest.DoesNotExist:
            return {'success': False, 'error': 'Demande introuvable.'}
        except Exception as e:
            logger.error(f"accept_ride_request erreur : {e}")
            return {'success': False, 'error': str(e)}

    @database_sync_to_async
    def reject_ride_request(self, request_id):
        logger.info(f"Conducteur {self.user.phone_number} refuse la demande {request_id}")

    @database_sync_to_async
    def update_ride_status(self, ride_id, new_status):
        try:
            from apps.rides.models import Ride
            from apps.drivers.models import DriverProfile

            driver = DriverProfile.objects.get(user=self.user)
            ride   = Ride.objects.get(id=ride_id, driver=driver)

            TRANSITIONS = {
                Ride.Status.ACCEPTED:        [Ride.Status.DRIVER_EN_ROUTE, Ride.Status.CANCELLED],
                Ride.Status.DRIVER_EN_ROUTE: [Ride.Status.STARTED,         Ride.Status.CANCELLED],
                Ride.Status.STARTED:         [Ride.Status.COMPLETED],
            }

            allowed = TRANSITIONS.get(ride.status, [])
            if new_status not in allowed:
                return {'success': False, 'error': f"Transition impossible : {ride.status} → {new_status}"}

            now        = timezone.now()
            ride.status = new_status

            if new_status == Ride.Status.STARTED:
                ride.started_at              = now
                ride.actual_pickup_latitude  = driver.current_latitude
                ride.actual_pickup_longitude = driver.current_longitude
            elif new_status == Ride.Status.COMPLETED:
                ride.completed_at             = now
                ride.actual_dropoff_latitude  = driver.current_latitude
                ride.actual_dropoff_longitude = driver.current_longitude
                driver.is_on_ride = False
                driver.save(update_fields=['is_on_ride'])
            elif new_status == Ride.Status.CANCELLED:
                ride.cancelled_at = now
                driver.is_on_ride = False
                driver.save(update_fields=['is_on_ride'])

            ride.save()
            return {'success': True}

        except Exception as e:
            logger.error(f"update_ride_status erreur : {e}")
            return {'success': False, 'error': str(e)}

    @database_sync_to_async
    def update_driver_offline(self):
        try:
            from apps.drivers.models import DriverProfile
            driver = DriverProfile.objects.get(user=self.user)
            if not driver.is_on_ride:
                driver.is_available = False
                driver.save(update_fields=['is_available'])
        except Exception:
            pass



    async def send_initial_surveillance_data(self):
        """Envoie les positions de tous les conducteurs actifs à l'admin."""
        drivers = await self.get_active_drivers()
        stats   = await self.get_surveillance_stats(drivers)

        await self.send(text_data=json.dumps({
            'type':    'initial',
            'drivers': drivers,
            'stats':   stats,
        }))

    @database_sync_to_async
    def get_active_drivers(self):
        """Récupère tous les conducteurs actifs avec leur position."""
        from apps.drivers.models import DriverProfile
        from apps.fraud_detection.models import FraudCheck
        from apps.rides.models import Ride

        drivers = DriverProfile.objects.filter(
            validation_status='approved',
            user__status='active',
            current_latitude__isnull=False,
            current_longitude__isnull=False,
        ).select_related('user')

        fraud_ids = set(FraudCheck.objects.filter(
            statut='alerte', check_status='pending'
        ).values_list('driver_id', flat=True))

        result = []
        for d in drivers:
            status = 'alerte' if d.id in fraud_ids else ('en_course' if d.is_on_ride else 'disponible')
            trip_id = None
            if d.is_on_ride:
                ride = Ride.objects.filter(
                    driver=d, status__in=['accepted', 'driver_en_route', 'started']
                ).first()
                trip_id = str(ride.id)[:8].upper() if ride else None

            result.append({
                'id':      str(d.id),
                'name':    d.user.full_name,
                'phone':   d.user.phone_number,
                'lat':     float(d.current_latitude),
                'lng':     float(d.current_longitude),
                'status':  status,
                'trip_id': trip_id,
                'speed':   0,
            })

        return result

    @database_sync_to_async
    def get_surveillance_stats(self, drivers):
        """Calcule les statistiques."""
        return {
            'total_actifs': len(drivers),
            'en_course':    sum(1 for d in drivers if d['status'] == 'en_course'),
            'disponibles':  sum(1 for d in drivers if d['status'] == 'disponible'),
            'alertes':      sum(1 for d in drivers if d['status'] == 'alerte'),
        }


    async def surveillance_update(self, event):
        """Reçoit une mise à jour de position et la transmet à l'admin."""
        if self.is_admin:
            await self.send(text_data=json.dumps({
                'type': 'location_update',
                'data': event['data'],
            }))











class RideConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        user    = self.scope.get('user')
        ride_id = self.scope['url_route']['kwargs']['ride_id']

        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        is_authorized = await self.check_ride_authorization(user, ride_id)
        if not is_authorized:
            logger.warning(f"WebSocket Ride : {user.phone_number} non autorisé pour {ride_id}")
            await self.close(code=4003)
            return

        self.user       = user
        self.ride_id    = ride_id
        self.group_name = f"ride_{safe_group_name(ride_id)}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        ride_data = await self.get_ride_data(ride_id)

        await self.send(text_data=json.dumps({
            'type':      'connection_established',
            'message':   'Connexion établie. Suivi en temps réel actif.',
            'ride_data': ride_data,
        }))

        logger.info(f"Ride WebSocket connecté : {user.phone_number} → course {ride_id}")

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        logger.info(f"Ride WebSocket déconnecté : {getattr(self, 'ride_id', 'inconnu')} (code: {close_code})")

    async def receive(self, text_data):
        try:
            data         = json.loads(text_data)
            message_type = data.get('type')

            if message_type == 'ping':
                await self.send(text_data=json.dumps({'type': 'pong'}))
            elif message_type == 'cancel_ride':
                await self.handle_cancel_ride(data)

        except json.JSONDecodeError:
            pass

    async def handle_cancel_ride(self, data):
        reason = data.get('reason', 'Annulé par le client')
        result = await self.cancel_ride(self.ride_id, reason)

        if result['success']:
            await self.channel_layer.group_send(
                f"driver_{safe_group_name(result['driver_id'])}",
                {
                    'type':    'ride_status',
                    'status':  'cancelled',
                    'message': f"Course annulée par le client. Raison : {reason}",
                    'ride_id': self.ride_id,
                }
            )
            await self.send(text_data=json.dumps({
                'type':    'ride_cancelled',
                'message': 'Course annulée.',
            }))

    async def driver_location(self, event):
        await self.send(text_data=json.dumps({
            'type':      'driver_location',
            'latitude':  event['latitude'],
            'longitude': event['longitude'],
            'speed_kmh': event.get('speed_kmh'),
            'bearing':   event.get('bearing'),
            'timestamp': event.get('timestamp'),
        }))

    async def ride_status(self, event):
        await self.send(text_data=json.dumps({
            'type':    'ride_status',
            'status':  event['status'],
            'message': event.get('message', ''),
            'ride_id': event.get('ride_id'),
        }))

    @database_sync_to_async
    def check_ride_authorization(self, user, ride_id):
        try:
            from apps.rides.models import Ride
            ride = Ride.objects.get(id=ride_id)
            return (
                ride.client == user or
                (hasattr(user, 'driver_profile') and ride.driver == user.driver_profile) or
                user.role == 'admin'
            )
        except Exception:
            return False

    @database_sync_to_async
    def get_ride_data(self, ride_id):
        try:
            from apps.rides.models import Ride
            ride   = Ride.objects.select_related('driver__user', 'client').get(id=ride_id)
            driver = ride.driver
            return {
                'ride_id': str(ride.id),
                'status':  ride.status,
                'driver': {
                    'name':      driver.user.full_name,
                    'phone':     driver.user.phone_number,
                    'latitude':  str(driver.current_latitude) if driver.current_latitude else None,
                    'longitude': str(driver.current_longitude) if driver.current_longitude else None,
                },
                'total_fare': str(ride.total_fare) if ride.total_fare else None,
            }
        except Exception as e:
            logger.error(f"get_ride_data erreur : {e}")
            return {}

    @database_sync_to_async
    def cancel_ride(self, ride_id, reason):
        try:
            from apps.rides.models import Ride, RideRequest
            ride = Ride.objects.get(
                id=ride_id, client=self.user,
                status__in=[Ride.Status.ACCEPTED, Ride.Status.DRIVER_EN_ROUTE],
            )
            driver_id                = str(ride.driver.user.id)
            ride.status              = Ride.Status.CANCELLED
            ride.cancelled_at        = timezone.now()
            ride.cancellation_reason = reason
            ride.save()

            ride.driver.is_on_ride = False
            ride.driver.save(update_fields=['is_on_ride'])

            ride.request.status       = RideRequest.Status.CANCELLED
            ride.request.cancelled_by = RideRequest.CancelledBy.CLIENT
            ride.request.save(update_fields=['status', 'cancelled_by'])

            return {'success': True, 'driver_id': driver_id}
        except Exception as e:
            return {'success': False, 'error': str(e)}
        