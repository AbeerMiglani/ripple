#!/usr/bin/env bash
# Vercel build for Ripple. The Vercel project's root directory is backend/;
# Vercel installs the Python dependencies from pyproject.toml first, then runs
# this (pyproject.toml: [tool.vercel.scripts] build).
#
#   1. Migrate the database to the latest schema.
#   2. Seed the demo network if it is not there yet. An existing network is
#      left alone, so its id (and every saved run that refers to it) survives
#      redeploys.
#   3. Build the frontend into dist/. The app serves it at the site root
#      below the API routes (app.main.serve_frontend), and Vercel promotes
#      those files to its CDN.
#
# Needs DATABASE_URL (Neon sets it) and the source outside backend/ (the
# frontend and data/seed), which Vercel includes in the build by default.
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$BACKEND_DIR/.." && pwd)"
cd "$BACKEND_DIR"
# The interpreter Vercel installed the dependencies into.
PYTHON="$(command -v python || command -v python3)"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is not set: connect a Postgres database (Neon) to this project." >&2
  exit 1
fi

echo "==> Migrating the database"
# Schema changes go over the direct connection when there is one: a
# transaction-mode pooler is the wrong place for DDL.
DATABASE_URL="${DATABASE_URL_UNPOOLED:-$DATABASE_URL}" "$PYTHON" -m alembic upgrade head

echo "==> Seeding the demo network (skipped when it exists)"
"$PYTHON" -m app.services.ingestion

echo "==> Building the frontend into backend/dist"
cd "$REPO_ROOT/frontend"
# Dev dependencies hold the compiler and bundler; a production NODE_ENV
# would otherwise leave them out.
npm ci --include=dev --no-audit --no-fund
npx tsc -b
npx vite build --outDir "$BACKEND_DIR/dist" --emptyOutDir

echo "==> Build complete"
