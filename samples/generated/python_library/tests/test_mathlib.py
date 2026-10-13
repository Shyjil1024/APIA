"""Core-feature tests for the mathlib library (models the live-generated tests)."""

import pytest

from mathlib import add, clamp, mean


def test_add():
    assert add(2, 3) == 5


def test_mean():
    assert mean([1, 2, 3, 4]) == 2.5


def test_mean_rejects_empty():
    with pytest.raises(ValueError):
        mean([])


def test_clamp():
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0
    assert clamp(99, 0, 10) == 10


def test_clamp_rejects_bad_range():
    with pytest.raises(ValueError):
        clamp(1, 10, 0)
