"""APIA-EVAL FIXTURE — not product code.

Stand-in .apia/scripts module for the ingest_include_apia (S7) scenario. It is
excluded from the default ingest run and included only under include_apia, where
it must appear in the source index as .apia/scripts/foo.py.
"""


def foo():
    """Return a constant marker value."""
    return 42
