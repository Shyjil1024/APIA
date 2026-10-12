"""A small, untested legacy module.

No accompanying tests exist on purpose: this is the characterization-test
target. Behavior here (including its quirks) is the current ground truth that
characterization tests must pin before APIA modifies anything.
"""


def apply_discount(price, percent):
    """Apply a percentage discount to a price.

    Note: performs no validation on ``percent`` (legacy behavior kept as-is).
    """
    return price - (price * percent / 100.0)


def restock(current, incoming):
    """Return the new stock level after receiving an incoming shipment."""
    return current + incoming


def is_low_stock(level, threshold=5):
    """Return True when the stock ``level`` is at or below ``threshold``."""
    return level <= threshold


def total_value(items):
    """Return the total value of a list of (quantity, unit_price) tuples."""
    return sum(qty * price for qty, price in items)


def reorder_quantity(level, threshold=5, target=10):
    """Return how many units to reorder to reach ``target`` when stock is low.

    APIA safe-change ADDED behavior (FEAT-004): a NEW function added without
    altering any existing function, so the characterization tests that pin the
    current behavior of ``apply_discount``/``restock``/``is_low_stock``/
    ``total_value`` stay green. Covered by tests/test_reorder.py.

    Returns 0 when stock is not low (``is_low_stock`` is False) or already at/
    above ``target``.
    """
    if not is_low_stock(level, threshold):
        return 0
    return max(0, target - level)
