import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from apps.payments.models import (
    SiraWallet, WalletTransaction,
    COMMISSION_COURSE_PERCENT,
    COMMISSION_RETRAIT_PERCENT,
    FRAIS_ANNULATION_PERCENT,
)

logger = logging.getLogger('apps')


class WalletService:
    """
    Service de gestion des wallets SIRA.
    Toutes les opérations financières internes passent ici.
    """

    @staticmethod
    def create_wallet(user) -> SiraWallet:
        wallet, created = SiraWallet.objects.get_or_create(user=user)
        if created:
            logger.info(f"Wallet créé pour {user.phone_number}")
        return wallet

    @staticmethod
    def credit_wallet(
        wallet: SiraWallet,
        amount: Decimal,
        transaction_type: str,
        description: str,
        ride=None,
        metadata: dict = None,
    ) -> WalletTransaction:
        """Crédite un wallet de façon atomique."""
        with transaction.atomic():
            wallet = SiraWallet.objects.select_for_update().get(pk=wallet.pk)

            if not wallet.is_active:
                raise ValueError(f"Wallet {wallet.id} inactif.")

            balance_before = wallet.balance
            balance_after  = wallet.balance + amount

            wallet.balance        = balance_after
            wallet.total_credited = wallet.total_credited + amount
            wallet.save(update_fields=['balance', 'total_credited', 'updated_at'])

            tx = WalletTransaction.objects.create(
                wallet           = wallet,
                transaction_type = transaction_type,
                direction        = WalletTransaction.Direction.CREDIT,
                amount           = amount,
                balance_before   = balance_before,
                balance_after    = balance_after,
                ride             = ride,
                status           = WalletTransaction.Status.SUCCESS,
                description      = description,
                metadata         = metadata or {},
            )

            logger.info(
                f"CREDIT {amount} XOF → {wallet.user.phone_number} "
                f"| Solde: {balance_after} XOF"
            )
            return tx

    @staticmethod
    def debit_wallet(
        wallet: SiraWallet,
        amount: Decimal,
        transaction_type: str,
        description: str,
        ride=None,
        metadata: dict = None,
    ) -> WalletTransaction:
        """Débite un wallet de façon atomique."""
        with transaction.atomic():
            wallet = SiraWallet.objects.select_for_update().get(pk=wallet.pk)

            if not wallet.is_active:
                raise ValueError(f"Wallet {wallet.id} inactif.")

            if wallet.balance < amount:
                raise ValueError(
                    f"Solde insuffisant. "
                    f"Disponible: {wallet.balance} XOF, "
                    f"Requis: {amount} XOF"
                )

            balance_before = wallet.balance
            balance_after  = wallet.balance - amount

            wallet.balance       = balance_after
            wallet.total_debited = wallet.total_debited + amount
            wallet.save(update_fields=['balance', 'total_debited', 'updated_at'])

            tx = WalletTransaction.objects.create(
                wallet           = wallet,
                transaction_type = transaction_type,
                direction        = WalletTransaction.Direction.DEBIT,
                amount           = amount,
                balance_before   = balance_before,
                balance_after    = balance_after,
                ride             = ride,
                status           = WalletTransaction.Status.SUCCESS,
                description      = description,
                metadata         = metadata or {},
            )

            logger.info(
                f"DEBIT {amount} XOF ← {wallet.user.phone_number} "
                f"| Solde: {balance_after} XOF"
            )
            return tx

    @staticmethod
    def process_ride_payment(ride) -> dict:
        """
        Paiement interne d'une course :
        Client → Conducteur (90%) + SIRA (10%)
        """
        from apps.users.models import User
        from apps.payments.models import DriverEarning
        from datetime import date

        total_fare    = ride.total_fare
        commission    = round(total_fare * Decimal(COMMISSION_COURSE_PERCENT) / 100, 2)
        driver_amount = total_fare - commission

        client_wallet = SiraWallet.objects.get(user=ride.client)
        driver_wallet = SiraWallet.objects.get(user=ride.driver.user)
        sira_admin    = User.objects.filter(role=User.Role.ADMIN).first()
        sira_wallet   = SiraWallet.objects.get(user=sira_admin)

        with transaction.atomic():
            # Débiter client
            WalletService.debit_wallet(
                wallet           = client_wallet,
                amount           = total_fare,
                transaction_type = WalletTransaction.Type.PAIEMENT_COURSE,
                description      = f"Paiement course #{ride.id}",
                ride             = ride,
                metadata         = {
                    'driver_name': ride.driver.user.full_name,
                    'distance_km': str(ride.actual_distance_km),
                },
            )

            # Créditer conducteur
            WalletService.credit_wallet(
                wallet           = driver_wallet,
                amount           = driver_amount,
                transaction_type = WalletTransaction.Type.RECEPTION_COURSE,
                description      = f"Gain course #{ride.id}",
                ride             = ride,
                metadata         = {
                    'gross_amount': str(total_fare),
                    'commission':   str(commission),
                    'net_amount':   str(driver_amount),
                },
            )

            # Créditer SIRA
            WalletService.credit_wallet(
                wallet           = sira_wallet,
                amount           = commission,
                transaction_type = WalletTransaction.Type.COMMISSION,
                description      = f"Commission course #{ride.id} (10%)",
                ride             = ride,
            )

            # Marquer course payée
            ride.is_paid = True
            ride.save(update_fields=['is_paid'])

            # Créer DriverEarning
            DriverEarning.objects.update_or_create(
                ride=ride,
                defaults={
                    'driver':            ride.driver,
                    'gross_amount':      total_fare,
                    'commission_amount': commission,
                    'net_amount':        driver_amount,
                    'is_paid':           True,
                    'paid_at':           timezone.now(),
                    'earning_date':      date.today(),
                }
            )

        logger.info(
            f"Paiement course {ride.id} → "
            f"Client: -{total_fare} | "
            f"Driver: +{driver_amount} | "
            f"SIRA: +{commission}"
        )

        return {
            'total_fare':    str(total_fare),
            'driver_amount': str(driver_amount),
            'commission':    str(commission),
        }

    @staticmethod
    def process_refund(ride, reason_valid: bool) -> dict:
        """Remboursement d'une course annulée."""
        from apps.users.models import User

        total_fare    = ride.total_fare or ride.request.estimated_price
        client_wallet = SiraWallet.objects.get(user=ride.client)
        sira_admin    = User.objects.filter(role=User.Role.ADMIN).first()
        sira_wallet   = SiraWallet.objects.get(user=sira_admin)

        with transaction.atomic():
            if reason_valid:
                # Remboursement 100%
                WalletService.credit_wallet(
                    wallet           = client_wallet,
                    amount           = total_fare,
                    transaction_type = WalletTransaction.Type.REMBOURSEMENT,
                    description      = f"Remboursement course #{ride.id}",
                    ride             = ride,
                )
                frais = Decimal('0')

            else:
                # Frais 5% + remboursement 95%
                frais         = round(total_fare * Decimal(FRAIS_ANNULATION_PERCENT) / 100, 2)
                refund_amount = total_fare - frais

                WalletService.credit_wallet(
                    wallet           = client_wallet,
                    amount           = refund_amount,
                    transaction_type = WalletTransaction.Type.REMBOURSEMENT,
                    description      = f"Remboursement 95% course #{ride.id}",
                    ride             = ride,
                )
                WalletService.credit_wallet(
                    wallet           = sira_wallet,
                    amount           = frais,
                    transaction_type = WalletTransaction.Type.FRAIS_ANNULATION,
                    description      = f"Frais annulation course #{ride.id} (5%)",
                    ride             = ride,
                )

        return {
            'refund_amount': str(total_fare - frais),
            'frais':         str(frais),
            'reason_valid':  reason_valid,
        }