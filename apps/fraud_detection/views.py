from django.shortcuts import render

# Create your views here.
import logging
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permission import IsAdminUser
from apps.fraud_detection.models import FraudCheck
from apps.fraud_detection.serializer import (
    FraudCheckListSerializer,
    FraudCheckDetailSerializer,
    FraudCheckResolveSerializer,
)
from apps.fraud_detection.services import FraudDetectionService
from apps.rides.models import Ride

logger = logging.getLogger('apps')


class FraudCheckListView(APIView):
    """
    GET → liste tous les contrôles anti-fraude.
    Filtres : ?statut=alerte&check_status=pending
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        checks = FraudCheck.objects.select_related(
            'driver__user', 'ride__client'
        ).order_by('-created_at')

        # Filtres
        statut       = request.query_params.get('statut')
        check_status = request.query_params.get('check_status')
        date_from    = request.query_params.get('date_from')
        date_to      = request.query_params.get('date_to')

        if statut:
            checks = checks.filter(statut=statut)
        if check_status:
            checks = checks.filter(check_status=check_status)
        if date_from:
            checks = checks.filter(created_at__date__gte=date_from)
        if date_to:
            checks = checks.filter(created_at__date__lte=date_to)

        # Pagination
        page      = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        start     = (page - 1) * page_size
        end       = start + page_size
        total     = checks.count()

        # Statistiques globales
        stats = {
            'total':           total,
            'alertes':         FraudCheck.objects.filter(statut='alerte').count(),
            'avertissements':  FraudCheck.objects.filter(statut='avertissement').count(),
            'en_attente':      FraudCheck.objects.filter(check_status='pending').count(),
            'confirmes':       FraudCheck.objects.filter(check_status='confirmed').count(),
        }

        serializer = FraudCheckListSerializer(checks[start:end], many=True)

        return Response(
            {
                'success':     True,
                'stats':       stats,
                'count':       total,
                'page':        page,
                'total_pages': (total + page_size - 1) // page_size,
                'data':        serializer.data,
            },
            status=status.HTTP_200_OK
        )


class FraudCheckDetailView(APIView):
    """GET → détail complet d'un contrôle."""
    permission_classes = [IsAdminUser]

    def get(self, request, fraud_id):
        try:
            check = FraudCheck.objects.select_related(
                'driver__user', 'ride__client'
            ).get(id=fraud_id)
        except FraudCheck.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'detail': "Contrôle introuvable."}},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response(
            {'success': True, 'data': FraudCheckDetailSerializer(check).data},
            status=status.HTTP_200_OK
        )


class FraudCheckResolveView(APIView):
    """
    PATCH → admin résout un contrôle anti-fraude.
    check_status : cleared (sans fraude) ou confirmed (fraude confirmée)
    """
    permission_classes = [IsAdminUser]

    def patch(self, request, fraud_id):
        try:
            check = FraudCheck.objects.get(id=fraud_id)
        except FraudCheck.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'detail': "Contrôle introuvable."}},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = FraudCheckResolveSerializer(
            check, data=request.data, partial=True
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        check = serializer.save(
            reviewed_by = request.user,
            reviewed_at = timezone.now(),
        )

        # Si fraude confirmée → suspendre le conducteur
        if check.check_status == FraudCheck.CheckStatus.CONFIRMED:
            self._handle_confirmed_fraud(check, request.user)

        logger.info(
            f"FraudCheck {fraud_id} résolu par {request.user.phone_number} "
            f"→ {check.check_status}"
        )

        return Response(
            {
                'success': True,
                'message': f"Contrôle résolu : {check.check_status}",
                'data':    FraudCheckDetailSerializer(check).data,
            },
            status=status.HTTP_200_OK
        )

    def _handle_confirmed_fraud(self, check, admin_user):
        """Suspend le conducteur si fraude confirmée."""
        from apps.admin_panel.models import SystemLog

        driver_user = check.driver.user
        driver_user.status           = driver_user.Status.SUSPENDED
        driver_user.suspension_reason = (
            f"Fraude confirmée — Course {check.ride_id} — "
            f"Score {check.fraud_score}/100"
        )
        driver_user.save(update_fields=['status', 'suspension_reason'])

        # Log système
        SystemLog.objects.create(
            action      = SystemLog.ActionType.USER_SUSPENDED,
            performed_by= admin_user,
            target_user = driver_user,
            description = f"Suspension pour fraude confirmée. Score: {check.fraud_score}/100",
            metadata    = {
                'fraud_check_id': str(check.id),
                'ride_id':        str(check.ride_id),
                'fraud_score':    check.fraud_score,
                'incidents':      check.incidents,
            },
        )

        # Notifier le conducteur
        from apps.notifications.services import NotificationService
        NotificationService.create(
            recipient         = driver_user,
            notification_type = 'account_suspended',
            title             = "Compte suspendu",
            body              = (
                "Votre compte a été suspendu suite à une fraude détectée. "
                "Contactez le support SIRA."
            ),
        )


class FraudCheckTriggerView(APIView):
    """
    POST → déclenche manuellement l'analyse anti-fraude
    pour une course spécifique (usage admin/debug).
    """
    permission_classes = [IsAdminUser]

    def post(self, request, ride_id):
        try:
            ride = Ride.objects.get(id=ride_id)
        except Ride.DoesNotExist:
            return Response(
                {'success': False, 'errors': {'detail': "Course introuvable."}},
                status=status.HTTP_404_NOT_FOUND
            )

        if ride.status != Ride.Status.COMPLETED:
            return Response(
                {
                    'success': False,
                    'errors': {'detail': "L'analyse ne peut être faite que sur une course terminée."}
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        fraud_check = FraudDetectionService.analyze(ride)

        return Response(
            {
                'success': True,
                'message': "Analyse anti-fraude effectuée.",
                'data':    FraudCheckDetailSerializer(fraud_check).data,
            },
            status=status.HTTP_200_OK
        )