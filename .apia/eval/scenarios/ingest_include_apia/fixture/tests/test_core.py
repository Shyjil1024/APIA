"""APIA-EVAL FIXTURE — not product code.

Ordinary pytest test for the ingest_include_apia (S7) scenario.
"""

from widget.core import greet


def test_greet():
    assert greet("world") == "hello world"
