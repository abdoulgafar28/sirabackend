from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from apps.users import views

urlpatterns = [
    # ── Inscription ──────────────────────────────────────
    path('register/',           views.RegisterView.as_view(),      name='auth-register'),

    # ── OTP ──────────────────────────────────────────────
    path('otp/send/',           views.OTPSendView.as_view(),       name='auth-otp-send'),
    path('otp/verify/',         views.OTPVerifyView.as_view(),     name='auth-otp-verify'),

    # ── Connexion / Déconnexion ───────────────────────────
    path('login/',              views.LoginView.as_view(),         name='auth-login'),
    path('logout/',             views.LogoutView.as_view(),        name='auth-logout'),

    # ── Token refresh (SimpleJWT natif) ──────────────────
    path('token/refresh/',      TokenRefreshView.as_view(),        name='auth-token-refresh'),

    # ── Profil connecté ───────────────────────────────────
    path('me/',                 views.MeView.as_view(),            name='auth-me'),
    path('me/password/',        views.ChangePasswordView.as_view(),name='auth-change-password'),
]