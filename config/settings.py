import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
# Handle special characters in secret key
try:
    # Try to get from environment variable first
    SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'fallback-secret-key-for-development')
    # If it's the fallback, use the actual key
    if SECRET_KEY == 'fallback-secret-key-for-development':
        SECRET_KEY = "(@lhxdh^3z1aea9xjny21q^0crno_h48*3!y7en!g#x(5^*zad"
except:
    # Fallback if environment variable access fails
    SECRET_KEY = "(@lhxdh^3z1aea9xjny21q^0crno_h48*3!y7en!g#x(5^*zad"

# SECURITY WARNING: Don't run with debug turned on in production!
#DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'
DEBUG = True
# Security: Allow specific hosts only

# Get ALLOWED_HOSTS from environment variable, with fallback to specific IPs
import os

ALLOWED_HOSTS = ['*']

# CSRF_TRUSTED_ORIGINS MUST have the scheme (http://)
CSRF_TRUSTED_ORIGINS = [
    'http://3.254.121.126', 
    'http://localhost',
    'http://*.compute-1.amazonaws.com'  # This handles the internal AWS address
]
    
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'dashboard',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database - SECURE: All from environment variables
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'postgres'),
        'USER': os.getenv('DB_USER', 'postgres'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST', 'rds-vulnerabilityindex-euwest-01.cfgmtx8ishfx.eu-west-1.rds.amazonaws.com'),  # RDS endpoint
        'PORT': os.getenv('DB_PORT', '5432'),
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# AWS Configuration - SECURE: All from environment
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
AWS_S3_REGION_NAME = os.getenv('AWS_S3_REGION_NAME', 'us-east-1')
S3_MODELS_BUCKET = os.getenv('S3_MODELS_BUCKET')

# API Keys - SECURE: All from environment
MEDIACLOUD_API_KEY = os.getenv('MEDIACLOUD_API_KEY')
GROQ_API_KEY = os.getenv("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")
