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

def cluster_product_matrix(rows):
    """Make a readable cluster-by-product table without hiding quantities in a sum."""
    products = []
    normalized_rows = []
    for row in rows:
        items = row.get("items") or [{
            "product": row.get("product") or row.get("Товары") or "Товар",
            "quantity": row.get("Отправить, шт.", 0),
        }]
        quantities = {}
        for item in items:
            product = str(item["product"])
            if product not in products:
                products.append(product)
            quantities[product] = quantities.get(product, 0) + int(item["quantity"])
        normalized_rows.append((str(row["Кластер"]), quantities))
    return [
        {"Кластер": cluster, **{product: quantities.get(product, 0) for product in products}}
        for cluster, quantities in normalized_rows
    ]
