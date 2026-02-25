FROM python:3.11-slim

# Set environment variables for better Python behavior
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Combine update and installation into ONE command to ensure consistency.
# Including 'pkgconf' as it is the modern equivalent of 'pkg-config'.
# We also add 'libffi-dev' and 'libssl-dev' which are often required for cryptography/pyHanko.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libcairo2-dev \
    pkg-config \
    pkgconf \
    python3-dev \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip first to use the latest dependency resolver
RUN pip install --no-cache-dir --upgrade pip

# Now install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

EXPOSE 8000

# Start the application
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "dashboard.wsgi:application"]
