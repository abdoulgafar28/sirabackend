from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.admin_panel import views

# ── Router automatique ────────────────────────────────────────
router = DefaultRouter()
router.register('companies',    views.CompanyViewSet,      basename='admin-companies')
router.register('dashboard',    views.DashboardViewSet,    basename='admin-dashboard')
router.register('drivers',      views.DriverAdminViewSet,  basename='admin-drivers')
router.register('rides',        views.RideAdminViewSet,    basename='admin-rides')
router.register('operations',   views.OperationsViewSet,   basename='admin-operations')
router.register('surveillance', views.SurveillanceViewSet, basename='admin-surveillance')
router.register('pricing',      views.PricingViewSet,      basename='admin-pricing')
router.register('fraud',        views.FraudAdminViewSet,   basename='admin-fraud')

urlpatterns = [
    # Auth — hors router (endpoints spéciaux)
    path('auth/login/',    views.AdminLoginView.as_view(),          name='admin-login'),
    path('auth/register/', views.AdminRegisterViewSet.as_view({'post': 'create'}), name='admin-register'),
    path('auth/verify-2fa/', views.AdminVerify2FAView.as_view(), name='admin-verify-2fa'),


    path('auth/forgot-password/',  views.AdminForgotPasswordView.as_view(),  name='admin-forgot-password'),
    path('auth/reset-password/',   views.AdminResetPasswordView.as_view(),   name='admin-reset-password'),

    


    # Toutes les autres routes générées automatiquement
    path('', include(router.urls)),
]