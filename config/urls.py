"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from apps.core.views import home
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView


urlpatterns = [
    path('', home, name='home'),

    path('admin/',          admin.site.urls),

    # Auth
    path('api/v1/auth/',    include('apps.users.urls')),

    # Drivers
    path('api/v1/drivers/', include('apps.drivers.urls')),

    # Courses
    path('api/v1/rides/',    include('apps.rides.urls')),

    # Tracking
    path('api/v1/tracking/',    include('apps.tracking.urls')),

    # Contrôles anti-fraude
    path('api/v1/fraud/',       include('apps.fraud_detection.urls')),

    # Documentation
    path('api/schema/',     SpectacularAPIView.as_view(),    name='schema'),
    path('api/docs/',       SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),


    #Paiement
    path('api/v1/payments/', include('apps.payments.urls')),   # ✅ AJOUT ICI

    # Admin Panel 
    path('api/v1/admin/',       include('apps.admin_panel.urls')),  # ← nouveau

]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
