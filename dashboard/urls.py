from django.urls import path
from . import views

urlpatterns = [
    path('', views.overview, name='home'),
    path('cii/', views.cii, name='cii'),
    path('countries/', views.countries, name='countries'),
    path('authors/', views.authors, name='authors'),
    path('media/', views.media, name='media'),
    path('articles/', views.all_articles, name='all_articles'), 
    path('report/', views.generate_report, name='generate_report'),
    path('chatbot-response/', views.chatbot_response, name='chatbot_response'),
    path('actors/', views.actors, name='actors'),
]
