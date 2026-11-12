"""Small pure helpers for assembling one cluster order with many products."""


def combine_product_allocations(product_allocations):
    """Return one row per cluster, with all product quantities for that cluster."""
    by_cluster = {}
    for product, allocation in product_allocations.items():
        for cluster, amount in allocation.items():
            amount = int(amount)
            if amount <= 0:
                continue
            by_cluster.setdefault(str(cluster), []).append({
                "product": str(product),
                "quantity": amount,
            })
    rows = []
    for cluster, items in by_cluster.items():
        rows.append({
            "Кластер": cluster,
            "Товары": "; ".join(f"{item['product']} × {item['quantity']}" for item in items),
            "Отправить, шт.": sum(item["quantity"] for item in items),
            "items": items,
        })
    return sorted(rows, key=lambda row: (-row["Отправить, шт."], row["Кластер"]))
