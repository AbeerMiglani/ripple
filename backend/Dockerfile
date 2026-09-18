# ---- Rust builder ----------------------------------------------------------
# Builds the optional ripple_graph_rs acceleration (see
# app/simulation/cascade.py) as a wheel. Kept in its own stage so the
# ~1GB+ Rust toolchain never lands in the runtime image below -- only the
# compiled wheel is copied out of it.
FROM python:3.12-slim AS rust-builder

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
    sh -s -- -y --profile minimal
ENV PATH="/root/.cargo/bin:${PATH}"

RUN pip install --no-cache-dir maturin

COPY native/ripple_graph_rs native/ripple_graph_rs
RUN cd native/ripple_graph_rs && maturin build --release --out /build/wheels

# ---- Runtime image ----------------------------------------------------------
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

# Rust-accelerated global efficiency calculation. app.simulation.cascade
# imports this defensively and falls back to pure Python if it is missing, so
# this install is a performance win, not a hard runtime dependency.
COPY --from=rust-builder /build/wheels /tmp/ripple_graph_rs_wheels
RUN pip install --no-cache-dir /tmp/ripple_graph_rs_wheels/*.whl && \
    rm -rf /tmp/ripple_graph_rs_wheels

# Source is mounted as a volume in dev; this COPY is for production builds
COPY . .

EXPOSE 8000
