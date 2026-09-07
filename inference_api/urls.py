"""Dedicated urlconf for the inference process - deliberately does NOT import
dashboard.views (spec 666-668)."""
from django.urls import path

from . import views

urlpatterns = [
    path("healthz", views.healthz),
    path("readyz", views.readyz),
    path("api/v1/inference", views.inference),
]
