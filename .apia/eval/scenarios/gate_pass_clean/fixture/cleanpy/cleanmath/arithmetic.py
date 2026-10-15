"""APIA-EVAL FIXTURE — not product code.

A handful of trivial pure functions for the gate_pass_clean (S2) scenario.
Engineered to produce ZERO findings from ruff/flake8/bandit/radon so the only
honest gate verdict is PASSED when every required Python scanner has run.
"""


def add(left, right):
    """Return the sum of two numbers."""
    return left + right


def subtract(left, right):
    """Return the difference of two numbers."""
    return left - right


def multiply(left, right):
    """Return the product of two numbers."""
    return left * right


def negate(value):
    """Return the arithmetic negation of a number."""
    return -value
