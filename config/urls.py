from django.contrib import admin
from django.urls import path, include
from dashboard import views  
from dashboard.views import clear_cache_view

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('dashboard.urls')),
    path('generate-report/', views.generate_report, name='generate_report'),
    path('report/', views.generate_report, name='report'),
    path('chatbot-response/', views.chatbot_response, name='chatbot_response'),
    path('clear-cache/', clear_cache_view, name='clear_cache'),
]
