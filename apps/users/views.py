import random
import string
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from django.db.models import Q

from apps.users.models import OTPVerification
from apps.users.serializer import (
    UserRegistrationSerializer,
    OTPRequestSerializer,
    OTPVerifySerializer,
    UserProfileSerializer,
    UserUpdateSerializer,
    ChangePasswordSerializer,
)

User = get_user_model()


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def generate_otp_code(length=6) -> str:
    """Génère un code OTP numérique."""
    return ''.join(random.choices(string.digits, k=length))


def get_tokens_for_user(user) -> dict:
    """Génère la paire access/refresh token pour un utilisateur."""
    refresh = RefreshToken.for_user(user)
    return {
        'refresh': str(refresh),
        'access':  str(refresh.access_token),
    }


def send_sms_otp(phone_number: str, code: str) -> bool:
    """
    Envoi du SMS OTP.
    En développement : affiche dans la console.
    En production : brancher sur un provider SMS (ex: Orange SMS API).
    """
    print(f"[SMS OTP] → {phone_number} : Votre code SIRA est {code}")
    return True


# ─────────────────────────────────────────────────────────────
# INSCRIPTION
# ─────────────────────────────────────────────────────────────

class RegisterView(APIView):
    """
    Inscription d'un nouvel utilisateur (client ou conducteur).
    Après inscription, un OTP est envoyé automatiquement.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = serializer.save()

        # Envoi OTP automatique après inscription
        code = generate_otp_code()
        OTPVerification.objects.create(
            user=user,
            code=code,
            purpose=OTPVerification.Purpose.REGISTRATION,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        send_sms_otp(user.phone_number, code)

        return Response(
            {
                'success': True,
                'message': f"Compte créé. Un code OTP a été envoyé au {user.phone_number}.",
                'data': {
                    'user_id': str(user.id),
                    'phone_number': user.phone_number,
                    'role': user.role,
                }
            },
            status=status.HTTP_201_CREATED
        )


# ─────────────────────────────────────────────────────────────
# OTP
# ─────────────────────────────────────────────────────────────

# apps/users/views.py

class OTPSendView(APIView):
    """Génère un code OTP et le renvoie au frontend pour envoi."""
    permission_classes = [AllowAny]

    def post(self, request):
        identifier = request.data.get('identifier', '').strip()
        
        # Chercher l'utilisateur par email ou téléphone
        from django.db.models import Q
        user = User.objects.filter(
            Q(email=identifier) | Q(phone_number=identifier)
        ).first()
        
        if not user:
            return Response(
                {'success': False, 'errors': {'identifier': 'Aucun compte trouvé.'}},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Générer le code OTP
        code = generate_otp_code()
        OTPVerification.objects.create(
            user=user,
            code=code,
            purpose=request.data.get('purpose', 'login'),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        
        # Retourner le code au frontend (pour qu'il l'envoie)
        return Response({
            'success': True,
            'code': code,  # ← Le frontend va utiliser ce code pour envoyer SMS/Email
            'identifier_type': 'email' if user.email == identifier else 'phone',
            'contact': identifier,
        })







"""class OTPSendView(APIView):
    
    Envoi ou renvoi d'un code OTP.
    Utilisé pour : inscription, connexion, réinitialisation.
    
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = OTPRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        phone   = serializer.validated_data['phone_number']
        purpose = serializer.validated_data['purpose']

        try:
            user = User.objects.get(phone_number=phone)
        except User.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'phone_number': 'Aucun compte trouvé.'}},
                status=status.HTTP_404_NOT_FOUND
            )

        # Anti-spam : bloquer si un OTP valide existe déjà depuis < 1 minute
        recent_otp = OTPVerification.objects.filter(
            user=user,
            purpose=purpose,
            is_used=False,
            expires_at__gt=timezone.now(),
            created_at__gt=timezone.now() - timedelta(minutes=1),
        ).exists()

        if recent_otp:
            return Response(
                {
                    'success': False,
                    'errors': {'detail': 'Veuillez attendre 1 minute avant de renvoyer un code.'}
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        # Invalider les anciens OTPs du même type
        OTPVerification.objects.filter(
            user=user,
            purpose=purpose,
            is_used=False
        ).update(is_used=True)

        # Créer et envoyer le nouveau OTP
        code = generate_otp_code()
        OTPVerification.objects.create(
            user=user,
            code=code,
            purpose=purpose,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        send_sms_otp(user.phone_number, code)

        return Response(
            {
                'success': True,
                'message': f"Code OTP envoyé au {phone}.",
            },
            status=status.HTTP_200_OK
        )"""






class OTPVerifyView(APIView):
    """
    Vérification du code OTP.
    Si purpose=registration → marque le compte comme vérifié.
    Si purpose=login        → retourne les tokens JWT directement.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = OTPVerifySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        user    = serializer.validated_data['user']
        otp     = serializer.validated_data['otp']
        purpose = serializer.validated_data['purpose']

        # Marquer l'OTP comme utilisé
        otp.is_used = True
        otp.save(update_fields=['is_used'])

        response_data = {'success': True}

        if purpose == OTPVerification.Purpose.REGISTRATION:
            # Activer le compte
            user.is_verified = True
            user.status      = User.Status.ACTIVE
            user.save(update_fields=['is_verified', 'status'])

            response_data['message'] = "Téléphone vérifié. Compte activé."

        elif purpose == OTPVerification.Purpose.LOGIN:
            # Connexion directe par OTP → retourner les tokens
            tokens = get_tokens_for_user(user)
            response_data['message'] = "Connexion réussie."
            response_data['data']    = {
                **tokens,
                'user': UserProfileSerializer(user).data,
            }

        return Response(response_data, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────────
# CONNEXION / LOGOUT
# ─────────────────────────────────────────────────────────────


class LoginView(APIView):
    """
    Connexion par numéro de téléphone + mot de passe.
    Retourne access token + refresh token.
    """
    permission_classes = [AllowAny]


    def post(self, request):
        phone    = request.data.get('phone_number', '').strip()
        identifier = request.data.get('identifier', '').strip()  # email ou phone
        password = request.data.get('password', '').strip()

        if not phone or not password:
            return Response(
                {
                    'success': False,
                    'errors': {'detail': 'Numéro de téléphone et mot de passe obligatoires.'}
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user = User.objects.filter(
                Q(email=identifier) | Q(phone_number=identifier)
            ).first()
        except User.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'detail': 'Identifiants incorrects.'}},
                status=status.HTTP_401_UNAUTHORIZED
            )
        

        if not user or not user.check_password(password):
            return Response(
                {'success': False, 'errors': {'detail': 'Identifiants incorrects.'}},
                status=status.HTTP_401_UNAUTHORIZED
            )

        # Vérifier le mot de passe
        if not user.check_password(password):
            return Response(
                {'success': False, 'errors': {'detail': 'Identifiants incorrects.'}},
                status=status.HTTP_401_UNAUTHORIZED
            )

        # Vérifier que le compte est actif
        if not user.is_verified:
            return Response(
                {
                    'success': False,
                    'errors': {'detail': 'Compte non vérifié. Vérifiez votre téléphone.'}
                },
                status=status.HTTP_403_FORBIDDEN
            )

        if user.status == User.Status.SUSPENDED:
            return Response(
                {
                    'success': False,
                    'errors': {
                        'detail': f"Compte suspendu. Raison : {user.suspension_reason or 'Non précisée'}"
                    }
                },
                status=status.HTTP_403_FORBIDDEN
            )

        if user.status == User.Status.BANNED:
            return Response(
                {'success': False, 'errors': {'detail': "Compte banni définitivement."}},
                status=status.HTTP_403_FORBIDDEN
            )

        # Mettre à jour last_seen
        user.last_seen_at = timezone.now()
        user.save(update_fields=['last_seen_at'])

        tokens = get_tokens_for_user(user)

        return Response(
            {
                'success': True,
                'message': "Connexion réussie.",
                'data': {
                    **tokens,
                    'user': UserProfileSerializer(user).data,
                }
            },
            status=status.HTTP_200_OK
        )


class LogoutView(APIView):
    """
    Déconnexion — blackliste le refresh token.
    L'access token expire naturellement (durée courte : 2h).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get('refresh')

        if not refresh_token:
            return Response(
                {'success': False, 'errors': {'detail': 'Refresh token manquant.'}},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except TokenError:
            return Response(
                {'success': False, 'errors': {'detail': 'Token invalide ou déjà révoqué.'}},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {'success': True, 'message': "Déconnexion réussie."},
            status=status.HTTP_200_OK
        )


# ─────────────────────────────────────────────────────────────
# PROFIL UTILISATEUR CONNECTÉ
# ─────────────────────────────────────────────────────────────

class MeView(APIView):
    """
    GET  → retourne le profil de l'utilisateur connecté
    PATCH → met à jour le profil
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UserProfileSerializer(request.user)
        return Response(
            {'success': True, 'data': serializer.data},
            status=status.HTTP_200_OK
        )

    def patch(self, request):
        serializer = UserUpdateSerializer(
            request.user,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        serializer.save()
        return Response(
            {'success': True, 'message': "Profil mis à jour.", 'data': serializer.data},
            status=status.HTTP_200_OK
        )


class ChangePasswordView(APIView):
    """Changement de mot de passe pour utilisateur connecté."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data,
            context={'request': request}
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        request.user.set_password(serializer.validated_data['new_password'])
        request.user.save(update_fields=['password'])

        return Response(
            {'success': True, 'message': "Mot de passe modifié avec succès."},
            status=status.HTTP_200_OK
        )