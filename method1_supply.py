"""Method 1's own one-supply-per-cluster queue and worker."""
import time
from datetime import date

import pandas as pd
import streamlit as st

import method1_supply_state as store
from method2_supply import _check_order_status, create_individual_supply

RETRY_SECONDS = 180


def process_once(client, key):
    plan = store.read_plan()
    if not plan:
        return
    jobs = plan["jobs"]
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
    st.write(f"Товар: **{plan['identity']['article']}** · SKU Ozon: **{plan['identity']['sku']}** · {sum(j['quantity'] for j in plan['jobs'])} шт.")
    st.dataframe(pd.DataFrame([{
        "Кластер": job["name"], "Шт.": job["quantity"],
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
