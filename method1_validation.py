"""Validation for Method 1 report values before allocation."""
import math


def has_only_finite_numbers(values):
    """Return False for blanks, non-numeric values, NaN, and infinities."""
    try:
        return all(math.isfinite(float(value)) for value in values)
    except (TypeError, ValueError, OverflowError):
        return False
