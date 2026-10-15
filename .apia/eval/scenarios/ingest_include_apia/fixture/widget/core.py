"""APIA-EVAL FIXTURE — not product code.

One ordinary module for the ingest_include_apia (S7) scenario, so that the
default ingest run (include_apia=False) has at least one source and test and the
include_apia run adds exactly the declared .apia/scripts paths.
"""


def greet(name):
    """Return a greeting for the given name."""
    return "hello " + name
