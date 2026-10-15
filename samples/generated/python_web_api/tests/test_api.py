"""Core-feature tests for the todo_api web API (models the live-generated tests).

These exercise the REAL request dispatcher (``create_app()`` returns a
framework-agnostic ``handle(method, path, payload)`` callable backed by an
in-memory store), so the service's HTTP contract -- health payload, item
create/list, empty-title rejection -- is genuinely exercised with no web
framework and no network socket.
"""

import pytest


@pytest.fixture()
def handle():
    from todo_api.app import create_app

    return create_app()


def test_health_endpoint_served(handle):
    status, body = handle("GET", "/health")
    assert status == 200
    assert body == {"status": "ok", "service": "todo_api"}


def test_items_crud_roundtrip(handle):
    assert handle("GET", "/items") == (200, [])
    status, created = handle("POST", "/items", {"title": "buy milk"})
    assert status == 201
    assert created == {"id": 1, "title": "buy milk"}
    assert handle("GET", "/items") == (200, [{"id": 1, "title": "buy milk"}])


def test_items_rejects_missing_title(handle):
    status, body = handle("POST", "/items", {})
    assert status == 400
    assert "error" in body
