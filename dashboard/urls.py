from django.urls import path, include
from . import views

urlpatterns = [
    path('', views.overview, name='overview'),
    path('vulnerability-index/', views.overview, name='vulnerability_index'),
    path('dashboard/', views.overview, name='dashboard_overview'),
    path('vi/', views.overview, name='vi_tool'),
    path('authors/', views.authors, name='authors'),
    path('i18n/', include('django.conf.urls.i18n')),
    path('outlets/', views.media, name='media'),
    path('countries/', views.countries, name='countries'),
    path('articles/', views.articles_view, name='articles'),
    path('generate-report/', views.generate_report, name='generate_report'),
    path('chat/', views.chatbot_response, name='chatbot_response'),
    path('clear-cache/', views.clear_cache_view, name='clear_cache'), 
]
