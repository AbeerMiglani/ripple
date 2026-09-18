FROM python:3.12-slim

WORKDIR /app

# System deps for psycopg2 and geo libs
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        libgeos-dev \
        libproj-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

# Source is mounted as a volume in dev; this COPY is for production builds
COPY . .

EXPOSE 8000
