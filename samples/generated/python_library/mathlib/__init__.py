"""mathlib library (APIA-generated skeleton + core feature).

The skeleton (the importable package exposing a ``version()`` stub) is emitted
deterministically by scaffold.py. The small math helpers below model the LIVE
Copilot generation step: a minimal, idiomatic public API so the importable
library "actually works", not just imports.
"""


def version():
    """Return the package version string."""
    return "0.1.0"


def add(a, b):
    """Return the sum of two numbers."""
    return a + b


def mean(values):
    """Return the arithmetic mean of a non-empty sequence of numbers."""
    values = list(values)
    if not values:
        raise ValueError("mean() requires at least one value")
    return sum(values) / len(values)


def clamp(value, low, high):
    """Clamp ``value`` into the inclusive range [low, high]."""
    if low > high:
        raise ValueError("low must not exceed high")
    return max(low, min(value, high))
