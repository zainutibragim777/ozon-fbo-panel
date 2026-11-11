"""Offline smoke tests: no real Ozon network requests."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import pandas as pd
import method2_supply as m2

raw = pd.DataFrame([
    ["Название отчёта", None],
    ["Кластер доставки", "Доля в категории"],
    ["Пояснение", "Процент от общей суммы заказов — с учётом примененных фильтров"],
    ["Москва, МО и Дальние регионы", .6],
    ["Самара", .1],
    ["Махачкала", .1],
    ["Ростов", .2],
])
buf = BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as writer:
    raw.to_excel(writer, index=False, header=False)
good, excluded = m2.parse_geography(buf.getvalue())
assert len(good) == 2
assert len(excluded) == 2
assert round(good["Доля, %"].sum(), 2) == 80.0
assert m2._clean(" ТЮМЕНЬ  Ё ") == "тюмень е"

today = datetime.now(m2.MOSCOW).date() + timedelta(days=2)
at = today.isoformat()
job = {
    "sku": 987654321, "quantity": 25, "cluster_id": 4070,
    "name": "Пермь", "status": "WAITING", "attempts": 0, "reason": "",
    "order_id": None,
}
calls = []
def fake_api(client, api_key, endpoint, body, timeout=40):
    calls.append((endpoint, body))
    if endpoint == "/v1/draft/crossdock/create":
        assert len(body["cluster_info"]["items"]) == 1
        assert body["cluster_info"]["items"][0]["sku"] == 987654321
        assert body["cluster_info"]["items"][0]["quantity"] == 25
        return {"draft_id": 1111, "errors": []}
    if endpoint == "/v2/draft/create/info":
        return {"status": "SUCCESS", "errors": [], "clusters": [{
            "macrolocal_cluster_id": 4070,
            "warehouses": [{"availability_status": {"state": "FULL_AVAILABLE"}, "storage_warehouse": None}]
        }]}
    if endpoint == "/v2/draft/timeslot/info":
        assert body["supply_type"] == "CROSSDOCK"
        return {"result": {"drop_off_warehouse_timeslots": {
            "warehouse_timezone": "Europe/Moscow",
            "days": [{"timeslots": [{
                "from_in_timezone": f"{at}T10:00:00",
                "to_in_timezone": f"{at}T11:00:00",
            }]}]}}}
    if endpoint == "/v2/draft/supply/create":
        assert body["supply_type"] == "CROSSDOCK"
        assert body["selected_cluster_warehouses"] == [
            {"macrolocal_cluster_id": 4070, "storage_warehouse_id": 0}
        ]
        assert body["timeslot"]["from_in_timezone"] == f"{at}T10:00:00"
        return {"draft_id": 2222, "error_reasons": []}
    if endpoint == "/v2/draft/supply/create/status":
        return {"status": "SUCCESS", "order_id": 3333, "error_reasons": []}
    raise AssertionError(endpoint)

m2.ozon = fake_api
m2.time.sleep = lambda _: None
m2.create_individual_supply("fake", "fake", job, today, today, "08:00", "22:00")
assert job["status"] == "CREATED", job
assert job["order_id"] == 3333, job
assert sum(ep == "/v2/draft/supply/create" for ep, _ in calls) == 1
print("Method 2 allocation/real-supply payload smoke test: PASS")
