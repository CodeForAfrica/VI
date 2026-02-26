# Use Debian-based slim image
FROM python:3.11-slim

<<<<<<< Updated upstream
# Set environment variables for better Python behavior
=======
>>>>>>> Stashed changes
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

<<<<<<< Updated upstream
# Install system dependencies for compilation
# Complete list of required packages for pycairo and other dependencies
=======
>>>>>>> Stashed changes
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    g++ \
    pkg-config \
    libcairo2-dev \
    libgirepository1.0-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

<<<<<<< Updated upstream
# Upgrade pip, setuptools, and wheel
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
=======
RUN pip install --upgrade pip setuptools wheel

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

>>>>>>> Stashed changes
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "config.wsgi:application"]
