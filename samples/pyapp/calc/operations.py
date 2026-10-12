"""Basic arithmetic operations.

Intentionally small and well-tested so it can serve as the regression-guard
baseline for the APIA ingest -> clarify -> modify -> test -> apply loop.
"""


def add(a, b):
    """Return the sum of two numbers."""
    return a + b


def subtract(a, b):
    """Return the difference of two numbers (a - b)."""
    return a - b


def divide(a, b):
    """Return the quotient a / b.

    Raises:
        ZeroDivisionError: if ``b`` is zero.
    """
    # APIA worked-example change (FEAT-004): the error message is made more
    # descriptive while the behavior (still raising ZeroDivisionError) is
    # preserved, so the existing regression tests stay green.
    if b == 0:
        raise ZeroDivisionError("cannot divide {!r} by zero".format(a))
    return a / b


def multiply(a, b):
    """Return the product of two numbers.

    APIA worked-example NEW behavior (FEAT-004): added alongside the change to
    ``divide`` and covered by a new test (tests/test_multiply.py).
    """
    return a * b
