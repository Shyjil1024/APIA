"""APIA-EVAL FIXTURE — not product code.

Passing tests for the lint-clean gate_pass_clean (S2) fixture package.
"""

from cleanmath.arithmetic import add, multiply, negate, subtract


def test_add():
    assert add(2, 3) == 5


def test_subtract():
    assert subtract(5, 3) == 2


def test_multiply():
    assert multiply(4, 5) == 20


def test_negate():
    assert negate(7) == -7
