"""New-behavior test for the APIA safe change on legacy_pyapp (FEAT-004).

This covers ``reorder_quantity`` -- the function ADDED to inventory.py after the
characterization tests pinned the module's current behavior. Together with the
characterization suite (which must stay green), this proves the legacy
no-tests path: pin current behavior, make a safe change, verify both.

It performs NO network or LLM calls.
"""

from legacy_pyapp.inventory import reorder_quantity


def test_reorder_when_low_stock():
    # level 3 is low (<= default threshold 5) -> reorder up to target 10.
    assert reorder_quantity(3) == 7


def test_reorder_when_not_low():
    # level 8 is above the default threshold -> nothing to reorder.
    assert reorder_quantity(8) == 0


def test_reorder_custom_target():
    assert reorder_quantity(2, threshold=5, target=20) == 18


def test_reorder_at_or_above_target_is_zero():
    # Low stock but already at the target -> no reorder.
    assert reorder_quantity(10, threshold=12, target=10) == 0
