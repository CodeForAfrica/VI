from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('dashboard.urls')),  # Your main dashboard
    path('vulnerability-index/', include('dashboard.urls')),  # SEO-friendly URL
    path('vi-tool/', include('dashboard.urls')),  # Another SEO-friendly option
    path('dashboard/', include('dashboard.urls')),  # Dashboard URL
    path('index/', RedirectView.as_view(pattern_name='overview'), name='index'),  # Redirect
    path('generate-report/', views.generate_report, name='generate_report'),
    path('report/', views.generate_report, name='report'),
]
