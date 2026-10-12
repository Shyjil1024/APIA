"""Characterization test (APIA, Python/pytest) for legacy_pyapp.inventory.

APIA generated this to PIN THE CURRENT behavior of the untested legacy module
BEFORE any change, so regressions become detectable. These assertions document
what ``inventory.py`` *does today* (observed by running the current code), not
what it *should* do -- including its quirks (e.g. ``apply_discount`` performs no
validation, so a negative percent INCREASES the price).

Shape follows the adapter's characterization_test_template
(.github/skills/apia-test-authoring/templates/python_characterization_test.py.tmpl).
It performs NO network or LLM calls.

Run from this directory (samples/legacy_pyapp) so pyproject's pythonpath applies:
    python -m pytest
"""

import pytest

from legacy_pyapp.inventory import (
    apply_discount,
    restock,
    is_low_stock,
    total_value,
)


# --- apply_discount: pins current (unvalidated) behavior ---------------------


def test_apply_discount_reduces_price():
    # Observed: 100 with a 10% discount -> 90.0
    assert apply_discount(100, 10) == 90.0


def test_apply_discount_zero_percent_is_identity():
    # Observed: a 0% discount returns the price unchanged (as a float).
    assert apply_discount(50, 0) == 50.0


def test_apply_discount_negative_percent_increases_price():
    # QUIRK pinned: no validation on percent, so a negative percent raises the
    # price. Current behavior: 100 with -10% -> 110.0.
    assert apply_discount(100, -10) == 110.0


# --- restock -----------------------------------------------------------------


def test_restock_adds_incoming():
    # Observed: 5 + 3 -> 8.
    assert restock(5, 3) == 8


# --- is_low_stock ------------------------------------------------------------


def test_is_low_stock_at_default_threshold():
    # Observed: level 5 with default threshold 5 is low (<=).
    assert is_low_stock(5) is True


def test_is_low_stock_above_default_threshold():
    # Observed: level 6 with default threshold 5 is not low.
    assert is_low_stock(6) is False


def test_is_low_stock_custom_threshold():
    # Observed: level 3 with threshold 2 is not low (3 <= 2 is False).
    assert is_low_stock(3, 2) is False


# --- total_value -------------------------------------------------------------


def test_total_value_sums_quantity_times_price():
    # Observed: [(2, 3.0), (1, 4.5)] -> 10.5.
    assert total_value([(2, 3.0), (1, 4.5)]) == 10.5


def test_total_value_empty_is_zero():
    # Observed: an empty list totals 0.
    assert total_value([]) == 0
