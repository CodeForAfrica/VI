"""Explicitly selected only by make run-local-dashboard; never used for deployment."""
import os
from .settings import *  # noqa: F403

ROOT_URLCONF = "config.local_test_urls"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
DEBUG = False
