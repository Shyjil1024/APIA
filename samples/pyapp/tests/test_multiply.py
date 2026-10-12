"""New-behavior test for the APIA worked-example change (FEAT-004).

This test covers ``multiply`` -- the function added in the same change that
modified ``divide``'s error message. The existing suite (test_operations.py)
remains the regression guard; this file proves the NEW behavior.
"""

from calc.operations import multiply


def test_multiply_positives():
    assert multiply(3, 4) == 12


def test_multiply_by_zero():
    assert multiply(5, 0) == 0


def test_multiply_negatives():
    assert multiply(-2, -3) == 6
