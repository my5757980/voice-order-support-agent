#!/usr/bin/env bash
# Boot the whole app as one process on one port.
#
# The frontend is built to static assets and served by the same FastAPI process that
# holds the WebSockets — no CORS, no reverse proxy, no second deploy target. That shape
# was forced by the permitted-platform constraint and turned out to be the simplest
# thing that works.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> installing backend dependencies"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e "./backend"

if [ ! -d frontend/dist ]; then
  echo "==> building frontend"
  (cd frontend && npm ci --silent 2>/dev/null || npm install --silent) && (cd frontend && npm run build)
fi

echo "==> seeding demo fixtures"
(cd backend && python -m src.adapters.store.seed)

echo "==> starting on :${PORT:-8000}"
cd backend
exec python -m uvicorn src.app:app --host 0.0.0.0 --port "${PORT:-8000}"
