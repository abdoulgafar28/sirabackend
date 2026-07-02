from django.urls import path
from apps.fraud_detection import views

urlpatterns = [
    # Liste tous les contrôles
    path('',
         views.FraudCheckListView.as_view(),
         name='fraud-list'),

    # Détail d'un contrôle
    path('<uuid:fraud_id>/',
         views.FraudCheckDetailView.as_view(),
         name='fraud-detail'),

    # Résoudre un contrôle
    path('<uuid:fraud_id>/resolve/',
         views.FraudCheckResolveView.as_view(),
         name='fraud-resolve'),

    # Déclencher manuellement
    path('trigger/<uuid:ride_id>/',
         views.FraudCheckTriggerView.as_view(),
         name='fraud-trigger'),
]