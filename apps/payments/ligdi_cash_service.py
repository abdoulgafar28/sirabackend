import logging
import requests
from decimal import Decimal
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger('apps')

LIGDICASH_BASE_URL = "https://app.ligdicash.com/pay"

HEADERS = {
    "Apikey":        settings.LIGDICASH_API_KEY,
    "Authorization": f"Bearer {settings.LIGDICASH_API_TOKEN}",
    "Accept":        "application/json",
    "Content-Type":  "application/json",
}


class LigdiCashService:
    """
    Service d'intégration LigdiCash.
    Gère le Payin (recharge) et le Payout (retrait).
    """

    # ─────────────────────────────────────────────────
    # PAYIN — ÉTAPE 1 : Envoyer OTP au client
    # ─────────────────────────────────────────────────

    @staticmethod
    def send_otp(phone_number: str, amount: int) -> dict:
        """
        Initie une transaction et envoie un OTP
        par SMS au client via LigdiCash.

        phone_number : numéro avec indicatif pays
                       ex: "22670123456"
        amount       : montant en FCFA (entier)
        """
        try:
            url = (
                f"{LIGDICASH_BASE_URL}/v02/debitotp"
                f"/{phone_number}/{amount}"
            )

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=15,
            )
            data = response.json()

            if not data.get('error'):
                logger.info(
                    f"OTP LigdiCash envoyé → {phone_number} "
                    f"pour {amount} FCFA"
                )
                return {
                    'success': True,
                    'message': data.get('message', 'OTP envoyé avec succès.'),
                }

            logger.warning(f"LigdiCash OTP erreur : {data}")
            return {
                'success': False,
                'error':   data.get('message', 'Erreur inconnue'),
            }

        except requests.exceptions.Timeout:
            logger.error("LigdiCash timeout — send_otp")
            return {'success': False, 'error': "Délai d'attente dépassé. Réessayez."}

        except Exception as e:
            logger.error(f"LigdiCash send_otp erreur : {e}")
            return {'success': False, 'error': str(e)}

    # ─────────────────────────────────────────────────
    # PAYIN — ÉTAPE 2 : Valider l'OTP
    # ─────────────────────────────────────────────────

    @staticmethod
    def validate_otp(
        phone_number: str,
        otp: str,
        amount: int,
        payin_id: str,
        customer_firstname: str = "",
        customer_lastname: str = "",
    ) -> dict:
        """
        Valide l'OTP saisi par le client.
        Débite son compte Mobile Money.
        Retourne un invoice_token pour vérifier le statut.
        """
        try:
            url  = f"{LIGDICASH_BASE_URL}/v02/debitwallet/withotp"
            body = {
                "commande": {
                    "invoice": {
                        "items": [
                            {
                                "name":        "Recharge Wallet SIRA",
                                "description": "Dépôt sur compte SIRA",
                                "quantity":    1,
                                "unit_price":  amount,
                                "total_price": amount,
                            }
                        ],
                        "total_amount":        amount,
                        "devise":              "XOF",
                        "description":         "Recharge Wallet SIRA",
                        "customer":            phone_number,
                        "customer_firstname":  customer_firstname,
                        "customer_lastname":   customer_lastname,
                        "customer_email":      "",
                        "external_id":         "",
                        "otp":                 otp,
                    },
                    "store": {
                        "name":        settings.LIGDICASH_STORE_NAME,
                        "website_url": settings.LIGDICASH_STORE_URL,
                    },
                    "actions": {
                        "cancel_url":   "",
                        "return_url":   "",
                        "callback_url": settings.LIGDICASH_CALLBACK_URL,
                    },
                    "custom_data": {
                        "transaction_id": payin_id,  # notre ID interne
                    },
                }
            }

            response = requests.post(
                url,
                headers=HEADERS,
                json=body,
                timeout=15,
            )
            data = response.json()

            if data.get('response_code') == '00':
                logger.info(
                    f"OTP LigdiCash validé → {phone_number} "
                    f"{amount} FCFA"
                )
                return {
                    'success':       True,
                    'invoice_token': data.get('token'),
                }

            logger.warning(f"LigdiCash OTP invalide : {data}")
            return {
                'success': False,
                'error':   data.get('response_text', 'OTP invalide ou expiré.'),
            }

        except requests.exceptions.Timeout:
            logger.error("LigdiCash timeout — validate_otp")
            return {'success': False, 'error': "Délai d'attente dépassé. Réessayez."}

        except Exception as e:
            logger.error(f"LigdiCash validate_otp erreur : {e}")
            return {'success': False, 'error': str(e)}

    # ─────────────────────────────────────────────────
    # PAYIN — ÉTAPE 3 : Vérifier le statut
    # ─────────────────────────────────────────────────

    @staticmethod
    def check_payin_status(invoice_token: str) -> dict:
        """
        Vérifie le statut final d'une transaction Payin.
        status == 'completed' → paiement confirmé ✅
        """
        try:
            url = (
                f"{LIGDICASH_BASE_URL}/v01/redirect/"
                f"checkout-invoice/confirm/"
                f"?invoiceToken={invoice_token}"
            )

            response = requests.post(
                url,
                headers=HEADERS,
                timeout=15,
            )
            data = response.json()

            return {
                'success':        data.get('response_code') == '00',
                'status':         data.get('status'),          # completed / pending / nocompleted
                'amount':         data.get('amount'),
                'operator_name':  data.get('operator_name'),
                'transaction_id': data.get('transaction_id'),
                'error':          data.get('response_text', ''),
            }

        except Exception as e:
            logger.error(f"LigdiCash check_payin_status erreur : {e}")
            return {'success': False, 'status': 'failed', 'error': str(e)}

    # ─────────────────────────────────────────────────
    # PAYOUT : Retrait conducteur → Mobile Money
    # ─────────────────────────────────────────────────

    @staticmethod
    def create_payout(
        phone_number: str,
        amount: int,
        payout_id: str,
        description: str = "Retrait Wallet SIRA",
    ) -> dict:
        """
        Transfère de l'argent depuis le compte marchand SIRA
        vers le Mobile Money du conducteur.

        top_up_wallet=0 → argent envoyé directement
                           sur le Mobile Money
        """
        try:
            url  = f"{LIGDICASH_BASE_URL}/v01/withdrawal/create"
            body = {
                "commande": {
                    "amount":       amount,
                    "description":  description,
                    "customer":     phone_number,
                    "top_up_wallet": 0,         # 0 = direct Mobile Money
                    "callback_url": settings.LIGDICASH_CALLBACK_URL,
                    "custom_data": {
                        "transaction_id": payout_id,
                    },
                }
            }

            response = requests.post(
                url,
                headers=HEADERS,
                json=body,
                timeout=15,
            )
            data = response.json()

            if data.get('response_code') == '00':
                logger.info(
                    f"Payout LigdiCash créé → {phone_number} "
                    f"{amount} FCFA"
                )
                return {
                    'success':          True,
                    'withdrawal_token': data.get('token'),
                }

            logger.warning(f"LigdiCash payout erreur : {data}")
            return {
                'success': False,
                'error':   data.get('response_text', 'Erreur payout.'),
            }

        except requests.exceptions.Timeout:
            logger.error("LigdiCash timeout — create_payout")
            return {'success': False, 'error': "Délai d'attente dépassé."}

        except Exception as e:
            logger.error(f"LigdiCash create_payout erreur : {e}")
            return {'success': False, 'error': str(e)}

    # ─────────────────────────────────────────────────
    # PAYOUT : Vérifier le statut du retrait
    # ─────────────────────────────────────────────────

    @staticmethod
    def check_payout_status(withdrawal_token: str) -> dict:
        """
        Vérifie le statut d'un retrait.
        status == 'completed' → retrait effectué ✅
        """
        try:
            url = (
                f"{LIGDICASH_BASE_URL}/v01/withdrawal/confirm/"
                f"?withdrawalToken={withdrawal_token}"
            )

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=15,
            )
            data = response.json()

            return {
                'success':        data.get('response_code') == '00',
                'status':         data.get('status'),
                'amount':         data.get('amount'),
                'operator_name':  data.get('operator_name'),
                'transaction_id': data.get('transaction_id'),
                'error':          data.get('response_text', ''),
            }

        except Exception as e:
            logger.error(f"LigdiCash check_payout_status erreur : {e}")
            return {'success': False, 'status': 'failed', 'error': str(e)}