#!/usr/bin/env bash
# Boot the whole app as one process on one port.
#
# The frontend is built to static assets and served by the same FastAPI process that
# holds the WebSockets — no CORS, no reverse proxy, no second deploy target. That shape
# was forced by the permitted-platform constraint and turned out to be the simplest
# thing that works.
set -euo pipefail

cd "$(dirname "$0")/.."

# Replit's Python is Nix-managed and marked EXTERNALLY-MANAGED (PEP 668), so pip refuses
# to install into it without this. The environment-variable form rather than the
# --break-system-packages flag, because a pip too old to know the option ignores an
# unknown variable but fails on an unknown flag. Inside a venv it changes nothing.
export PIP_BREAK_SYSTEM_PACKAGES=1

echo "==> installing backend dependencies"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e "./backend"

# Always build. Skipping the build whenever dist/ existed meant a redeploy shipped whatever
# was built last — which is how the live app kept serving untranspiled worklets, with no
# audio in either direction, after the fix was already pushed. Vite builds this in about
# a second; only the dependency install is worth skipping.
if [ ! -d frontend/node_modules ]; then
  echo "==> installing frontend dependencies"
  (cd frontend && (npm ci --silent 2>/dev/null || npm install --silent))
fi
echo "==> building frontend"
(cd frontend && npm run build)

echo "==> seeding demo fixtures"
(cd backend && python -m src.adapters.store.seed)

echo "==> starting on :${PORT:-8000}"
cd backend
exec python -m uvicorn src.app:app --host 0.0.0.0 --port "${PORT:-8000}"
