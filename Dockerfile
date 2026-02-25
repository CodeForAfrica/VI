FROM python:3.11-slim

# Set environment variables for better Python behavior
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies needed for compilation *if* wheels are unavailable,
# and crucially, for pkg-config to find the libraries (needed for wheel builds too).
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
    pkgconf \
    libcairo-gobject2 \
    && \
    # Verify pkg-config exists and can find cairo (optional, for debugging during build)
    command -v pkg-config && \
    pkg-config --exists cairo && \
    # Clean up apt cache
    rm -rf /var/lib/apt/lists/*

# Upgrade pip, setuptools, and wheel to the latest versions *before* installing requirements.
# This is the crucial step for Strategy 1 to find pre-compiled wheels effectively.
RUN pip install --upgrade pip setuptools wheel

# Copy requirements.txt
COPY requirements.txt .

# Install Python requirements using pip.
# With an upgraded pip, this should now preferentially download and install
# pre-compiled wheels for packages like pycairo (via rlpycairo -> svglib -> xhtml2pdf)
# if available on PyPI for the target platform (aarch64, cp311).
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project files
COPY . .

# Expose the port the app runs on
EXPOSE 8000

# Command to run the application with Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "config.wsgi:application"]
