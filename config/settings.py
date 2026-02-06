INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'dashboard',  # your app
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

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'media_db',
        'USER': 'postgres',
        'PASSWORD': 'B1234',
        'HOST': 'localhost',
        'PORT': '1621',
    }
}

# Point this to the Python module that contains your urls.py
ROOT_URLCONF = 'config.urls'

# Debug mode (True for development)
DEBUG = True
SECRET_KEY = "(@lhxdh^3z1aea9xjny21q^0crno_h48*3!y7en!g#x(5^*zad"
# Hosts allowed to connect
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],  # Optional: for project-wide templates
        'APP_DIRS': True,  # ← THIS IS CRITICAL — enables dashboard/templates/
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

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'  # ← This is the REQUIRED setting

# Optional: Where to collect static files when you run collectstatic (for production)
STATIC_ROOT = BASE_DIR / 'staticfiles'

