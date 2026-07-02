from django.urls import path
from apps.rides import views

urlpatterns = [
    # ── Estimation tarifaire ──────────────────────────────
    path('estimate/',
         views.FareEstimateView.as_view(),
         name='ride-fare-estimate'),

    # ── Conducteurs à proximité ───────────────────────────
    path('nearby-drivers/',
         views.NearbyDriversView.as_view(),
         name='ride-nearby-drivers'),

    # ── Demandes de course ────────────────────────────────
    path('requests/',
         views.RideRequestCreateView.as_view(),
         name='ride-request-create'),

    path('requests/<uuid:request_id>/cancel/',
         views.RideRequestCancelView.as_view(),
         name='ride-request-cancel'),

    # ── Course en cours ───────────────────────────────────
    path('current/',
         views.ClientCurrentRideView.as_view(),
         name='ride-current'),

    path('<uuid:ride_id>/cancel/',
         views.ClientCancelRideView.as_view(),
         name='ride-cancel'),

    # ── Historique ────────────────────────────────────────
    path('history/',
         views.ClientRideHistoryView.as_view(),
         name='ride-history'),

    path('<uuid:ride_id>/',
         views.ClientRideDetailView.as_view(),
         name='ride-detail'),

    # ── Évaluations ───────────────────────────────────────
    path('reviews/',
         views.ReviewCreateView.as_view(),
         name='review-create'),

    path('drivers/<uuid:driver_id>/reviews/',
         views.DriverReviewsListView.as_view(),
         name='driver-reviews'),
]