FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv

WORKDIR /app

RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    pkg-config \
    libcairo2-dev \
    libgirepository1.0-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --upgrade pip setuptools wheel \
    && pip install -r requirements.txt

FROM builder AS translations

RUN apt-get update && apt-get install -y --no-install-recommends gettext \
    && rm -rf /var/lib/apt/lists/*
COPY locale/en/LC_MESSAGES/django.po ./locale/en/LC_MESSAGES/django.po
COPY locale/fr/LC_MESSAGES/django.po ./locale/fr/LC_MESSAGES/django.po
RUN echo '[build] Compiling English and French translation catalogues.' \
    && python -m django compilemessages --locale en --locale fr --ignore=venv \
    && echo '[build] OK - English and French catalogues compiled.'

FROM python:3.11-slim AS catalogue-tools
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends gettext \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
# Catalogue extraction needs Django/Babel, not the dashboard's ML runtime.
# Select their pinned dependency closure from the same runtime requirements.
RUN grep -E '^(Django|babel|asgiref|sqlparse)==' requirements.txt > /tmp/catalogue-requirements.txt \
    && pip install --no-cache-dir -r /tmp/catalogue-requirements.txt && pip check

FROM catalogue-tools AS local-checks
COPY locale ./locale
COPY dashboard ./dashboard
COPY config ./config
COPY scripts/check_translations.py ./scripts/check_translations.py
CMD ["python", "scripts/check_translations.py"]

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    HOME=/home/appuser \
    MPLCONFIGDIR=/home/appuser/.config/matplotlib

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    libgirepository-1.0-1 \
    libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system appgroup \
    && adduser --system --ingroup appgroup --home /home/appuser appuser \
    && mkdir -p /home/appuser/.config/matplotlib \
    && chown -R appuser:appgroup /home/appuser

COPY --from=builder /opt/venv /opt/venv
COPY . .
COPY --from=translations /app/locale/ ./locale/

RUN python manage.py collectstatic --noinput \
    && chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py collectstatic --noinput && exec gunicorn --bind 0.0.0.0:8000 config.wsgi:application"]
