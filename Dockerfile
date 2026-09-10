# One image, one process, one port.
#
# Multi-stage: the frontend is built with Node, then the built assets are served by the
# same FastAPI process that holds the two WebSockets.

# --- stage 1: build the browser client -------------------------------------
FROM node:20-slim AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


# --- stage 2: runtime ------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_PATH=/app/data/orders.db

WORKDIR /app

COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/src backend/src
COPY backend/conftest.py backend/conftest.py
COPY specs/001-order-support-agent/contracts specs/001-order-support-agent/contracts

RUN pip install --no-cache-dir -e ./backend

COPY --from=frontend /build/dist frontend/dist

# Seed at build time so a cold start serves a working demo immediately. The seed is
# idempotent, so it is safe to run again at boot.
RUN mkdir -p /app/data && cd backend && python -m src.adapters.store.seed

EXPOSE 8000

# No API keys are baked in. They arrive as environment variables at run time, and none
# of them is ever sent to the browser.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/health')"

WORKDIR /app/backend
CMD ["python", "-m", "uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8000"]
