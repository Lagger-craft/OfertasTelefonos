#!/usr/bin/env bash
# Local web server for the Pixel Price dashboard.
# Solves: opening index.html directly (file://) blocks the JSON data fetch.
# Serves static files plus POST /api/run-search so the page can trigger a
# fresh price capture for testing (scripts/website_server.py).
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8000}"
URL="http://localhost:${PORT}/"
echo "Sirviendo la web en ${URL} (Ctrl+C para detener)"
(
  sleep 1
  xdg-open "$URL" 2>/dev/null \
    || chromium --new-window "$URL" 2>/dev/null \
    || firefox "$URL" 2>/dev/null \
    || true
) &
exec python3 scripts/website_server.py "${PORT}"
