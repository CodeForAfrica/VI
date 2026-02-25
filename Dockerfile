FROM python:3.11-slim

# Set environment variables for better Python behavior
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Combine update and installation of system dependencies in ONE command.
# Install pkg-config, Cairo dev libraries, GObject Introspection, build tools,
# Python dev headers, PostgreSQL dev headers, libffi-dev, and libssl-dev.
# Also install pkgconf explicitly as the underlying tool.
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
    # Install pkg-config data files for cairo explicitly (sometimes needed)
    libcairo-gobject2 \
    && \
    # Verify pkg-config binary exists and is in PATH during build
    command -v pkg-config && \
    # Verify pkg-config can find cairo libraries during build
    pkg-config --exists cairo && \
    # Print the PATH where pkg-config was found (debugging aid)
    echo "pkg-config found at: $(which pkg-config)" && \
    # Print where Cairo.pc might be (debugging aid)
    find /usr -name cairo.pc -type f 2>/dev/null && \
    # Clean up apt cache to reduce image size
    rm -rf /var/lib/apt/lists/*

# Upgrade pip first to use the latest dependency resolver
RUN pip install --no-cache-dir --upgrade pip

# Copy requirements.txt
COPY requirements.txt .

# Install Python requirements using pip
# The system dependencies installed above should make pycairo build successfully
# IF pkg-config is correctly found during the build process.
# Explicitly set PATH and PKG_CONFIG_PATH for the pip install command.
# PKG_CONFIG_PATH often points to directories like /usr/lib/pkgconfig or /usr/share/pkgconfig
RUN PKG_CONFIG_PATH="/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig:$PKG_CONFIG_PATH" \
    PATH="$PATH:/usr/bin:/usr/local/bin" \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project files
COPY . .

# Expose the port the app runs on
EXPOSE 8000

# Command to run the application with Gunicorn
# Note: Changed 'dashboard.wsgi:application' to 'config.wsgi:application' based on your earlier logs/structure
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "config.wsgi:application"]
