from django.urls import path
from apps.tracking import views

urlpatterns = [
    # ── Temps réel ────────────────────────────────────────
    path(
        'location/',
        views.DriverLocationUpdateView.as_view(),
        name='driver-location-update'
    ),

    # ── Sync offline (bulk) ───────────────────────────────
    path(
        'sync/gps/',
        views.GPSPointBulkSyncView.as_view(),
        name='gps-bulk-sync'
    ),

    # ── Position conducteur (client) ──────────────────────
    path(
        'rides/<uuid:ride_id>/location/',
        views.DriverCurrentLocationView.as_view(),
        name='driver-current-location'
    ),

    # ── Trajet complet (admin) ────────────────────────────
    path(
        'rides/<uuid:ride_id>/trail/',
        views.RideGPSTrailView.as_view(),
        name='ride-gps-trail'
    ),

    # ── Recalcul distance (admin) ─────────────────────────
    path(
        'rides/<uuid:ride_id>/distance/',
        views.RideDistanceCalculateView.as_view(),
        name='ride-distance-calculate'
    ),
]