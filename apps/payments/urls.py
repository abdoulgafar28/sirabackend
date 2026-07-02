from django.urls import path
from apps.payments import views

urlpatterns = [
    # ── Wallet ────────────────────────────────────────
    path('wallet/',
         views.WalletView.as_view(),
         name='wallet'),

    path('wallet/transactions/',
         views.WalletTransactionHistoryView.as_view(),
         name='wallet-transactions'),

    # ── Payin (recharge via LigdiCash) ───────────────
    path('payin/initiate/',
         views.PayinInitiateView.as_view(),
         name='payin-initiate'),

    path('payin/validate/',
         views.PayinValidateView.as_view(),
         name='payin-validate'),

    # ── Callback LigdiCash (webhook) ──────────────────
    path('ligdicash/callback/',
         views.LigdiCashCallbackView.as_view(),
         name='ligdicash-callback'),

    # ── Paiement course ───────────────────────────────
    path('rides/<uuid:ride_id>/pay/',
         views.RidePaymentView.as_view(),
         name='ride-pay'),

    # ── Payout (retrait via LigdiCash) ────────────────
    path('payout/',
         views.PayoutRequestView.as_view(),
         name='payout-request'),

    path('payout/history/',
         views.PayoutHistoryView.as_view(),
         name='payout-history'),
]