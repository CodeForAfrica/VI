from django.urls import path
from . import views

urlpatterns = [
    path('', views.overview, name='overview'),
    path('vulnerability-index/', views.overview, name='vulnerability_index'),
    path('dashboard/', views.overview, name='dashboard_overview'),
    path('vi/', views.overview, name='vi_tool'),
    path('authors/', views.authors, name='authors'),
    # CHECK THE COMMA AT THE END OF THE LINE BELOW:
    path('outlets/', views.media, name='media'), 
    path('intents/', views.intents, name='intents'),
    path('countries/', views.countries, name='countries'),
    path('all-articles/', views.all_articles, name='all_articles'),
    path('articles/', views.articles_view, name='articles'),
    path('generate-report/', views.generate_report, name='generate_report'),
    path('chat/', views.chatbot_response, name='chatbot_response'),
    path('clear-cache/', views.clear_cache_view, name='clear_cache'), # Good practice to keep a comma here too
]
