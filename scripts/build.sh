#!/usr/bin/env bash
# Everything slow: dependencies and the frontend build. Never on the serving path.
#
# Replit's published deployments health-check `/` shortly after the run command starts.
# When installs and the Vite build ran there, the first publish failed with "your app
# built successfully but failed to start" — the server was still installing when the
# check arrived. The deployment runs this as its build step; the workspace runs it from
# start.sh.
set -euo pipefail

cd "$(dirname "$0")/.."

# Replit's Python is Nix-managed and marked EXTERNALLY-MANAGED (PEP 668), so pip refuses
# to install into it without this. The environment-variable form rather than the
# --break-system-packages flag, because a pip too old to know the option ignores an
# unknown variable but fails on an unknown flag. Inside a venv it changes nothing.
export PIP_BREAK_SYSTEM_PACKAGES=1

echo "==> installing backend dependencies"
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
