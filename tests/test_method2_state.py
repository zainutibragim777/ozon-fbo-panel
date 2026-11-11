"""Offline persistence regression: refresh cannot drop the Ozon queue."""
import hashlib
import importlib
import os
import tempfile

with tempfile.TemporaryDirectory() as temp:
    os.environ["OZON_METHOD2_DB_PATH"] = temp + "/ozon-method2-test.sqlite3"
    import method2_state as store
    importlib.reload(store)

    assert store.pref_get("last_sku") is None
    store.pref_set("last_sku", "4357898852")
    assert store.pref_get("last_sku") == "4357898852"
    excel = b"sample uploaded data"
    digest = hashlib.sha256(excel).hexdigest()
    store.file_save("4357898852", excel, digest, 1650)
    assert store.file_get("4357898852") == (excel, digest, 1650)

    identity = {"sku": 4357898852, "article": "Test", "quantity": 1650}
    rows = [
        {"Кластер": "Москва, МО и Дальние регионы", "ID Ozon": 4039, "Отправить, шт.": 350},
        {"Кластер": "Ростов", "ID Ozon": 4071, "Отправить, шт.": 1300},
    ]
    window = ("2026-11-12", "2026-11-16", "08:00", "22:00")
    plan, created = store.start_plan("abc", identity, rows, window)
    assert created and len(plan["jobs"]) == 2

    # Persist a submitted order BEFORE calling Ozon's non-idempotent create.
    one = plan["jobs"][0]
    one["status"] = "SUBMITTING"
    one["attempts"] = 1
    store.save_job(one)

    # Simulate a brand-new Streamlit session and module reload.
    importlib.reload(store)
    found = store.read_plan()
    assert len(found["jobs"]) == 2
    assert found["jobs"][0]["status"] == "SUBMITTING"
    assert store.pref_get("last_sku") == "4357898852"
    assert store.file_get("4357898852")[0] == excel

    # Clicking "Create" again must NEVER add the same jobs twice.
    same, created_again = store.start_plan("abc", identity, rows, window)
    assert not created_again
    assert same["id"] == plan["id"]
    assert len(same["jobs"]) == 2

    one = same["jobs"][0]
    one["status"] = "CREATED"
    one["order_id"] = 987654321
    store.save_job(one)
    store.stop_queue()
    finished = store.read_plan()
    assert finished["jobs"][0]["status"] == "CREATED"
    assert finished["jobs"][0]["order_id"] == 987654321
    assert finished["jobs"][1]["status"] == "STOPPED"

print("Method 2 refresh, queue, Excel, SKU, and non-duplicate persistence: PASS")
