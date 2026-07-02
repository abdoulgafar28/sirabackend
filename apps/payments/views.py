import logging
import re
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permission import IsClient, IsDriver, IsAdminUser
from apps.notifications.services import NotificationService
from apps.payments.ligdi_cash_service import LigdiCashService
from apps.payments.models import (
    SiraWallet, WalletTransaction,
    LigdiCashPayin, LigdiCashPayout,
    MONTANT_MIN_RETRAIT, DELAI_PAIEMENT_MINUTES,
    COMMISSION_RETRAIT_PERCENT,
)
from apps.payments.wallet_service import WalletService
from apps.rides.models import Ride

logger = logging.getLogger('apps')


def format_phone(phone: str) -> str:
    """
    Formate le numéro avec l'indicatif Burkina Faso.
    Ex: 70123456 → 22670123456
    """
    phone = phone.replace(' ', '').replace('+', '')
    if phone.startswith('226'):
        return phone
    return f"226{phone}"


# ─────────────────────────────────────────────────────────
# 1. WALLET — SOLDE ET HISTORIQUE
# ─────────────────────────────────────────────────────────

class WalletView(APIView):
    """GET → solde et infos du Wallet SIRA."""

    def get(self, request):
        wallet = WalletService.create_wallet(request.user)
        return Response(
            {
                'success': True,
                'data': {
                    'id':             str(wallet.id),
                    'balance':        str(wallet.balance),
                    'currency':       'XOF',
                    'status':         wallet.status,
                    'total_credited': str(wallet.total_credited),
                    'total_debited':  str(wallet.total_debited),
                    'updated_at':     wallet.updated_at,
                }
            },
            status=status.HTTP_200_OK
        )


class WalletTransactionHistoryView(APIView):
    """GET → historique des transactions."""

    def get(self, request):
        try:
            wallet = SiraWallet.objects.get(user=request.user)
        except SiraWallet.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'detail': "Wallet introuvable."}},
                status=status.HTTP_404_NOT_FOUND
            )

        transactions = WalletTransaction.objects.filter(
            wallet=wallet
        ).order_by('-created_at')

        # Filtres
        tx_type   = request.query_params.get('type')
        date_from = request.query_params.get('date_from')
        date_to   = request.query_params.get('date_to')

        if tx_type:
            transactions = transactions.filter(transaction_type=tx_type)
        if date_from:
            transactions = transactions.filter(created_at__date__gte=date_from)
        if date_to:
            transactions = transactions.filter(created_at__date__lte=date_to)

        # Pagination
        page      = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        start     = (page - 1) * page_size
        end       = start + page_size
        total     = transactions.count()

        data = []
        for tx in transactions[start:end]:
            data.append({
                'id':               str(tx.id),
                'type':             tx.transaction_type,
                'type_label':       tx.get_transaction_type_display(),
                'direction':        tx.direction,
                'amount':           str(tx.amount),
                'balance_before':   str(tx.balance_before),
                'balance_after':    str(tx.balance_after),
                'description':      tx.description,
                'status':           tx.status,
                'created_at':       tx.created_at,
            })

        return Response(
            {
                'success':     True,
                'balance':     str(wallet.balance),
                'count':       total,
                'page':        page,
                'total_pages': (total + page_size - 1) // page_size,
                'data':        data,
            },
            status=status.HTTP_200_OK
        )


# ─────────────────────────────────────────────────────────
# 2. PAYIN — RECHARGE WALLET VIA LIGDICASH
# ─────────────────────────────────────────────────────────

class PayinInitiateView(APIView):
    """
    POST → Client initie une recharge de son Wallet SIRA.

    LigdiCash envoie un OTP par SMS au client.
    Le client n'a pas besoin de taper *144# lui-même.
    """

    def post(self, request):
        phone  = request.data.get('phone_number', '').strip()
        amount = request.data.get('amount')

        # Validations
        if not phone:
            return Response(
                {'success': False, 'errors': {'phone_number': "Numéro obligatoire."}},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not re.match(r'^(\+226|00226|226)?[0-9]{8}$', phone.replace(' ', '')):
            return Response(
                {'success': False, 'errors': {'phone_number': "Numéro invalide."}},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            amount = int(amount)
            if amount < 100:
                raise ValueError
            if amount > 500000:
                raise ValueError
        except (TypeError, ValueError):
            return Response(
                {
                    'success': False,
                    'errors': {'amount': "Montant invalide. Min: 100 FCFA, Max: 500 000 FCFA."}
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Vérifier qu'il n'y a pas de payin en attente
        pending = LigdiCashPayin.objects.filter(
            user=request.user,
            status__in=[
                LigdiCashPayin.Status.OTP_SENT,
                LigdiCashPayin.Status.PENDING,
            ],
            expires_at__gt=timezone.now(),
        ).first()

        if pending:
            return Response(
                {
                    'success': False,
                    'errors': {
                        'detail': (
                            f"Vous avez déjà une recharge en cours "
                            f"(ID: {pending.id}). "
                            f"Entrez l'OTP reçu ou attendez son expiration."
                        )
                    }
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        phone_formatted = format_phone(phone)

        # Appeler LigdiCash → envoyer OTP
        result = LigdiCashService.send_otp(phone_formatted, amount)

        if not result['success']:
            return Response(
                {
                    'success': False,
                    'errors': {'detail': f"Erreur LigdiCash : {result.get('error')}"}
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Créer le payin en base
        wallet = WalletService.create_wallet(request.user)
        payin  = LigdiCashPayin.objects.create(
            wallet       = wallet,
            user         = request.user,
            phone_number = phone_formatted,
            amount       = Decimal(amount),
            status       = LigdiCashPayin.Status.OTP_SENT,
            expires_at   = timezone.now() + timedelta(minutes=10),
        )

        logger.info(
            f"Payin initié — {request.user.phone_number} "
            f"→ {amount} FCFA — payin_id: {payin.id}"
        )

        return Response(
            {
                'success': True,
                'message': (
                    f"Un code OTP a été envoyé par SMS au {phone}. "
                    f"Entrez-le dans l'application pour valider votre recharge."
                ),
                'data': {
                    'payin_id':   str(payin.id),
                    'amount':     amount,
                    'currency':   'XOF',
                    'expires_in': "10 minutes",
                }
            },
            status=status.HTTP_200_OK
        )


class PayinValidateView(APIView):
    """
    POST → Client soumet l'OTP reçu par SMS.
    LigdiCash valide et débite son Mobile Money.
    Wallet SIRA crédité automatiquement si succès.
    """

    def post(self, request):
        payin_id = request.data.get('payin_id')
        otp      = request.data.get('otp', '').strip()

        if not payin_id or not otp:
            return Response(
                {'success': False, 'errors': {'detail': "payin_id et OTP obligatoires."}},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Récupérer le payin
        try:
            payin = LigdiCashPayin.objects.get(
                id=payin_id,
                user=request.user,
                status__in=[
                    LigdiCashPayin.Status.OTP_SENT,
                    LigdiCashPayin.Status.PENDING,
                ],
            )
        except LigdiCashPayin.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'detail': "Recharge introuvable ou déjà traitée."}},
                status=status.HTTP_404_NOT_FOUND
            )

        # Vérifier expiration
        if payin.expires_at < timezone.now():
            payin.status = LigdiCashPayin.Status.EXPIRED
            payin.save(update_fields=['status'])
            return Response(
                {
                    'success': False,
                    'errors': {'detail': "OTP expiré. Recommencez une nouvelle recharge."}
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Valider OTP auprès de LigdiCash
        result = LigdiCashService.validate_otp(
            phone_number       = payin.phone_number,
            otp                = otp,
            amount             = int(payin.amount),
            payin_id           = str(payin.id),
            customer_firstname = request.user.first_name,
            customer_lastname  = request.user.last_name,
        )

        if not result['success']:
            payin.status = LigdiCashPayin.Status.PENDING
            payin.save(update_fields=['status'])
            return Response(
                {
                    'success': False,
                    'errors': {'detail': result.get('error', 'OTP invalide. Réessayez.')}
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Sauvegarder le token
        invoice_token = result['invoice_token']
        payin.invoice_token = invoice_token
        payin.status        = LigdiCashPayin.Status.PENDING
        payin.save(update_fields=['invoice_token', 'status'])

        # Vérifier le statut final
        status_result = LigdiCashService.check_payin_status(invoice_token)

        if (status_result['success'] and
                status_result.get('status') == 'completed'):

            # ✅ Créditer le Wallet SIRA
            WalletService.credit_wallet(
                wallet           = payin.wallet,
                amount           = payin.amount,
                transaction_type = WalletTransaction.Type.DEPOT,
                description      = (
                    f"Recharge via {status_result.get('operator_name', 'Mobile Money')} "
                    f"— ID: {status_result.get('transaction_id')}"
                ),
                metadata={
                    'payin_id':       str(payin.id),
                    'operator':       status_result.get('operator_name'),
                    'transaction_id': status_result.get('transaction_id'),
                    'phone':          payin.phone_number,
                },
            )

            # Mettre à jour le payin
            payin.status                 = LigdiCashPayin.Status.COMPLETED
            payin.operator_name          = status_result.get('operator_name')
            payin.ligdicash_transaction_id = status_result.get('transaction_id')
            payin.wallet_credited        = True
            payin.save()

            # Notifier le client
            NotificationService.create(
                recipient         = request.user,
                notification_type = 'payment_success',
                title             = "Recharge réussie ✅",
                body              = (
                    f"{payin.amount} FCFA ajoutés à votre Wallet SIRA. "
                    f"Nouveau solde : {payin.wallet.balance} FCFA."
                ),
                data={'new_balance': str(payin.wallet.balance)},
            )

            logger.info(
                f"Wallet crédité — {request.user.phone_number} "
                f"+{payin.amount} FCFA"
            )

            return Response(
                {
                    'success': True,
                    'message': f"{payin.amount} FCFA ajoutés à votre Wallet SIRA !",
                    'data': {
                        'amount':      str(payin.amount),
                        'new_balance': str(payin.wallet.balance),
                        'operator':    status_result.get('operator_name'),
                    }
                },
                status=status.HTTP_200_OK
            )

        # Statut pending → callback le traitera
        return Response(
            {
                'success': True,
                'message': "Transaction en cours de traitement. Votre wallet sera crédité sous peu.",
                'data': {
                    'payin_id': str(payin.id),
                    'status':   'pending',
                }
            },
            status=status.HTTP_200_OK
        )


class LigdiCashCallbackView(APIView):
    """
    POST → Webhook LigdiCash.
    LigdiCash notifie notre backend automatiquement
    quand une transaction est confirmée.
    Sécurité supplémentaire en plus de la vérification directe.
    """
    permission_classes = [AllowAny]  # appelé par LigdiCash

    def post(self, request):
        data           = request.data
        response_code  = data.get('response_code')
        status_val     = data.get('status')
        custom_data    = data.get('custom_data', [])
        transaction_id = data.get('transaction_id')
        operator_name  = data.get('operator_name')
        amount         = data.get('amount')

        logger.info(f"LigdiCash Callback reçu : {data}")

        # Extraire notre transaction_id depuis custom_data
        our_id = None
        if isinstance(custom_data, list):
            for item in custom_data:
                if item.get('keyof_customdata') == 'transaction_id':
                    our_id = item.get('valueof_customdata')
                    break
        elif isinstance(custom_data, dict):
            our_id = custom_data.get('transaction_id')

        if not our_id:
            return Response({'status': 'ignored'}, status=status.HTTP_200_OK)

        # ── Traiter un Payin ──────────────────────────
        payin = LigdiCashPayin.objects.filter(
            id=our_id,
            wallet_credited=False,
        ).first()

        if payin and response_code == '00' and status_val == 'completed':
            payin.callback_received        = True
            payin.callback_data            = data
            payin.operator_name            = operator_name
            payin.ligdicash_transaction_id = transaction_id

            if not payin.wallet_credited:
                WalletService.credit_wallet(
                    wallet           = payin.wallet,
                    amount           = payin.amount,
                    transaction_type = WalletTransaction.Type.DEPOT,
                    description      = (
                        f"Recharge {operator_name} "
                        f"— Callback ID: {transaction_id}"
                    ),
                    metadata={'source': 'callback', 'transaction_id': transaction_id},
                )
                payin.status         = LigdiCashPayin.Status.COMPLETED
                payin.wallet_credited = True

                NotificationService.create(
                    recipient         = payin.user,
                    notification_type = 'payment_success',
                    title             = "Recharge confirmée ✅",
                    body              = (
                        f"{payin.amount} FCFA ajoutés à votre Wallet SIRA."
                    ),
                )

            payin.save()

        # ── Traiter un Payout ─────────────────────────
        payout = LigdiCashPayout.objects.filter(id=our_id).first()

        if payout:
            payout.callback_received        = True
            payout.callback_data            = data
            payout.operator_name            = operator_name
            payout.ligdicash_transaction_id = transaction_id

            if response_code == '00' and status_val == 'completed':
                payout.status = LigdiCashPayout.Status.COMPLETED

                NotificationService.create(
                    recipient         = payout.driver.user,
                    notification_type = 'payment_success',
                    title             = "Retrait effectué ✅",
                    body              = (
                        f"{payout.amount_to_receive} FCFA envoyés "
                        f"sur votre {operator_name}."
                    ),
                )
            else:
                payout.status = LigdiCashPayout.Status.FAILED
                # Rembourser le wallet conducteur
                WalletService.credit_wallet(
                    wallet           = payout.wallet,
                    amount           = payout.amount_requested,
                    transaction_type = WalletTransaction.Type.REMBOURSEMENT,
                    description      = f"Remboursement retrait échoué #{payout.id}",
                )

            payout.save()

        return Response({'status': 'ok'}, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────
# 3. PAIEMENT COURSE (interne entre wallets)
# ─────────────────────────────────────────────────────────

class RidePaymentView(APIView):
    """POST → client paie une course depuis son Wallet SIRA."""
    permission_classes = [IsClient]

    def post(self, request, ride_id):
        try:
            ride = Ride.objects.get(id=ride_id, client=request.user)
        except Ride.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'detail': "Course introuvable."}},
                status=status.HTTP_404_NOT_FOUND
            )

        if ride.is_paid:
            return Response(
                {'success': False, 'errors': {'detail': "Course déjà payée."}},
                status=status.HTTP_400_BAD_REQUEST
            )

        if ride.status not in [
            Ride.Status.COMPLETED,
            Ride.Status.ACCEPTED,
            Ride.Status.DRIVER_EN_ROUTE,
        ]:
            return Response(
                {
                    'success': False,
                    'errors': {
                        'detail': f"Impossible de payer une course '{ride.status}'."
                    }
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Vérifier délai (30 min après course terminée)
        if ride.status == Ride.Status.COMPLETED and ride.completed_at:
            deadline = ride.completed_at + timedelta(minutes=DELAI_PAIEMENT_MINUTES)
            if timezone.now() > deadline:
                return Response(
                    {
                        'success': False,
                        'errors': {
                            'detail': (
                                f"Délai de paiement dépassé "
                                f"({DELAI_PAIEMENT_MINUTES} min). "
                                f"Contactez le support SIRA."
                            )
                        }
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Vérifier solde wallet
        try:
            client_wallet = SiraWallet.objects.get(user=request.user)
        except SiraWallet.DoesNotExist:
            return Response(
                {
                    'success': False,
                    'errors': {'detail': "Wallet introuvable. Rechargez d'abord votre compte."}
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        fare = ride.total_fare or ride.request.estimated_price

        if client_wallet.balance < fare:
            return Response(
                {
                    'success': False,
                    'errors': {
                        'detail': (
                            f"Solde insuffisant. "
                            f"Disponible : {client_wallet.balance} FCFA, "
                            f"Requis : {fare} FCFA. "
                            f"Rechargez votre Wallet SIRA."
                        )
                    }
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            result = WalletService.process_ride_payment(ride)
        except ValueError as e:
            return Response(
                {'success': False, 'errors': {'detail': str(e)}},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Notifier conducteur
        NotificationService.create(
            recipient         = ride.driver.user,
            notification_type = 'payment_success',
            title             = "Paiement reçu 💰",
            body              = (
                f"{result['driver_amount']} FCFA crédités "
                f"sur votre Wallet SIRA."
            ),
            data={'ride_id': str(ride.id)},
        )

        return Response(
            {
                'success': True,
                'message': "Paiement effectué avec succès.",
                'data':    result,
            },
            status=status.HTTP_200_OK
        )


# ─────────────────────────────────────────────────────────
# 4. PAYOUT — RETRAIT CONDUCTEUR VIA LIGDICASH
# ─────────────────────────────────────────────────────────

class PayoutRequestView(APIView):
    """
    POST → Conducteur demande un retrait
    vers son Mobile Money via LigdiCash.
    Automatique — zéro intervention admin.
    """
    permission_classes = [IsDriver]

    def post(self, request):
        phone  = request.data.get('phone_number', '').strip()
        amount = request.data.get('amount')

        # Validations
        if not phone:
            return Response(
                {'success': False, 'errors': {'phone_number': "Numéro Mobile Money obligatoire."}},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            amount = Decimal(str(amount))
            if amount < MONTANT_MIN_RETRAIT:
                raise ValueError
        except (TypeError, ValueError):
            return Response(
                {
                    'success': False,
                    'errors': {
                        'amount': f"Montant invalide. Minimum : {MONTANT_MIN_RETRAIT} FCFA."
                    }
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Vérifier solde wallet conducteur
        try:
            wallet = SiraWallet.objects.get(user=request.user)
        except SiraWallet.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'detail': "Wallet introuvable."}},
                status=status.HTTP_404_NOT_FOUND
            )

        if wallet.balance < amount:
            return Response(
                {
                    'success': False,
                    'errors': {
                        'detail': (
                            f"Solde insuffisant. "
                            f"Disponible : {wallet.balance} FCFA, "
                            f"Demandé : {amount} FCFA."
                        )
                    }
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Calcul commission retrait (1%)
        commission    = round(amount * Decimal(COMMISSION_RETRAIT_PERCENT) / 100, 2)
        amount_to_receive = amount - commission
        phone_formatted   = format_phone(phone)

        # Créer le payout en base
        payout = LigdiCashPayout.objects.create(
            wallet            = wallet,
            driver            = request.user.driver_profile,
            amount_requested  = amount,
            commission_amount = commission,
            amount_to_receive = amount_to_receive,
            recipient_phone   = phone_formatted,
            status            = LigdiCashPayout.Status.PENDING,
        )

        # Débiter le wallet immédiatement
        try:
            WalletService.debit_wallet(
                wallet           = wallet,
                amount           = amount,
                transaction_type = WalletTransaction.Type.RETRAIT,
                description      = f"Retrait vers Mobile Money {phone}",
                metadata={
                    'payout_id':  str(payout.id),
                    'phone':      phone_formatted,
                    'commission': str(commission),
                },
            )
        except ValueError as e:
            payout.delete()
            return Response(
                {'success': False, 'errors': {'detail': str(e)}},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Appeler LigdiCash Payout
        result = LigdiCashService.create_payout(
            phone_number = phone_formatted,
            amount       = int(amount_to_receive),
            payout_id    = str(payout.id),
            description  = f"Retrait Wallet SIRA — {request.user.full_name}",
        )

        if result['success']:
            payout.withdrawal_token = result['withdrawal_token']
            payout.save(update_fields=['withdrawal_token'])

            # Vérifier statut immédiatement
            status_result = LigdiCashService.check_payout_status(
                result['withdrawal_token']
            )

            if (status_result['success'] and
                    status_result.get('status') == 'completed'):

                payout.status                   = LigdiCashPayout.Status.COMPLETED
                payout.operator_name            = status_result.get('operator_name')
                payout.ligdicash_transaction_id = status_result.get('transaction_id')
                payout.save()

                # Créditer commission SIRA
                from apps.users.models import User
                sira_admin  = User.objects.filter(role=User.Role.ADMIN).first()
                sira_wallet = SiraWallet.objects.get(user=sira_admin)
                WalletService.credit_wallet(
                    wallet           = sira_wallet,
                    amount           = commission,
                    transaction_type = WalletTransaction.Type.COMMISSION_RETRAIT,
                    description      = f"Commission retrait {payout.id} (1%)",
                )

                return Response(
                    {
                        'success': True,
                        'message': (
                            f"{amount_to_receive} FCFA envoyés sur votre "
                            f"Mobile Money ({phone}). "
                            f"Commission prélevée : {commission} FCFA."
                        ),
                        'data': {
                            'amount_sent':    str(amount_to_receive),
                            'commission':     str(commission),
                            'operator':       status_result.get('operator_name'),
                            'new_balance':    str(wallet.balance),
                        }
                    },
                    status=status.HTTP_200_OK
                )

            # En attente → le callback confirmera
            return Response(
                {
                    'success': True,
                    'message': (
                        f"Retrait de {amount_to_receive} FCFA en cours. "
                        f"Vous recevrez une notification dès confirmation."
                    ),
                    'data': {
                        'payout_id':      str(payout.id),
                        'amount_to_receive': str(amount_to_receive),
                        'new_balance':    str(wallet.balance),
                    }
                },
                status=status.HTTP_200_OK
            )

        else:
            # Échec LigdiCash → rembourser le wallet
            payout.status         = LigdiCashPayout.Status.FAILED
            payout.failure_reason = result.get('error')
            payout.save()

            WalletService.credit_wallet(
                wallet           = wallet,
                amount           = amount,
                transaction_type = WalletTransaction.Type.REMBOURSEMENT,
                description      = f"Remboursement retrait échoué #{payout.id}",
            )

            return Response(
                {
                    'success': False,
                    'errors': {
                        'detail': (
                            f"Échec du retrait : {result.get('error')}. "
                            f"Votre solde a été restauré."
                        )
                    }
                },
                status=status.HTTP_400_BAD_REQUEST
            )


class PayoutHistoryView(APIView):
    """GET → historique des retraits du conducteur."""
    permission_classes = [IsDriver]

    def get(self, request):
        payouts = LigdiCashPayout.objects.filter(
            driver=request.user.driver_profile
        ).order_by('-created_at')

        data = []
        for p in payouts:
            data.append({
                'id':               str(p.id),
                'amount_requested': str(p.amount_requested),
                'commission':       str(p.commission_amount),
                'amount_received':  str(p.amount_to_receive),
                'phone':            p.recipient_phone,
                'operator':         p.operator_name,
                'status':           p.status,
                'created_at':       p.created_at,
            })

        return Response(
            {
                'success': True,
                'count':   payouts.count(),
                'data':    data,
            },
            status=status.HTTP_200_OK
        )