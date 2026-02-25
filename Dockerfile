# Use an Alpine base image
FROM python:3.11-alpine

# Set environment variables for better Python behavior
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies for compilation using apk (Alpine's package manager)
# Includes musl-dev for standard C library headers, which might be needed by Cairo build tools.
# The names of the packages are often different in Alpine.
RUN apk add --no-cache \
    build-base \
    cairo-dev \
    glib-dev \
    pkgconfig \
    python3-dev \
    postgresql-dev \
    musl-dev \
    linux-headers \
    # Cairo GObject library (sometimes needed)
    cairo-gobject-dev

# Upgrade pip, setuptools, and wheel within the Alpine environment
RUN pip install --upgrade pip setuptools wheel

# Copy requirements.txt
COPY requirements.txt .

# Install Python requirements using pip
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project files
COPY . .

# Expose the port the app runs on
EXPOSE 8000

# Command to run the application with Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "config.wsgi:application"]
