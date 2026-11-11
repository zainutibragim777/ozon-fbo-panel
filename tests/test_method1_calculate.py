"""Allocation boundary tests without importing the live Streamlit app."""
import ast
import math
from pathlib import Path

tree = ast.parse(Path("streamlit_app.py").read_text(encoding="utf-8"))
calculate_node = next(
    node for node in tree.body
    if isinstance(node, ast.FunctionDef) and node.name == "calculate"
)
isolated = ast.Module(body=[calculate_node], type_ignores=[])
namespace = {"math": math}
exec(compile(ast.fix_missing_locations(isolated), "streamlit_app.py", "exec"), namespace)
calculate = namespace["calculate"]


def check_plan(rows, quantity, minimum):
    plan = calculate(rows, quantity, minimum)
    assert sum(plan.values()) == quantity, plan
    assert all(value >= minimum for value in plan.values()), plan
    return plan


assert calculate([], 0, 19) == {}
try:
    calculate([], 100, 19)
except ValueError:
    pass
else:
    raise AssertionError("No demand must not produce an allocation")

try:
    calculate([("Москва", 1, 0)], 18, 19)
except ValueError:
    pass
else:
    raise AssertionError("Quantity below the minimum must be rejected")

equal = check_plan([("A", 1, 0), ("B", 1, 0), ("C", 1, 0)], 100, 19)
assert sorted(equal.values()) == [33, 33, 34], equal

stock_adjusted = check_plan(
    [("Москва", 10, 0), ("Казань", 10, 1000)], 1000, 19
)
assert stock_adjusted == {"Москва": 1000}, stock_adjusted

small_cluster_removed = check_plan(
    [("Высокий спрос", 100, 0), ("Низкий спрос", 1, 0)], 200, 19
)
assert small_cluster_removed == {"Высокий спрос": 200}, small_cluster_removed

print("Method 1 allocation boundaries: PASS")
