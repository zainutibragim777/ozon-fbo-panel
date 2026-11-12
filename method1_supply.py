"""Method 1's own one-supply-per-cluster queue and worker."""
import time
import json
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import streamlit as st

import method1_supply_state as store

BASE = "https://api-seller.ozon.ru"
ORIGIN_ID = 1020005035056500
ORIGIN_TYPE = "SORTING_CENTER"
MOSCOW = timezone(timedelta(hours=3))
RETRY_SECONDS = 180


def ozon(client, key, endpoint, body, timeout=40):
    request = urllib.request.Request(
        BASE + endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Client-Id": client, "Api-Key": key, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _date_hours_allowed(slot, start_date, end_date, time_from, time_to):
    try:
        begins = datetime.fromisoformat(str(slot["from_in_timezone"]))
        ends = datetime.fromisoformat(str(slot["to_in_timezone"]))
    except (KeyError, TypeError, ValueError):
        return False
    comparison_start = begins if begins.tzinfo is not None else begins.replace(tzinfo=MOSCOW)
    if not start_date <= begins.date() <= end_date or comparison_start <= datetime.now(MOSCOW):
        return False
    end_hour = ends.strftime("%H:%M") if ends.date() == begins.date() else "24:00"
    return time_from <= begins.strftime("%H:%M") and end_hour <= time_to


def _job_items(job):
    """Read new multi-item jobs and old single-SKU saved jobs."""
    return store.items_for_job(job)


def _new_draft(client, key, job):
    created = ozon(client, key, "/v1/draft/crossdock/create", {
        "cluster_info": {
            "macrolocal_cluster_id": int(job["cluster_id"]),
            "items": _job_items(job),
        },
        "deletion_sku_mode": "FULL",
        "delivery_info": {
            "drop_off_warehouse": {"warehouse_id": ORIGIN_ID, "warehouse_type": ORIGIN_TYPE},
            "type": "DROPOFF",
        },
    })
    draft_id = int(created.get("draft_id") or 0)
    if not draft_id or created.get("errors"):
        return None, None, "Черновик отклонён: " + str(created.get("errors") or [])[:300]
    info = {}
    for _ in range(8):
        time.sleep(2.5)
        info = ozon(client, key, "/v2/draft/create/info", {"draft_id": draft_id})
        if info.get("status") in ("SUCCESS", "FAILED"):
            break
    if info.get("status") != "SUCCESS" or info.get("errors"):
        return None, None, "Проверка черновика: " + str(info.get("errors") or info.get("status"))[:300]
    selected = None
    for cluster in info.get("clusters", []) or []:
        if int(cluster.get("macrolocal_cluster_id") or 0) != int(job["cluster_id"]):
            continue
        for warehouse in cluster.get("warehouses", []) or []:
            if (warehouse.get("availability_status") or {}).get("state") != "FULL_AVAILABLE":
                continue
            storage = warehouse.get("storage_warehouse") or {}
            selected = {
                "macrolocal_cluster_id": int(job["cluster_id"]),
                "storage_warehouse_id": int(storage.get("warehouse_id") or 0),
            }
            break
        if selected:
            break
    if selected is None:
        return None, None, "Кластер недоступен в Ozon для выбранного состава товаров"
    return draft_id, selected, ""


def _check_order_status(client, key, job):
    try:
        response = ozon(client, key, "/v2/draft/supply/create/status", {
            "draft_id": int(job["creation_draft_id"]),
        })
        state = str(response.get("status") or "UNSPECIFIED")
        if state == "SUCCESS" and int(response.get("order_id") or 0) > 0:
            job["order_id"] = int(response["order_id"])
            job["status"] = "CREATED"
            job["reason"] = "Заявка создана в Ozon"
        elif state == "FAILED":
            job["status"] = "NEEDS_REVIEW"
            job["reason"] = "Ozon отклонил заявку: " + str(response.get("error_reasons") or [])
        else:
            job["status"] = "VERIFYING"
            job["reason"] = "Ждём подтверждения Ozon"
    except Exception as error:
        job["status"] = "VERIFYING"
        job["reason"] = "Статус требует повторной проверки: " + str(error)[:120]


def create_individual_supply(client, key, job, start_date, end_date, time_from, time_to, before_submit=None):
    if job["status"] in ("CREATED", "VERIFYING", "NEEDS_REVIEW", "SUBMITTING"):
        return
    job["attempts"] += 1
    job["last_attempt"] = datetime.now(MOSCOW).isoformat()
    job["status"] = "CHECKING"
    try:
        draft_id, selected, reason = _new_draft(client, key, job)
        if not draft_id:
            job["status"] = "RETRY"
            job["reason"] = reason
            return
        response = ozon(client, key, "/v2/draft/timeslot/info", {
            "date_from": start_date.isoformat(), "date_to": end_date.isoformat(),
            "draft_id": draft_id, "supply_type": "CROSSDOCK",
            "selected_cluster_warehouses": [selected],
        })
        raw = (response.get("result") or {}).get("drop_off_warehouse_timeslots") or {}
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        if not isinstance(raw, dict):
            job["status"] = "RETRY"
            job["reason"] = "Ozon не вернул календарь."
            return
        offers = [slot for day in raw.get("days", []) or []
                  for slot in day.get("timeslots", []) or []
                  if _date_hours_allowed(slot, start_date, end_date, time_from, time_to)]
        offers.sort(key=lambda slot: str(slot.get("from_in_timezone")))
        if not offers:
            job["status"] = "RETRY"
            job["reason"] = "Свободных интервалов нет в выбранном диапазоне."
            return
        slot = offers[0]
        job["timeslot"] = {
            "from": str(slot["from_in_timezone"]), "to": str(slot["to_in_timezone"]),
        }
        # Persist this guard before the non-idempotent Ozon request.
        job["status"] = "SUBMITTING"
        job["reason"] = "Отправляем заявку; повторы заблокированы до проверки результата."
        if before_submit is not None:
            before_submit(job)
        result = ozon(client, key, "/v2/draft/supply/create", {
            "draft_id": draft_id,
            "selected_cluster_warehouses": [selected],
            "timeslot": {"from_in_timezone": job["timeslot"]["from"],
                         "to_in_timezone": job["timeslot"]["to"]},
            "supply_type": "CROSSDOCK",
        })
        errors = result.get("error_reasons") or []
        operation = int(result.get("draft_id") or 0)
        if errors or not operation:
            if "TIMESLOT_NOT_AVAILABLE" in errors:
                job["status"] = "RETRY"
                job["reason"] = "Слот уже занят — проверим другой."
            else:
                job["status"] = "NEEDS_REVIEW"
                job["reason"] = "Ozon вернул ошибку создания: " + str(errors)[:250]
            return
        job["creation_draft_id"] = operation
        job["status"] = "VERIFYING"
        if before_submit is not None:
            before_submit(job)
        for _ in range(5):
            time.sleep(2)
            _check_order_status(client, key, job)
            if job["status"] in ("CREATED", "NEEDS_REVIEW"):
                break
    except urllib.error.HTTPError as error:
        if job["status"] == "SUBMITTING":
            job["status"] = "NEEDS_REVIEW"
            job["reason"] = f"Ответ при создании неизвестен (HTTP {error.code}). Повтор запрещён."
        elif error.code in (429, 500, 502, 503, 504):
            job["status"] = "RETRY"
            job["reason"] = f"Ozon HTTP {error.code}; повторим позже."
        else:
            job["status"] = "NEEDS_REVIEW"
            job["reason"] = f"Ozon HTTP {error.code}; нужна проверка."
    except Exception as error:
        if job["status"] == "SUBMITTING":
            job["status"] = "NEEDS_REVIEW"
            job["reason"] = "Неясен результат отправки; повтор запрещён: " + str(error)[:100]
        else:
            job["status"] = "RETRY"
            job["reason"] = str(error)[:200]


def process_once(client, key):
    plan = store.read_plan()
    if not plan:
        return
    jobs = plan["jobs"]
    # A crash during draft/slot checks can be retried safely. An interrupted
    # order submission is ambiguous and must never be retried automatically.
    for job in jobs:
        age = time.time() - float(job.get("last_attempt_epoch") or time.time())
        if job.get("status") == "CHECKING" and age > 240:
            job["status"] = "RETRY"
            job["next_retry_at"] = time.time() + RETRY_SECONDS
            job["reason"] = "Восстановлена проверка после сбоя (заявка ещё не отправлялась)"
            store.save_job(job)
        elif job.get("status") == "SUBMITTING" and age > 240:
            job["status"] = "NEEDS_REVIEW"
            job["reason"] = "Ответ Ozon после отправки неизвестен; повтор запрещён до сверки"
            store.save_job(job)
    verifying = next((job for job in jobs if job.get("status") == "VERIFYING" and job.get("creation_draft_id")
                     and time.time() - float(job.get("last_verify_epoch") or 0) > 30), None)
    if verifying:
        verifying["last_verify_epoch"] = time.time()
        try:
            _check_order_status(client, key, verifying)
        finally:
            store.save_job(verifying)
        return
    if plan["stopped"]:
        return
    now = time.time()
    waiting = next((job for job in jobs if job["status"] == "WAITING"), None)
    retries = sorted((job for job in jobs if job["status"] == "RETRY"
                      and now >= float(job.get("next_retry_at") or 0)),
                     key=lambda job: float(job.get("last_attempt_epoch") or 0))
    job = waiting or (retries[0] if retries else None)
    if job is None or not store.reserve_attempt():
        return
    job["last_attempt_epoch"] = time.time()
    job["attempts"] = int(job.get("attempts") or 0) + 1
    job["status"] = "CHECKING"
    job["reason"] = "Проверяем Ozon"
    store.save_job(job)

    def checkpoint(current):
        store.save_job(current)

    window = plan["window"]
    try:
        create_individual_supply(
            client, key, job, date.fromisoformat(window[0]), date.fromisoformat(window[1]),
            window[2], window[3], before_submit=checkpoint,
        )
    except Exception as error:
        if job["status"] in ("SUBMITTING", "VERIFYING"):
            job["status"] = "NEEDS_REVIEW"
            job["reason"] = "Неясен результат создания; повтор запрещён: " + str(error)[:150]
        else:
            job["status"] = "RETRY"
            job["reason"] = str(error)[:150]
    finally:
        if job["status"] == "RETRY":
            job["next_retry_at"] = time.time() + RETRY_SECONDS
        store.save_job(job)


@st.fragment(run_every=30)
def render_method1_queue():
    plan = store.read_plan()
    if not plan:
        return
    st.subheader("Поставки Метода 1")
    st.caption("Эта очередь сохраняется отдельно от Метода 2.")
    labels = {
        "WAITING": "В очереди", "RETRY": "Повтор позже", "CHECKING": "Проверяем Ozon",
        "SUBMITTING": "Отправляется", "VERIFYING": "Ждём номер заявки", "CREATED": "Создана",
        "NEEDS_REVIEW": "Нужна проверка", "STOPPED": "Остановлена",
    }
    identity_products = plan["identity"].get("products") or [{
        "article": plan["identity"].get("article", ""),
        "sku": plan["identity"].get("sku", ""),
    }]
    product_names = ", ".join(
        str(item.get("article") or item.get("name") or item.get("sku")) for item in identity_products
    )
    st.write(f"Товары: **{product_names}** · {sum(j['quantity'] for j in plan['jobs'])} шт.")
    st.dataframe(pd.DataFrame([{
        "Кластер": job["name"],
        "Товары": "; ".join(
            f"{item.get('product') or item.get('sku')} × {item['quantity']}"
            for item in (job.get("items") or [{"sku": job.get("sku"), "quantity": job["quantity"]}])
        ),
        "Шт.": job["quantity"],
        "Статус": labels.get(job["status"], job["status"]),
        "Номер заявки": job.get("order_id") or "—", "Примечание": job.get("reason") or "",
    } for job in plan["jobs"]]), hide_index=True, use_container_width=True)
    created = sum(job["status"] == "CREATED" for job in plan["jobs"])
    st.write(f"Создано **{created} из {len(plan['jobs'])}** направлений.")
    if any(job["status"] in ("WAITING", "RETRY") for job in plan["jobs"]) and not plan["stopped"]:
        if st.button("Остановить очередь Метода 1", key="method1_stop_queue"):
            store.stop_queue()
            st.rerun()
    client = str(st.secrets.get("OZON_CLIENT_ID") or "")
    key = str(st.secrets.get("OZON_API_KEY") or "")
    if client and key:
        process_once(client, key)
