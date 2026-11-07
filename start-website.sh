#!/usr/bin/env bash
# Local web server for the Pixel Price dashboard.
# Solves: opening index.html directly (file://) blocks the JSON data fetch.
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
exec python3 -m http.server "${PORT}" --bind 127.0.0.1
