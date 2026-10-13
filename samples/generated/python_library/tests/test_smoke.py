from mathlib import version


def test_version():
    # Smoke test: the generated package is importable and exposes its API stub.
    assert version() == "0.1.0"
