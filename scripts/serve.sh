#!/usr/bin/env bash
# Start the server and nothing else, so `/` answers within a second of this running.
#
# No seeding step: app.py seeds on import, idempotently, so the fixtures exist on every
# boot — including a fresh deployment container whose database starts empty.
set -euo pipefail

cd "$(dirname "$0")/../backend"
echo "==> serving on :${PORT:-8000}"
exec python -m uvicorn src.app:app --host 0.0.0.0 --port "${PORT:-8000}"
