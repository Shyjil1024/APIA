"""Core-feature tests for the todo_api web API (models the live-generated tests).

These hit a REAL served endpoint via Flask's test client (an in-process WSGI
request, no network/socket), so the smoke claim "it actually serves HTTP" is
genuinely exercised, not just a unit call to the handler function.
"""

import pytest

from todo_api.app import create_app


@pytest.fixture()
def client():
    app = create_app()
    app.testing = True
    return app.test_client()


def test_health_endpoint_served(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "service": "todo_api"}


def test_items_crud_roundtrip(client):
    assert client.get("/items").get_json() == []
    created = client.post("/items", json={"title": "buy milk"})
    assert created.status_code == 201
    assert created.get_json() == {"id": 1, "title": "buy milk"}
    assert client.get("/items").get_json() == [{"id": 1, "title": "buy milk"}]


def test_items_rejects_missing_title(client):
    resp = client.post("/items", json={})
    assert resp.status_code == 400
    assert "error" in resp.get_json()
