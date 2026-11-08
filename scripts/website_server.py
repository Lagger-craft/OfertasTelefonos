"""Local website server with a run-search endpoint.

Serves the static dashboard (index.html, script.js, styles.css, data/) and
adds POST /api/run-search, which executes scripts/update_prices.py once and
returns its outcome as JSON so the page can trigger a fresh price capture
for testing. Localhost only: the handler must never be exposed beyond the
bound loopback address.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COLLECTOR_SCRIPT = REPO_ROOT / "scripts" / "update_prices.py"
DEFAULT_SEARCH_TIMEOUT_S = 240

_search_lock = False  # only one collection at a time


def run_search(collector_script: str = str(COLLECTOR_SCRIPT), timeout: int = DEFAULT_SEARCH_TIMEOUT_S,
               extra_args: list[str] | None = None) -> dict:
    """Run the price collector once and return a JSON-serializable outcome."""
    started = time.monotonic()
    tail = ""
    exit_code: int | None = None
    try:
        completed = subprocess.run(
            [sys.executable, collector_script, *(extra_args or [])],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=timeout,
        )
        exit_code = completed.returncode
        output = (completed.stdout or "") + (completed.stderr or "")
        tail = "\n".join(output.strip().splitlines()[-12:])
    except subprocess.TimeoutExpired:
        tail = f"La búsqueda superó {timeout} s y se canceló."
    except Exception as error:  # report anything unexpected without crashing the handler
        tail = f"Error al iniciar el collector: {error}"
    duration = round(time.monotonic() - started, 1)
    return {
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "duration_s": duration,
        "tail": tail,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


class WebsiteHandler(SimpleHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 (stdlib naming)
        global _search_lock
        if self.path != "/api/run-search":
            self._send_json(404, {"ok": False, "tail": "Endpoint no reconocido."})
            return
        if _search_lock:
            self._send_json(409, {"ok": False, "tail": "Ya hay una búsqueda ejecutándose; esperá a que termine."})
            return
        _search_lock = True
        try:
            self._send_json(200, run_search())
        finally:
            _search_lock = False

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quieter console for the dev server
        pass


def build_server(port: int, directory: str = str(REPO_ROOT)) -> ThreadingHTTPServer:
    handler = partial(WebsiteHandler, directory=directory)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    port = int(argv[0]) if argv else 8000
    server = build_server(port)
    print(f"Sirviendo la web en http://localhost:{port}/ (Ctrl+C para detener)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
