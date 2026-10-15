"""todo_api web-API app (APIA-generated skeleton + core feature).

The skeleton emits a framework-agnostic ``health()`` handler so a smoke test can
run with no web dependency. The ``handle()`` callable below models the LIVE
Copilot generation step: it dispatches ``GET /health`` plus a tiny in-memory
``/items`` resource through plain stdlib, so the service's request/response
behavior is genuinely exercised (by the feature tests) with NO web framework.
Mounting this on a real socket is a one-liner (see ``make_handler`` /
``__main__``) via ``http.server``; the dependency-free dispatcher keeps the
sample runnable and testable with only the standard library.
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


def health():
    """Core handler: return the service health payload (framework-agnostic)."""
    return {"status": "ok", "service": "todo_api"}


def create_app():
    """Build and return a dependency-free request dispatcher for todo_api.

    Returns a ``handle(method, path, payload=None)`` callable backed by an
    in-memory item store. ``handle`` returns ``(status_code, body)`` where
    ``body`` is a JSON-serializable object, mirroring the HTTP contract
    (``GET /health``, ``GET /items``, ``POST /items``) without any web
    framework or network socket.
    """
    items = []

    def handle(method, path, payload=None):
        if method == "GET" and path == "/health":
            return 200, health()
        if method == "GET" and path == "/items":
            return 200, list(items)
        if method == "POST" and path == "/items":
            title = (payload or {}).get("title")
            if not title:
                return 400, {"error": "title is required"}
            item = {"id": len(items) + 1, "title": title}
            items.append(item)
            return 201, item
        return 404, {"error": "not found"}

    return handle


def make_handler(dispatch):
    """Adapt the stdlib dispatcher to an ``http.server`` request handler.

    Lets the same framework-agnostic dispatch back a real socket server without
    any third-party dependency (used by ``__main__`` below).
    """

    class _Handler(BaseHTTPRequestHandler):
        def _respond(self, status, body):
            raw = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):  # noqa: N802 - stdlib handler naming
            status, body = dispatch("GET", self.path)
            self._respond(status, body)

        def do_POST(self):  # noqa: N802 - stdlib handler naming
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {}
            status, body = dispatch("POST", self.path, payload)
            self._respond(status, body)

        def log_message(self, *args):  # pragma: no cover - silence test noise
            pass

    return _Handler


if __name__ == "__main__":  # pragma: no cover - real server entrypoint
    HTTPServer(("127.0.0.1", 8000), make_handler(create_app())).serve_forever()
