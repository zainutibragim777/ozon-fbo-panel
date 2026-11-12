"""Method 1 has a durable queue isolated from Method 2's queue."""
import importlib
import os
import tempfile

with tempfile.TemporaryDirectory() as folder:
    os.environ["OZON_METHOD1_SUPPLY_DB_PATH"] = folder + "/method1-supplies.sqlite3"
    import method1_supply_state as store
    importlib.reload(store)

    identity = {"sku": 12345, "article": "ARTICLE-A", "quantity": 40}
    rows = [
        {"Кластер": "Москва", "Отправить, шт.": 29, "ID Ozon": 101, "items": [
            {"product": "ARTICLE-A", "sku": 12345, "quantity": 20},
            {"product": "ARTICLE-A variant", "sku": 12345, "quantity": 4},
            {"product": "ARTICLE-B", "sku": 99999, "quantity": 5},
        ]},
        # Older single-product rows remain readable as before.
        {"Кластер": "Урал", "Отправить, шт.": 11, "ID Ozon": 202},
    ]
    window = ["2026-11-15", "2026-11-20", "08:00", "22:00"]
    plan, created = store.start_plan("method1-signature", identity, rows, window)
    assert created
    assert len(plan["jobs"]) == 2
    assert sum(job["quantity"] for job in plan["jobs"]) == 40
    assert plan["jobs"][0]["items"] == [
        {"sku": 12345, "product": "ARTICLE-A, ARTICLE-A variant", "quantity": 24},
        {"sku": 99999, "product": "ARTICLE-B", "quantity": 5},
    ]
    assert plan["jobs"][1]["items"] == [
        {"sku": 12345, "product": "ARTICLE-A", "quantity": 11},
    ]
    assert store.items_for_job(plan["jobs"][0]) == [
        {"sku": 12345, "quantity": 24},
        {"sku": 99999, "quantity": 5},
    ]
    assert store.items_for_job({"sku": 54321, "quantity": 7}) == [
        {"sku": 54321, "quantity": 7},
    ]
    assert all(job["status"] == "WAITING" for job in plan["jobs"])

    # Refreshing reads Method 1's own durable plan. It does not use Method 2's DB.
    importlib.reload(store)
    restored = store.read_plan()
    assert restored["identity"] == identity
    assert restored["window"] == window
    assert len(restored["jobs"]) == 2
    assert restored["jobs"][0]["items"][1]["sku"] == 99999
    duplicate, created_again = store.start_plan("another-signature", identity, rows, window)
    assert not created_again
    assert duplicate["id"] == restored["id"]

    store.stop_queue()
    stopped = store.read_plan()
    assert stopped["stopped"] == 1
    assert all(job["status"] == "STOPPED" for job in stopped["jobs"])

print("Method 1 supply queue persistence and duplicate protection: PASS")
