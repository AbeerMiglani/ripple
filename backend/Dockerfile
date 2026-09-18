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

# Install Python dependencies. Dev tooling (pytest/mypy/ruff/httpx) is
# deliberately excluded: nothing under app/ imports any of it (this Dockerfile
# builds all four compose services -- backend, worker, migrator, seeder --
# and none of them need it at runtime), CI installs it separately via pip
# rather than through this image, and `./demo test` runs a scripted scenario
# script, not pytest, inside the container.
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Source is mounted as a volume in dev; this COPY is for production builds
COPY . .

EXPOSE 8000
