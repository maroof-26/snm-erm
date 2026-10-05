from django.contrib import admin
from django.urls import include, path

admin.site.site_header = 'SNM ERP administration'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', include('erp.urls')),
]
