"""Method 1 persistence regression; no Ozon API calls or orders."""
import hashlib
import importlib
import os
import tempfile

with tempfile.TemporaryDirectory() as folder:
    os.environ["OZON_METHOD1_DB_PATH"] = folder + "/method1.sqlite3"
    import method1_state as store
    importlib.reload(store)

    excel = b"saved excel content"
    hash1, updated = store.save_report("sales.xlsx", excel)
    assert updated and hash1 == hashlib.sha256(excel).hexdigest()
    assert store.load_report()["content"] == excel

    store.set_setting("method1_sku", "SKU-123")
    store.set_setting("method1_quantity", 1500)
    store.set_setting("method1_excluded", ["Самара", "Махачкала"])
    settings = {
        "file": hash1, "product": "SKU-123",
        "columns": ["SKU", "Кластер", "Продажи", "Остатки"],
        "quantity": 1500, "minimum": 19,
        "excluded": ["Махачкала", "Самара"],
    }
    sig = store.signature_for(settings)
    plan = [{"Кластер": "Москва", "Отправить, шт.": 1000},
            {"Кластер": "Ростов", "Отправить, шт.": 500}]
    store.save_plan("excel", sig, "SKU-123", plan)
    assert store.get_plan("excel", sig)["rows"] == plan

    # New Streamlit session: no session_state survives. DB must restore it.
    importlib.reload(store)
    assert store.load_report()["sha256"] == hash1
    assert store.get_setting("method1_sku") == "SKU-123"
    assert store.get_setting("method1_quantity") == 1500
    assert store.get_setting("method1_excluded") == ["Самара", "Махачкала"]
    assert sum(x["Отправить, шт."] for x in store.get_plan("excel", sig)["rows"]) == 1500

    # Never accidentally display an outdated plan when inputs change.
    changed = dict(settings)
    changed["quantity"] = 2500
    assert store.get_plan("excel", store.signature_for(changed)) is None
    assert store.save_report("sales.xlsx", excel) == (hash1, False)
    assert store.get_plan("excel", sig) is not None
    hash2, updated = store.save_report("sales-new.xlsx", b"new excel data")
    assert updated and hash2 != hash1
    assert store.get_plan("excel", sig) is None

print("Method 1 Excel, settings, saved plan and invalidation after refresh: PASS")
