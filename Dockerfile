FROM python:3.11-slim

# Set environment variables for better Python behavior
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Combine update and installation of system dependencies in ONE command.
# This includes Cairo (libcairo2-dev) and GObject Introspection (libgirepository1.0-dev)
# which are required for pycairo (needed by xhtml2pdf via svglib).
# Also include pkg-config (modern name for pkgconf), build tools, Python dev headers,
# PostgreSQL dev headers (libpq-dev), libffi-dev, and libssl-dev.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    libcairo2-dev \
    libgirepository1.0-dev \
    python3-dev \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    # Explicitly install pkgconf as pkg-config might be an alias or symlink
    pkgconf \
    # Install pkg-config data files for cairo if needed (sometimes necessary)
    # libcairo-gobject2 might also be needed depending on the exact build process
    libcairo-gobject2 \
    && \
    # Clean up apt cache to reduce image size
    rm -rf /var/lib/apt/lists/* \
    # Verify that pkg-config can find cairo (optional, for debugging during build)
    && pkg-config --exists cairo || echo "Warning: pkg-config cannot find cairo, but continuing..."

# Upgrade pip first to use the latest dependency resolver
RUN pip install --no-cache-dir --upgrade pip

# Copy requirements.txt
COPY requirements.txt .

# Now install Python requirements using pip
# The system dependencies installed above should make pycairo build successfully
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project files
COPY . .

# Expose the port the app runs on
EXPOSE 8000

# Command to run the application with Gunicorn
# Note: Changed 'dashboard.wsgi:application' to 'config.wsgi:application' based on your earlier logs/structure
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "config.wsgi:application"]
