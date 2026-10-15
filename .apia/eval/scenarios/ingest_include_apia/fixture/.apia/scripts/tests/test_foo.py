"""APIA-EVAL FIXTURE — not product code.

Stand-in .apia/scripts test for the ingest_include_apia (S7) scenario. It is the
single extra test that include_apia adds, giving the fixed test_count_delta of 1
and the declared path .apia/scripts/tests/test_foo.py.
"""

from foo import foo


def test_foo():
    assert foo() == 42
