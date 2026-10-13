"""todo_api web-API app (APIA-generated skeleton + core feature).

The skeleton emits a framework-agnostic ``health()`` handler so a smoke test can
run with no web dependency. The ``create_app()`` Flask factory below models the
LIVE Copilot generation step: it mounts ``health()`` on a real ``GET /health``
route plus a tiny in-memory ``/items`` resource, so the service genuinely serves
HTTP (exercised by the smoke test via Flask's test client, no network needed).
"""

from flask import Flask, jsonify, request


def health():
    """Core handler: return the service health payload (framework-agnostic)."""
    return {"status": "ok", "service": "todo_api"}


def create_app():
    """Build and return the Flask app serving the todo_api endpoints."""
    app = Flask(__name__)
    items = []

    @app.get("/health")
    def _health():
        return jsonify(health())

    @app.get("/items")
    def _list_items():
        return jsonify(items)

    @app.post("/items")
    def _add_item():
        payload = request.get_json(silent=True) or {}
        title = payload.get("title")
        if not title:
            return jsonify({"error": "title is required"}), 400
        item = {"id": len(items) + 1, "title": title}
        items.append(item)
        return jsonify(item), 201

    return app


if __name__ == "__main__":  # pragma: no cover - real server entrypoint
    create_app().run()
