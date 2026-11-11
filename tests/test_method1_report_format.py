"""Regression for replacing a saved report with the same bytes under a new format."""
import importlib
import os
import tempfile

with tempfile.TemporaryDirectory() as folder:
    os.environ["OZON_METHOD1_DB_PATH"] = folder + "/method1.sqlite3"
    import method1_state as store
    importlib.reload(store)

    data = b"same report bytes"
    digest, changed = store.save_report("sales.xlsx", data)
    assert changed
    signature = store.signature_for({"report": digest, "format": "xlsx"})
    store.save_plan("excel", signature, "SKU-123", [{"Кластер": "Москва"}])

    assert store.save_report("sales.xlsx", data) == (digest, False)
    assert store.get_plan("excel", signature) is not None

    same_digest, changed = store.save_report("sales.csv", data)
    assert changed and same_digest == digest
    assert store.load_report()["name"] == "sales.csv"
    assert store.get_plan("excel", signature) is None

print("Method 1 same-content filename/format replacement: PASS")
