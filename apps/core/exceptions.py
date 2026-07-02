from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status


def custom_exception_handler(exc, context):
    """
    Handler d'erreurs global — format uniforme pour toutes les erreurs API.
    """
    response = exception_handler(exc, context)

    if response is not None:
        error_data = {
            'success': False,
            'status_code': response.status_code,
            'errors': response.data,
        }
        response.data = error_data

    return response