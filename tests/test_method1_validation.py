"""Method 1 rejects invalid numeric values before calculating an allocation."""
from method1_validation import has_only_finite_numbers

assert has_only_finite_numbers([0, 1, 19.5])
assert has_only_finite_numbers([])
assert not has_only_finite_numbers([float("nan")])
assert not has_only_finite_numbers([float("inf")])
assert not has_only_finite_numbers([float("-inf")])
assert not has_only_finite_numbers(["not a number"])
assert not has_only_finite_numbers([10, None])

print("Method 1 finite numeric input validation: PASS")
