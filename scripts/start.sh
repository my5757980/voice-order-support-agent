#!/usr/bin/env bash
# Boot the whole app as one process on one port: build, then serve.
#
# The frontend is built to static assets and served by the same FastAPI process that
# holds the WebSockets — no CORS, no reverse proxy, no second deploy target. That shape
# was forced by the permitted-platform constraint and turned out to be the simplest
# thing that works.
#
# This is the workspace entry point. A published deployment runs the two halves as
# separate steps (see .replit), because its health check will not wait for a build.
set -euo pipefail

cd "$(dirname "$0")"
bash ./build.sh
exec bash ./serve.sh
