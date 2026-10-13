from todo_api.app import health


def test_health():
    # Smoke test: the health handler returns an ok payload (framework-agnostic).
    assert health()["status"] == "ok"
