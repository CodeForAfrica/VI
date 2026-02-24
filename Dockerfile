FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (required for psycopg2/math libraries)
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose Django port
EXPOSE 8000

# Start Gunicorn (Production server)
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "dashboard.wsgi:application"]
