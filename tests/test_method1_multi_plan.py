"""Several selected products must be combined into one row per cluster."""
from method1_multi_plan import combine_product_allocations, cluster_product_matrix

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

matrix = cluster_product_matrix(plans)
east = next(row for row in matrix if row["Кластер"] == "East")
assert east == {"Кластер": "East", "ARTICLE-A": 10, "ARTICLE-B": 5}
assert all("Отправить, шт." not in row for row in matrix)

print("Method 1 multi-product cluster grouping: PASS")
