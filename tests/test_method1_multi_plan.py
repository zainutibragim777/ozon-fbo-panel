"""Several selected products must be combined into one row per cluster."""
from method1_multi_plan import combine_product_allocations

plans = combine_product_allocations({
    "ARTICLE-A": {"East": 10, "West": 20},
    "ARTICLE-B": {"East": 5, "North": 7},
})
by_cluster = {row["Кластер"]: row for row in plans}

assert set(by_cluster) == {"East", "West", "North"}
assert by_cluster["East"]["Отправить, шт."] == 15
assert by_cluster["East"]["items"] == [
    {"product": "ARTICLE-A", "quantity": 10},
    {"product": "ARTICLE-B", "quantity": 5},
]
assert sum(row["Отправить, шт."] for row in plans) == 42
assert len(plans) == 3  # one supply job per cluster, not one per product

print("Method 1 multi-product cluster grouping: PASS")
