"""Method 2: SKU -> geography -> allocation -> date/time -> real individual supplies.

No part of Method 1 is modified here. One Ozon supply order per destination.
The 180-second retry loop runs only while its Streamlit session is active.
"""
import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

import pandas as pd
import streamlit as st
import method2_state as store

BASE = "https://api-seller.ozon.ru"
ORIGIN_ID = 1020005035056500
ORIGIN_TYPE = "SORTING_CENTER"
MOSCOW = timezone(timedelta(hours=3))
MIN_CLUSTER = 19
RETRY_SECONDS = 180
RUN_INTERVAL_SECONDS = 40
MAX_DRAFTS_PER_HOUR = 45
SLOTS = [f"{hour:02d}:00" for hour in range(0, 24)] + ["24:00"]


def ozon(client, key, endpoint, body, timeout=40):
    request = urllib.request.Request(
        BASE + endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Client-Id": client, "Api-Key": key, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _clean(value):
    return " ".join(str(value or "").strip().casefold().replace("ё", "е").split())


def _skus_from_product(item):
    identifiers = []
    direct = item.get("sku")
    if direct:
        identifiers.extend(direct if isinstance(direct, list) else [direct])
    stocks = item.get("stocks") or {}
    if isinstance(stocks, dict):
        for stock in stocks.get("stocks") or []:
            if isinstance(stock, dict) and stock.get("sku"):
                identifiers.append(stock["sku"])
    for source in item.get("sources") or []:
        if isinstance(source, dict) and source.get("sku"):
            identifiers.append(source["sku"])
    return sorted({str(v).strip() for v in identifiers if str(v).strip().isdigit() and int(str(v)) > 0})


@st.cache_data(ttl=1800, show_spinner=False)
def catalog(client, _api_key):
    products = {}
    cursor = ""
    listed = []
    for _ in range(30):
        data = ozon(client, _api_key, "/v3/product/list", {
            "filter": {"visibility": "ALL"}, "limit": 500, "last_id": cursor,
        })
        page = (data.get("result") or {}).get("items") or []
        if not isinstance(page, list):
            raise ValueError("Некорректный каталог Ozon.")
        listed.extend(item for item in page if isinstance(item, dict))
        new_cursor = str((data.get("result") or {}).get("last_id") or "")
        if not page or not new_cursor or new_cursor == cursor:
            break
        cursor = new_cursor
    for offset in range(0, len(listed), 500):
        batch = listed[offset:offset + 500]
        ids = [str(row.get("product_id")) for row in batch if row.get("product_id")]
        if not ids:
            continue
        info = ozon(client, _api_key, "/v3/product/info/list", {"product_id": ids})
        records = info.get("items") or (info.get("result") or {}).get("items") or []
        for item in records:
            if not isinstance(item, dict):
                continue
            article = str(item.get("offer_id") or "").strip()
            title = str(item.get("name") or article or "Товар").strip()
            for sku in _skus_from_product(item):
                products[sku] = {"sku": sku, "article": article, "name": title}
    # Some accounts include SKU in /v3/product/list, including products
    # omitted by the enrichment endpoint.
    for item in listed:
        for sku in _skus_from_product(item):
            if sku not in products:
                products[sku] = {
                    "sku": sku, "article": str(item.get("offer_id") or ""),
                    "name": str(item.get("name") or item.get("offer_id") or "Товар"),
                }
    if not products:
        raise ValueError("Ozon не вернул товары с SKU.")
    return products


@st.cache_data(ttl=3600, show_spinner=False)
def cluster_ids(client, _api_key):
    info = ozon(client, _api_key, "/v2/cluster/list", {})
    items = info.get("result") or []
    if not isinstance(items, list):
        raise ValueError("Не удалось получить кластеры Ozon.")
    result = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        cluster = (item.get("data") or {}).get("macrolocal_cluster") or {}
        name = str(cluster.get("name") or "")
        cluster_id = item.get("macrolocal_cluster_id")
        if name and cluster_id and int(cluster_id) > 0:
            result[_clean(name)] = int(cluster_id)
    if not result:
        raise ValueError("Ozon не вернул идентификаторы кластеров.")
    return result


def parse_geography(file_bytes):
    raw = pd.read_excel(BytesIO(file_bytes), header=None)
    idx = next(
        (i for i in range(min(len(raw), 50))
         if any("кластер доставки" in str(v).casefold() for v in raw.iloc[i])
         and any("доля в категории" in str(v).casefold() for v in raw.iloc[i])),
        None,
    )
    if idx is None:
        raise ValueError("В Excel не найдены «Кластер доставки» и «Доля в категории».")
    headers = raw.iloc[idx].astype(str).str.strip().tolist()
    data = raw.iloc[idx + 1:].copy()
    data.columns = headers
    data = data[["Кластер доставки", "Доля в категории"]].copy()
    data.columns = ["Кластер", "Доля, %"]
    data["Кластер"] = data["Кластер"].fillna("").astype(str).str.strip()

    def percent(value):
        # Ozon inserts an explanatory row directly under the table header:
        # "Процент от общей суммы заказов — с учётом примененных фильтров".
        # It is not a numeric cluster share and must be ignored.
        if pd.isna(value):
            return float("nan")
        try:
            if isinstance(value, str):
                part = value.replace("\u00a0", "").replace(" ", "").replace(",", ".").strip()
                if part.endswith("%"):
                    return float(part[:-1])
                x = float(part)
            else:
                x = float(value)
            return x * 100 if 0 <= x <= 1 else x
        except (TypeError, ValueError, OverflowError):
            return float("nan")

    data["Доля, %"] = pd.to_numeric(data["Доля, %"].map(
        lambda v: percent(v) if str(v).strip() else float("nan")
    ), errors="coerce")
    data = data[(data["Кластер"] != "") & data["Доля, %"].notna()].copy()
    if data.empty or (data["Доля, %"] < 0).any() or (data["Доля, %"] > 100).any():
        raise ValueError("В Excel неверные доли категории.")
    if data["Кластер"].map(_clean).duplicated().any():
        raise ValueError("В Excel повторяются названия кластеров.")
    total = float(data["Доля, %"].sum())
    if not (99 <= total <= 100.2):
        raise ValueError(f"Сумма долей {total:.2f}%. Для расчёта нужна полная география (около 100%).")
    removed = (
        (data["Доля, %"] < 0.5)
        | data["Кластер"].str.contains("самара|махачкала", case=False, na=False)
    )
    return data.loc[~removed].copy(), data.loc[removed].copy()


def _date_hours_allowed(slot, start_date, end_date, time_from, time_to):
    try:
        begins = datetime.fromisoformat(str(slot["from_in_timezone"]))
        ends = datetime.fromisoformat(str(slot["to_in_timezone"]))
    except (KeyError, TypeError, ValueError):
        return False
    comparison_start = begins if begins.tzinfo is not None else begins.replace(tzinfo=MOSCOW)
    if not start_date <= begins.date() <= end_date or comparison_start <= datetime.now(MOSCOW):
        return False
    b = begins.strftime("%H:%M")
    e = ends.strftime("%H:%M") if ends.date() == begins.date() else "24:00"
    return time_from <= b and e <= time_to


def _new_draft(client, key, job):
    created = ozon(client, key, "/v1/draft/crossdock/create", {
        "cluster_info": {
            "macrolocal_cluster_id": int(job["cluster_id"]),
            "items": [{"sku": int(job["sku"]), "quantity": int(job["quantity"])}],
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
        return None, None, "Кластер недоступен в Ozon"
    return draft_id, selected, ""


def _check_order_status(client, key, job):
    try:
        response = ozon(client, key, "/v2/draft/supply/create/status", {
            "draft_id": int(job["creation_draft_id"])
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
    except Exception as ex:
        job["status"] = "VERIFYING"
        job["reason"] = "Статус требует повторной проверки: " + str(ex)[:120]


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
        time_response = ozon(client, key, "/v2/draft/timeslot/info", {
            "date_from": start_date.isoformat(),
            "date_to": end_date.isoformat(),
            "draft_id": draft_id,
            "supply_type": "CROSSDOCK",
            "selected_cluster_warehouses": [selected],
        })
        raw = (time_response.get("result") or {}).get("drop_off_warehouse_timeslots") or {}
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        if not isinstance(raw, dict):
            job["status"] = "RETRY"
            job["reason"] = "Ozon не вернул календарь."
            return
        offers = []
        for day in raw.get("days", []) or []:
            for slot in day.get("timeslots", []) or []:
                if _date_hours_allowed(slot, start_date, end_date, time_from, time_to):
                    offers.append(slot)
        offers.sort(key=lambda x: str(x.get("from_in_timezone")))
        if not offers:
            job["status"] = "RETRY"
            job["reason"] = "Свободных интервалов нет в выбранном диапазоне."
            return
        slot = offers[0]
        job["timeslot"] = {
            "from": str(slot["from_in_timezone"]),
            "to": str(slot["to_in_timezone"]),
        }
        # This operation creates a REAL Ozon supply. It must be invoked
        # at most once per draft. Ambiguous network outcomes are not retried.
        job["status"] = "SUBMITTING"
        job["reason"] = "Отправляем заявку; повторы заблокированы до проверки результата."
        if before_submit is not None:
            before_submit(job)
        result = ozon(client, key, "/v2/draft/supply/create", {
            "draft_id": draft_id,
            "selected_cluster_warehouses": [selected],
            "timeslot": {
                "from_in_timezone": job["timeslot"]["from"],
                "to_in_timezone": job["timeslot"]["to"],
            },
            "supply_type": "CROSSDOCK",
        })
        errors = result.get("error_reasons") or []
        operation = int(result.get("draft_id") or 0)
        if errors or not operation:
            if "TIMESLOT_NOT_AVAILABLE" in errors:
                job["status"] = "RETRY"
                job["reason"] = "Слот уже занят — попробуем другой."
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
            job["reason"] = f"Ozon HTTP {error.code}; повторим."
        else:
            job["status"] = "NEEDS_REVIEW"
            job["reason"] = f"Ozon HTTP {error.code}; нужна проверка."
    except Exception as error:
        if job["status"] == "SUBMITTING":
            job["status"] = "NEEDS_REVIEW"
            job["reason"] = "Неясен результат отправки. Повтор запрещён: " + str(error)[:100]
        else:
            job["status"] = "RETRY"
            job["reason"] = str(error)[:200]


@st.fragment(run_every=30)
def queue_worker():
    plan = store.read_plan()
    if not plan:
        return
    st.subheader("📦 Сохранённая очередь Ozon — не пропадает при обновлении страницы")
    st.caption(
        f"План №{plan['id']} · SKU {plan['identity']['sku']}"
        f" · {len(plan['jobs'])} кластеров · "
        f"создан {plan['created_at'][:16]} UTC"
    )
    status_names = {
        "WAITING": "В очереди", "RETRY": "Повтор через 3 минуты",
        "CHECKING": "Проверяем Ozon", "SUBMITTING": "Заявка отправляется",
        "VERIFYING": "Проверяем номер Ozon", "CREATED": "✅ Создана",
        "NEEDS_REVIEW": "⚠️ Проверить вручную", "STOPPED": "Остановлена",
    }
    jobs = plan["jobs"]
    output = pd.DataFrame([{
        "Кластер": j["name"], "SKU": j["sku"], "Шт.": j["quantity"],
        "Статус": status_names.get(j["status"], j["status"]),
        "Попыток": j.get("attempts", 0),
        "Номер заявки Ozon": j.get("order_id") or "—",
        "Причина": j.get("reason") or "",
    } for j in jobs])
    st.dataframe(output, use_container_width=True, hide_index=True)
    created = sum(j["status"] == "CREATED" for j in jobs)
    pending = sum(j["status"] in ("WAITING", "RETRY", "CHECKING", "VERIFYING", "SUBMITTING") for j in jobs)
    st.write(f"**Создано {created}/{len(jobs)}** · ещё в обработке: {pending}.")
    st.caption(
        "Статусы сохраняются на сервере при обновлении вкладки. "
        "Пока Streamlit-сервер работает, он продолжает очередь без открытой страницы."
    )
    st.warning(
        "Важно: сервер Streamlit Cloud может перезапускаться и стирать локальные файлы. "
        "Для гарантированной сохранности через деплой и перезапуск потребуется внешняя база данных. "
        "Уже отправленные, но не подтверждённые заявки автоматически не дублируются."
    )
    if any(j["status"] in ("WAITING", "RETRY") for j in jobs) and not plan["stopped"]:
        if st.button("Остановить дальнейшие попытки", key="method2_stop_persisted"):
            store.stop_queue()
            st.rerun()



def render_method2(calculate):
    st.header("🚀 Метод 2 — поставки Ozon")
    st.caption("1 кластер = 1 отдельная поставка. Самара и Махачкала исключены, количество не уменьшаем.")
    client = str(st.secrets.get("OZON_CLIENT_ID") or "")
    key = str(st.secrets.get("OZON_API_KEY") or "")
    if not client or not key:
        st.error("Не настроен доступ к API Ozon.")
        return
    store.ensure_worker(client, key)
    active = store.read_plan()
    if active:
        st.success("Текущий план и все статусы уже сохранены. Заново выбирать товар и загружать Excel не нужно.")
        st.info(
            f"SKU {active['identity']['sku']} · "
            f"артикул {active['identity'].get('article') or '—'} · "
            f"отгружаем {active['identity']['quantity']} шт. "
            f"на {len(active['jobs'])} отдельных направлений."
        )
        st.caption("Сохранённая таблица поставок отображается ниже, с номерами заявок Ozon.")
        return

    st.markdown("**1. Выбор товара**")
    try:
        with st.spinner("Загружаем выпадающий список товаров Ozon..."):
            goods = catalog(client, key)
    except Exception as error:
        st.error(f"Не удалось получить товары из Ozon: {error}")
        return
    names = [None] + sorted(goods, key=lambda s: (goods[s]["article"].casefold(), int(s)))
    recent_sku = store.pref_get("last_sku")
    if "method2_sku" not in st.session_state and recent_sku in names:
        st.session_state.method2_sku = recent_sku
    sku = st.selectbox(
        "Товар / артикул / SKU",
        names,
        format_func=lambda s: (
            "Выбери товар..." if s is None
            else f"{goods[s]['article'] or goods[s]['name']} · {goods[s]['name']} · SKU {s}"
        ),
        key="method2_sku",
    )
    if not sku:
        st.info("Выбери товар в списке. Остальные шаги откроются автоматически.")
        return
    chosen = goods[sku]
    if store.pref_get("last_sku") != sku:
        store.pref_set("last_sku", sku)
    st.caption(f"Артикул продавца: {chosen['article']} · SKU Ozon: **{sku}**")

    st.markdown("**2. Загрузка географии и долей категории**")
    document = st.file_uploader(
        "Excel Ozon: Аналитика категорий → География (Мой магазин выключен)",
        type=["xlsx"], key=f"method2_geography_{sku}",
    )
    saved_file = store.file_get(sku)
    if not document and not saved_file:
        return
    if f"method2_qty_{sku}" not in st.session_state and saved_file:
        st.session_state[f"method2_qty_{sku}"] = max(19, int(saved_file[2]))
    qty = st.number_input("Количество для отправки, шт.", min_value=19, value=1650, step=1, key=f"method2_qty_{sku}")
    data_bytes = document.getvalue() if document else saved_file[0]
    if document:
        digest = hashlib.sha256(data_bytes).hexdigest()
        store.file_save(sku, data_bytes, digest, qty)
    else:
        st.success("Excel восстановлен из сохранённых данных. Повторно загружать его не нужно.")
        if saved_file[2] != qty:
            store.file_save(sku, data_bytes, saved_file[1], qty)
    try:
        eligible, excluded = parse_geography(data_bytes)
        mapping = cluster_ids(client, key)
        result = calculate(
            [(row["Кластер"], float(row["Доля, %"]), 0.0) for _, row in eligible.iterrows()],
            int(qty), MIN_CLUSTER,
        )
        rows = []
        for name, amount in sorted(result.items(), key=lambda p: -p[1]):
            cid = mapping.get(_clean(name))
            rows.append({
                "Кластер": name, "Доля, %": float(eligible.loc[eligible["Кластер"] == name, "Доля, %"].iloc[0]),
                "Отправить, шт.": int(amount), "ID Ozon": cid,
            })
        if sum(row["Отправить, шт."] for row in rows) != qty:
            raise ValueError("Контроль суммы распределения не пройден.")
    except Exception as error:
        st.error(f"Не удалось рассчитать географию: {error}")
        return

    st.markdown("**3. Список отгрузок с учётом исключений**")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.success(f"Распределено {sum(row['Отправить, шт.'] for row in rows)} шт. по {len(rows)} кластерам.")
    st.caption(f"Исключено направлений: {len(excluded)} (доля менее 0,5%, Самара и Махачкала).")
    missing = [row["Кластер"] for row in rows if not row["ID Ozon"]]
    if missing:
        st.error("В Ozon не найдены ID кластеров: " + ", ".join(missing) + ". Создание заблокировано.")
        return

    st.markdown("**4. Выбор диапазона дат и времени**")
    today = datetime.now(MOSCOW).date()
    dates = st.date_input(
        "Диапазон дат приёмки",
        value=(today + timedelta(days=1), today + timedelta(days=10)),
        min_value=today, max_value=today + timedelta(days=27),
        key="method2_date_range",
    )
    st.caption("Время (МСК): выбери начало, затем конец диапазона в таблице — ничего писать не нужно.")
    if "method2_time_start" not in st.session_state:
        st.session_state.method2_time_start = "08:00"
        st.session_state.method2_time_end = "22:00"
        st.session_state.method2_time_next = "start"

    def choose_hour(value):
        if st.session_state.method2_time_next == "start":
            if value == "24:00":
                return
            st.session_state.method2_time_start = value
            st.session_state.method2_time_next = "end"
        else:
            if value <= st.session_state.method2_time_start:
                st.session_state.method2_time_start = value
                st.session_state.method2_time_next = "end"
            else:
                st.session_state.method2_time_end = value
                st.session_state.method2_time_next = "start"

    st.write(
        f"**Выбрано: {st.session_state.method2_time_start}–"
        f"{st.session_state.method2_time_end}** · "
        + ("Нажми начало интервала" if st.session_state.method2_time_next == "start"
           else "Теперь нажми конец интервала")
    )
    for row_start in range(0, len(SLOTS), 6):
        columns = st.columns(6, gap="small")
        for index, hour in enumerate(SLOTS[row_start:row_start + 6]):
            columns[index].button(
                hour,
                key=f"method2_hour_{hour}",
                type="primary" if hour in (
                    st.session_state.method2_time_start,
                    st.session_state.method2_time_end,
                ) else "secondary",
                on_click=choose_hour,
                args=(hour,),
                use_container_width=True,
            )
    times = (st.session_state.method2_time_start, st.session_state.method2_time_end)
    if len(dates) != 2:
        st.info("Выбери начало и конец диапазона дат.")
        return
    begin_day, end_day = dates
    if (end_day - begin_day).days > 13:
        st.error("Проверяем максимум 14 дней за один запрос. Уменьши диапазон дат.")
        return
    if times[0] >= times[1]:
        st.error("Время окончания должно быть позже начала.")
        return

    identity = {
        "sku": int(sku), "article": chosen["article"], "quantity": int(qty),
        "excel_sha256": hashlib.sha256(data_bytes).hexdigest(),
        "rows": [(r["ID Ozon"], r["Отправить, шт."]) for r in rows],
        "date_from": begin_day.isoformat(), "date_to": end_day.isoformat(),
        "time_from": times[0], "time_to": times[1],
    }
    signature = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()

    st.markdown("**5. Создать поставку**")
    st.info(f"Будут созданы **{len(rows)} отдельные заявки Ozon**, по одной на кластер, для SKU **{sku}**. Точка отгрузки: Казань Аэропорт.")
    existing_queue = st.session_state.get("method2_supply_queue") or []
    already_submitted = any(row["status"] in ("CREATED", "VERIFYING", "SUBMITTING", "NEEDS_REVIEW") for row in existing_queue)
    same_plan = st.session_state.get("method2_active_signature") == signature
    if existing_queue and not same_plan:
        st.warning("У тебя уже есть очередь по другому плану. Новые заявки заблокированы, чтобы не создать дубли.")
    if st.button(
        "🚀 СОЗДАТЬ ПОСТАВКИ",
        key="method2_create_real",
        type="primary",
        disabled=bool(existing_queue),
        help="Реальные заявки создаются по одной на кластер. Даты и время перепроверяются перед каждой отправкой.",
    ):
        st.session_state.method2_supply_queue = [{
            "name": row["Кластер"], "cluster_id": int(row["ID Ozon"]),
            "sku": int(sku), "quantity": int(row["Отправить, шт."]),
            "attempts": 0, "status": "WAITING",
            "reason": "", "order_id": None,
        } for row in rows]
        st.session_state.method2_active_signature = signature
        st.session_state.method2_window = (
            begin_day.isoformat(), end_day.isoformat(), times[0], times[1]
        )
        st.session_state.method2_next_attempt_at = 0
        st.success("Создание отдельных реальных поставок запущено. Первую попытку выполняем сейчас.")

    st.markdown("**6. Повторные попытки**")
    if not st.session_state.get("method2_supply_queue"):
        st.caption("После нажатия кнопки недоступные направления попадут в очередь. Повторы — каждые 3 минуты в активной сессии.")
