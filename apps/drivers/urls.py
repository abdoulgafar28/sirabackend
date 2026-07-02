from django.urls import path
from apps.drivers import views

urlpatterns = [
    # ── Inscription ───────────────────────────────────────
    path('register/',                   views.DriverRegisterView.as_view(),         name='driver-register'),

    # ── Profil ────────────────────────────────────────────
    path('profile/',                    views.DriverProfileView.as_view(),          name='driver-profile'),

    # ── Documents ─────────────────────────────────────────
    path('documents/',                  views.DriverDocumentListView.as_view(),     name='driver-documents'),
    path('documents/<str:document_type>/', views.DriverDocumentDetailView.as_view(),name='driver-document-detail'),

    # ── Disponibilité ─────────────────────────────────────
    path('availability/',               views.DriverAvailabilityView.as_view(),     name='driver-availability'),

    # ── Demandes de course ────────────────────────────────
    path('ride-requests/',              views.DriverRideRequestsView.as_view(),     name='driver-ride-requests'),
    path('ride-requests/<uuid:request_id>/accept/', views.DriverAcceptRideView.as_view(),  name='driver-accept-ride'),
    path('ride-requests/<uuid:request_id>/reject/', views.DriverRejectRideView.as_view(),  name='driver-reject-ride'),

    # ── Course en cours ───────────────────────────────────
    path('rides/current/',              views.DriverCurrentRideView.as_view(),      name='driver-current-ride'),
    path('rides/<uuid:ride_id>/status/',views.DriverUpdateRideStatusView.as_view(), name='driver-update-ride-status'),

    # ── Historique et gains ───────────────────────────────
    path('rides/history/',              views.DriverRideHistoryView.as_view(),      name='driver-ride-history'),
    path('earnings/',                   views.DriverEarningsView.as_view(),         name='driver-earnings'),
]