Warning: truncated output (original token count: 22828)
Total output lines: 1282

"""Панель Ozon FBO: расчёт планов и независимые очереди поставок двух методов."""
import hmac
import hashlib
import math
import json
import time
import urllib.request
import urllib.error
from datetime import date, timedelta, datetime, time, timezone
from pathlib import Path
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Ozon FBO Control", page_icon="📦", layout="wide")

def calculate(rows, quantity, minimum):
    """Balance post-supply days of cover, honoring FBO stock and per-cluster minimum."""
    active = [(str(c), float(d), float(s)) for c, d, s in rows if d > 0]
    if quantity == 0:
        return {}
    if not active or quantity < minimum:
        raise ValueError("Недостаточно данных или товара для минимальной партии.")
    while active:
        lo = 0.0
        hi = (quantity + sum(s for _, _, s in active)) / min(d for _, d, _ in active)
        for _ in range(65):
            mid = (lo + hi) / 2
            if sum(max(0, mid * d - s) for _, d, s in active) > quantity:
                hi = mid
            else:
                lo = mid
        weights = [max(0, hi * d - s) for _, d, s in active]
        weight_sum = sum(weights)
        if weight_sum <= 0:
            raise ValueError("Не удалось найти подходящие направления.")
        raw = [quantity * w / weight_sum for w in weights]
        ints = [math.floor(v) for v in raw]
        remainder = quantity - sum(ints)
        for i in sorted(range(len(raw)), key=lambda j: (-(raw[j] - ints[j]), active[j][0]))[:remainder]:
            ints[i] += 1
        keep = [active[i] for i, n in enumerate(ints) if n >= minimum]
        if len(keep) == len(active):
            return {active[i][0]: ints[i] for i in range(len(active)) if ints[i] > 0}
        active = keep
    raise ValueError("Нет кластеров, проходящих минимальный порог. Требуется решение вручную.")

st.title("📦 Ozon FBO Control")
st.caption("Метод 1 и Метод 2 работают независимо: каждый считает свой план и создаёт свои поставки.")
st.info("Постоянная точка отгрузки: КАЗАНЬ_АЭРОПОРТ_КРОССДОКИНГ")
from io import BytesIO
import method1_state as method1_store
from method1_validation import has_only_finite_numbers
from method1_report import read_method1_report, suggest_method1_column
from method1_multi_plan import combine_product_allocations
from method2_supply import render_method2, queue_worker, catalog as ozon_catalog, cluster_ids as ozon_cluster_ids, _clean as clean_ozon_name
from method1_supply import render_method1_queue
import method1_supply_state as method1_supply_store

tab_plan, tab_method2, tab_stocks, tab_sales, tab_rules, tab_warehouses = st.tabs(
    ["📊 Метод 1", "🚀 Метод 2", "📦 Остатки Ozon", "📈 Продажи Ozon", "⚙️ Правила", "🚚 Склады"]
)
method1_report_columns = None
method1_column_defaults = {}
with tab_method2:
    render_method2(calculate)
    queue_worker()
with tab_plan:
    uploaded = st.file_uploader("Загрузи Excel или CSV с продажами и остатками", type=["xlsx", "csv"])
    if uploaded is not None:
        report_hash, changed = method1_store.save_report(uploaded.name, uploaded.getvalue())
        if changed:
            st.success("Файл сохранён на сервере. При обновлении страницы не пропадёт.")
    saved_report = method1_store.load_report()
    if saved_report is not None:
        if uploaded is None:
            st.success(f"Восстановлен предыдущий файл: {saved_report['name']}. Загружать заново не нужно.")
        try:
            df = read_method1_report(saved_report["content"], saved_report["name"])
            if df.empty:
                raise ValueError("Файл пустой.")
        except Exception as error:
            st.error(f"Не удалось прочитать сохранённый файл: {error}")
            st.session_state.pop("plan", None)
            st.session_state.pop("plan_sku", None)
        else:
            columns = list(df.columns)
            method1_report_columns = columns
            column_roles = {
                "sku_column": "product",
                "cluster_column": "cluster",
                "daily_column": "daily_sales",
                "stock_column": "fbo_stock",
            }
            for key, role in column_roles.items():
                current_choice = st.session_state.get(key)
                saved_choice = (
                    current_choice if current_choice in columns
                    else method1_store.get_setting("method1_" + key)
                )
                method1_column_defaults[key] = suggest_method1_column(columns, role, saved_choice)

            def selected_column(key):
                current_choice = st.session_state.get(key)
                return current_choice if current_choice in columns else method1_column_defaults[key]

            sku_col = selected_column("sku_column")
            cluster_col = selected_column("cluster_column")
            daily_col = selected_column("daily_column")
            stock_col = selected_column("stock_column")
            if len({sku_col, cluster_col, daily_col, stock_col}) != 4:
                st.warning("Нужно выбрать четыре разных столбца.")
                st.session_state.pop("plan", None)
                st.session_state.pop("plan_sku", None)
            else:
                products = sorted(df[sku_col].dropna().astype(str).unique().tolist())
                if not products:
                    st.error("В файле нет товаров.")
                else:
                    saved_products = method1_store.get_setting("method1_products")
                    if not isinstance(saved_products, list):
                        legacy_product = method1_store.get_setting("method1_sku")
                        saved_products = [legacy_product] if legacy_product in products else [products[0]]
                    default_products = [product for product in saved_products if product in products] or [products[0]]
                    selected_products = st.multiselect(
                        "Товары для расчёта и одной поставки на кластер",
                        products,
                        default=default_products,
                        key="method1_products",
                    )
                    saved_qty = method1_store.get_setting("method1_quantity", 1650)
                    saved_minimum = method1_store.get_setting("method1_minimum", 19)
                    if not selected_products:
                        st.info("Выбери один или несколько товаров.")
                    quantity_defaults = []
                    for index, product in enumerate(selected_products):
                        remembered = method1_store.get_setting("method1_quantity_" + product)
                        if remembered is None:
                            remembered = int(saved_qty) if len(selected_products) == 1 or index == 0 else 0
                        quantity_defaults.append({"Товар": product, "Количество, шт.": max(0, int(remembered))})
                    quantities_df = st.data_editor(
                        pd.DataFrame(quantity_defaults, columns=["Товар", "Количество, шт."]),
                        hide_index=True, use_container_width=True,
                        disabled=["Товар"],
                        column_config={
                            "Товар": st.column_config.TextColumn("Товар"),
                            "Количество, шт.": st.column_config.NumberColumn(
                                "Количество к распределению, шт.", min_value=0, step=1, format="%d",
                            ),
                        },
                        key="method1_product_quantities_" + hashlib.sha1(
                            json.dumps(selected_products, ensure_ascii=False).encode("utf-8")
                        ).hexdigest()[:10],
                    )
                    quantities = {
                        str(row["Товар"]): int(row["Количество, шт."])
                        for _, row in quantities_df.iterrows()
                    }
                    minimum = st.number_input(
                        "Минимум на один товар и кластер", min_value=1,
                        value=max(1, int(saved_minimum)), step=1, key="method1_minimum",
                    )
                    clusters = sorted(df[cluster_col].dropna().astype(str).unique().tolist())
                    previous_excluded = method1_store.get_setting("method1_excluded")
                    excluded_default = (
                        [name for name in previous_excluded if name in clusters]
                        if isinstance(previous_excluded, list)
                        else [name for name in clusters if any(
                            city in name.lower() for city in ("самара", "махачкала")
                        )]
                    )
                    excluded = st.multiselect(
                        "Исключить кластеры", clusters,
                        default=excluded_default, key="method1_excluded",
                    )
                    method1_store.set_setting("method1_products", list(selected_products))
                    if len(selected_products) == 1:
                        method1_store.set_setting("method1_sku", selected_products[0])
                    for product, product_quantity in quantities.items():
                        method1_store.set_setting("method1_quantity_" + product, product_quantity)
                    method1_store.set_setting("method1_minimum", int(minimum))
                    method1_store.set_setting("method1_excluded", list(excluded))

                    calculation_inputs = {
                        "file": saved_report["sha256"],
                        "products": sorted(quantities.items()),
                        "columns": [str(sku_col), str(cluster_col), str(daily_col), str(stock_col)],
                        "minimum": int(minimum),
                        "excluded": sorted(excluded),
                    }
                    calculation_signature = method1_store.signature_for(calculation_inputs)
                    cached_plan = method1_store.get_plan("excel", calculation_signature)
                    if st.button("Рассчитать", type="primary", key="method1_calculate", disabled=not selected_products):
                        if any(quantity <= 0 for quantity in quantities.values()):
                            st.error("Укажи количество больше нуля для каждого выбранного товара.")
                        else:
                            product_allocations = {}
                            calculation_error = None
                            for product, product_quantity in quantities.items():
                                subset = df.loc[df[sku_col].astype(str) == product, [cluster_col, daily_col, stock_col]].copy()
                                subset[daily_col] = pd.to_numeric(subset[daily_col], errors="coerce")
                                subset[stock_col] = pd.to_numeric(subset[stock_col], errors="coerce")
                                invalid_values = (
                                    subset[[daily_col, stock_col]].isna().any().any()
                                    or (subset[[daily_col, stock_col]] < 0).any().any()
                                    or not has_only_finite_numbers(subset[daily_col])
                                    or not has_only_finite_numbers(subset[stock_col])
                                )
                                if invalid_values:
                                    calculation_error = f"В продажах или остатках товара «{product}» есть пустые, отрицательные, бесконечные или нечисловые значения."
                                    break
                                grouped = subset.groupby(cluster_col, as_index=False)[[daily_col, stock_col]].sum()
                                rows = [
                                    (r[cluster_col], r[daily_col], r[stock_col])
                                    for _, r in grouped.iterrows() if str(r[cluster_col]) not in excluded
                                ]
                                try:
                                    product_plan = calculate(rows, product_quantity, int(minimum))
                                except ValueError as error:
                                    calculation_error = f"Товар «{product}»: {error}"
                                    break
                                product_allocations[product] = product_plan
                            if calculation_error:
                                st.error(calculation_error)
                            else:
                                rows_for_save = combine_product_allocations(product_allocations)
                                method1_store.save_plan(
                                    "excel", calculation_signature,
                                    ", ".join(selected_products), rows_for_save,
                                )
                                cached_plan = method1_store.get_plan("excel", calculation_signature)

                    if cached_plan is not None:
                        display_rows = []
                        for row in cached_plan["rows"]:
                            legacy_items = row.get("items") or [{
                                "product": cached_plan["sku"],
                                "quantity": int(row["Отправить, шт."]),
                            }]
                            display_rows.append({
                                "Кластер": row["Кластер"],
                                "Товары": row.get("Товары") or "; ".join(
                                    f"{item['product']} × {item['quantity']}" for item in legacy_items
                                ),
                                "Отправить, шт.": int(row["Отправить, шт."]),
                                "items": legacy_items,
                            })
                        st.session_state.plan = pd.DataFrame(display_rows)
                        st.session_state.plan_sku = cached_plan["sku"]
                        st.subheader("Сохранённый план: " + cached_plan["sku"])
                        st.dataframe(
                            st.session_state.plan[["Кластер", "Товары", "Отправить, шт."]],
                            use_container_width=True, hide_index=True,
                        )
                        st.metric("Всего распределено", int(st.session_state.plan["Отправить, шт."].sum()))
                        st.download_button(
                            "Скачать план CSV",
                            st.session_state.plan.to_csv(index=False).encode("utf-8-sig"),
                            "ozon_fbo_plan.csv", "text/csv",
                        )
                        st.markdown("### Создать поставки по Методу 1")
                        st.caption("Метод 1 создаёт собственные заявки по рассчитанным кластерам. Его очередь отдельна от очереди Метода 2.")
                        api_client = str(st.secrets.get("OZON_CLIENT_ID") or "")
                        api_key = str(st.secrets.get("OZON_API_KEY") or "")
                        mapped_rows = []
                        selected_goods_by_product = {}
                        unmapped_products = []
                        if not api_client or not api_key:
                            st.error("Для создания заявок не настроен доступ к Ozon API.")
                        else:
                            try:
                                goods = ozon_catalog(api_client, api_key)
                                products_in_plan = sorted({
                                    str(item["product"])
                                    for row in cached_plan["rows"]
                                    for item in (row.get("items") or [{"product": cached_plan["sku"]}])
                                })
                                catalog_options = sorted(
                                    goods,
                                    key=lambda value: (goods[value]["article"].casefold(), int(value)),
                                )
                                for product in products_in_plan:
                                    product_key = " ".join(product.strip().casefold().replace("ё", "е").split())
                                    exact_skus = [candidate for candidate, item in goods.items()
                                                  if product_key in {
                                                      " ".join(str(item.get("article", "")).strip().casefold().replace("ё", "е").split()),
                                                      " ".join(str(item.get("name", "")).strip().casefold().replace("ё", "е").split()),
                                                  }]
                                    options = exact_skus or catalog_options
                                    selection = st.selectbox(
                                        f"Товар «{product}» в каталоге Ozon",
                                        [None] + options,
                                        index=1 if len(exact_skus) == 1 else 0,
                                        format_func=lambda value: "Выбери совпадение..." if value is None else (
                                            f"{goods[value]['article']} · {goods[value]['name']} · SKU {value}"
                                        ),
                                        key="method1_ozon_product_" + hashlib.sha1(product.encode("utf-8")).hexdigest()[:10],
                                    )
                                    if selection:
                                        selected_goods_by_product[product] = goods[selection]
                                    else:
                                        unmapped_products.append(product)
                                    if not exact_skus:
                                        st.warning(f"Для «{product}» точного совпадения нет: проверь выбранный товар Ozon вручную.")
                                cluster_mapping = ozon_cluster_ids(api_client, api_key)
                                for row in cached_plan["rows"]:
                                    cluster = str(row["Кластер"])
                                    cluster_id = cluster_mapping.get(clean_ozon_name(cluster))
                                    source_items = row.get("items") or [{
                                        "product": cached_plan["sku"],
                                        "quantity": int(row["Отправить, шт."]),
                                    }]
                                    ozon_items = []
                                    for item in source_items:
                                        product = str(item["product"])
                                        mapped = selected_goods_by_product.get(product)
                                        if mapped:
                                            ozon_items.append({
                                                "product": product,
                                                "sku": int(mapped["sku"]),
                                                "quantity": int(item["quantity"]),
                                            })
                                    mapped_rows.append({
                                        "Кластер": cluster,
                                        "Отправить, шт.": int(row["Отправить, шт."]),
                                        "ID Ozon": cluster_id,
                                        "items": ozon_items,
                                    })
                            except Exception as error:
                                st.error(f"Не удалось сверить товар и кластеры с Ozon: {error}")
                        unmapped = [row["Кластер"] for row in mapped_rows if not row["ID Ozon"]]
                        if unmapped:
                            st.error("Ozon не подтвердил кластеры: " + ", ".join(unmapped))
                        if unmapped_products:
                            st.error("Выбери соответствие в каталоге Ozon для: " + ", ".join(unmapped_products))
                        st.write("**Даты приёмки (МСК)**")
                        today_local = datetime.now(timezone(timedelta(hours=3))).date()
                        schedule_cols = st.columns(2)
                        start_day = schedule_cols[0].date_input(
                            "Дата с", value=today_local + timedelta(days=1), min_value=today_local,
                            key="method1_supply_date_from",
                        )
                        end_day = schedule_cols[1].date_input(
                            "Дата по", value=today_local + timedelta(days=10), min_value=today_local,
                            key="method1_supply_date_to",
                        )
                        slots = [f"{hour:02d}:00" for hour in range(24)] + ["24:00"]
                        if "method1_supply_time_start" not in st.session_state:
                            st.session_state.method1_supply_time_start = "08:00"
                            st.session_state.method1_supply_time_end = "22:00"
                            st.session_state.method1_supply_time_next = "start"

                        def choose_method1_hour(hour):
                            if st.session_state.method1_supply_time_next == "start":
                                if hour != "24:00":
                                    st.session_state.method1_supply_time_start = hour
                                    st.session_state.method1_supply_time_next = "end"
                            elif hour <= st.session_state.method1_supply_time_start:
                                st.session_state.method1_supply_time_start = hour
                                st.session_state.method1_supply_time_next = "end"
                            else:
                                st.session_state.method1_supply_time_end = hour
                                st.session_state.method1_supply_time_next = "start"

                        st.caption(
                            f"Время: **{st.session_state.method1_supply_time_start}–"
                            f"{st.session_state.method1_supply_time_end}** (МСК). "
                            + ("Сначала нажми время начала." if st.session_state.method1_supply_time_next == "start"
                               else "Теперь нажми время окончания.")
                        )
                        for row_start in range(0, len(slots), 6):
                            hour_cols = st.columns(6, gap="small")
                            for index, hour in enumerate(slots[row_start:row_start + 6]):
                                hour_cols[index].button(
                                    hour,
                                    key=f"method1_supply_hour_{hour}",
                                    type="primary" if hour in (
                                        st.session_state.method1_supply_time_start,
                                        st.session_state.method1_supply_time_end,
                                    ) else "secondary",
                                    on_click=choose_method1_hour,
                                    args=(hour,),
                                    use_container_width=True,
                                )
                        time_from = st.session_state.method1_supply_time_start
                        time_to = st.session_state.method1_supply_time_end
                        queue = method1_supply_store.read_plan()
                        if queue:
                            st.warning("У Метода 1 уже есть отдельная сохранённая очередь. Новая не создаётся, чтобы не задублировать заявки.")
                        confirmed = st.checkbox(
                            f"Подтверждаю создание {len(mapped_rows)} заявок: {sum(row['Отправить, шт.'] for row in mapped_rows)} шт. всего, товаров в заказе — {len(selected_goods_by_product)}.",
                            key="method1_supply_confirm",
                        )
                        valid_window = (
                            start_day <= end_day and (end_day - start_day).days <= 13
                            and time_from < time_to
                        )
                        if not valid_window:
                            st.error("Выбери корректный диапазон дат (не более 14 дней) и времени.")
                        if st.button(
                            "🚀 СОЗДАТЬ ПОСТАВКИ МЕТОДА 1", type="primary",
                            key="method1_create_supplies",
                            disabled=(bool(queue) or not confirmed or bool(unmapped_products) or bool(unmapped)
                                      or not mapped_rows or not valid_window or not api_client or not api_key),
                        ):
                            rows_for_supply = [row for row in mapped_rows if row["ID Ozon"]]
                            identity = {
                                "products": [
                                    {"product": product, "sku": int(item["sku"]),
                                     "article": item["article"], "name": item["name"]}
                                    for product, item in selected_goods_by_product.items()
                                ],
                                "quantity": sum(row["Отправить, шт."] for row in rows_for_supply),
                                "signature": calculation_signature,
                            }
                            window = [start_day.isoformat(), end_day.isoformat(),
                                      time_from, time_to]
                            queue_signature = hashlib.sha256(json.dumps(
                                {"identity": identity, "rows": rows_for_supply, "window": window},
                                sort_keys=True, ensure_ascii=False,
                            ).encode("utf-8")).hexdigest()
                            created_queue, was_created = method1_supply_store.start_plan(
                                queue_signature, identity, rows_for_supply, window,
                            )
                            if was_created:
                                st.success("Очер…8828 tokens truncated…! Вернись в Метод 2 и рассчитай заново.")
        wish_date = st.date_input(
            "Желаемая дата отгрузки (НЕ подтверждена Ozon)",
            min_value=date.today(), value=date.today() + timedelta(days=7),
            key="preflight_date",
        )
        st.caption("Наличие свободного таймслота и лимиты для каждого кластера пока НЕ проверены.")
        numeric_sku = new_product_sku.strip().isdigit() and int(new_product_sku.strip()) > 0
        ready_local = all_matched and origin_ok and minimum_ok and unique_clusters_ok and no_excluded_cities and numeric_sku and product_matches_plan
        if ready_local:
            st.success("Локальная проверка пройдена. Следующий этап: проверка возможности создания черновика через Ozon API.")
        else:
            st.warning("Есть незаполненные или неверные данные. К проверке в Ozon переходить нельзя.")
        st.divider()
        st.subheader("🚀 Одна поставка — без скриншотов и повторного ввода SKU")
        st.caption("Все поля ниже берутся прямо из выбранного плана. Ничего не нужно присылать в чат.")
        if ready_local and len(matches) > 0:
            one_items = [row for row in matches if row["Статус"] == "Совпало"]
            selected_one_index = st.selectbox(
                "Кластер для пробной поставки",
                range(len(one_items)),
                format_func=lambda i: (
                    str(one_items[i]["Кластер из плана"])
                    + " · " + str(one_items[i]["Отправить, шт."]) + " шт."
                ),
                key="single_supply_cluster_index",
            )
            one_row = one_items[selected_one_index]
            one_cluster_id = int(one_row["ID кластера Ozon"])
            one_qty = int(one_row["Отправить, шт."])
            one_sku = int(new_product_sku.strip())
            one_signature = (one_sku, one_cluster_id, one_qty)
            if st.session_state.get("single_supply_signature") != one_signature:
                for old_key in ("single_supply_draft", "single_supply_slots", "single_supply_choice"):
                    st.session_state.pop(old_key, None)
                st.session_state.single_supply_signature = one_signature
            st.info(
                f"**SKU Ozon {one_sku}** · **{one_row['Кластер из плана']}**"
                f" · **{one_qty} шт.** · Отгрузка: Казань Аэропорт Кроссдокинг"
            )
            if st.button("🔎 Проверить доступные даты Ozon для этой одной поставки", key="single_supply_check_slot"):
                try:
                    origin = origin_matches[0]
                    typemap = {
                        "WAREHOUSE_TYPE_SORTING_CENTER": "SORTING_CENTER",
                        "WAREHOUSE_TYPE_DELIVERY_POINT": "DELIVERY_POINT",
                        "WAREHOUSE_TYPE_ORDERS_RECEIVING_POINT": "ORDERS_RECEIVING_POINT",
                        "WAREHOUSE_TYPE_CROSS_DOCK": "CROSS_DOCK",
                    }
                    origin_type = typemap.get(str(origin.get("warehouse_type") or ""))
                    if not origin_type:
                        raise ValueError("Неизвестный тип точки отгрузки. Проверь данные Ozon.")
                    body = {
                        "cluster_info": {
                            "macrolocal_cluster_id": one_cluster_id,
                            "items": [{"sku": one_sku, "quantity": one_qty}],
                        },
                        "deletion_sku_mode": "FULL",
                        "delivery_info": {
                            "drop_off_warehouse": {
                                "warehouse_id": int(origin["warehouse_id"]),
                                "warehouse_type": origin_type,
                            },
                            "type": "DROPOFF",
                        },
                    }
                    created = ozon_reference_request("/v1/draft/crossdock/create", body)
                    draft_id = int(created.get("draft_id") or 0)
                    if not draft_id or created.get("errors"):
                        raise ValueError("Ozon не принял технический черновик: " + str(created.get("errors") or [])[:500])
                    info = {}
                    for _ in range(7):
                        time.sleep(3)
                        info = ozon_reference_request("/v2/draft/create/info", {"draft_id": draft_id})
                        if info.get("status") in ("SUCCESS", "FAILED"):
                            break
                    if info.get("status") != "SUCCESS" or info.get("errors"):
                        raise ValueError("Ozon не подтвердил черновик: " + str(info.get("errors") or info.get("status"))[:300])
                    selection = None
                    for cluster in info.get("clusters", []) or []:
                        if int(cluster.get("macrolocal_cluster_id") or 0) != one_cluster_id:
                            continue
                        for wh in cluster.get("warehouses", []) or []:
                            if (wh.get("availability_status") or {}).get("state") != "FULL_AVAILABLE":
                                continue
                            storage = wh.get("storage_warehouse") or {}
                            selection = {
                                "macrolocal_cluster_id": one_cluster_id,
                                "storage_warehouse_id": int(storage.get("warehouse_id") or 0),
                            }
                            break
                    if selection is None:
                        raise ValueError("Этот кластер сейчас недоступен в Ozon. Количество не меняем.")
                    first_day = datetime.now(timezone(timedelta(hours=3))).date() + timedelta(days=1)
                    times = ozon_reference_request("/v2/draft/timeslot/info", {
                        "date_from": first_day.isoformat(),
                        "date_to": (first_day + timedelta(days=13)).isoformat(),
                        "draft_id": draft_id,
                        "supply_type": "CROSSDOCK",
                        "selected_cluster_warehouses": [selection],
                    })
                    raw = (times.get("result") or {}).get("drop_off_warehouse_timeslots") or {}
                    if isinstance(raw, list):
                        raw = raw[0] if raw else {}
                    if not isinstance(raw, dict):
                        raise ValueError("Ozon вернул неожиданный формат календаря.")
                    slots = []
                    for day in raw.get("days", []) or []:
                        for slot in day.get("timeslots", []) or []:
                            begin = slot.get("from_in_timezone")
                            finish = slot.get("to_in_timezone")
                            if begin and finish:
                                slots.append({"from": str(begin), "to": str(finish)})
                    slots.sort(key=lambda row: row["from"])
                    st.session_state.single_supply_draft = draft_id
                    st.session_state.single_supply_slots = slots
                    st.session_state.single_supply_signature = one_signature
                    st.success(f"Черновик Ozon №{draft_id}. Свободных интервалов: {len(slots)}.")
                except urllib.error.HTTPError as error:
                    st.error(f"Ozon вернул HTTP {error.code}. Заявка не создана, попробуй позже.")
                except Exception as error:
                    st.error(f"Проверка не завершилась: {error}")
            slots_for_one = st.session_state.get("single_supply_slots") or []
            if slots_for_one:
                selected_slot = st.selectbox(
                    "Время этой поставки (МСК)",
                    range(len(slots_for_one)),
                    format_func=lambda i: (
                        slots_for_one[i]["from"].replace("T", " ")
                        + " — " + slots_for_one[i]["to"].split("T")[-1][:5]
                    ),
                    key="single_supply_choice",
                )
                one_time = slots_for_one[selected_slot]
                st.success(
                    f"Подготовлено: SKU {one_sku}, кластер {one_row['Кластер из плана']},"
                    f" {one_qty} шт., {one_time['from'].replace('T', ' ')}."
                )
                st.warning("Это выбранный технический черновик, НЕ созданная поставка. Настоящее оформление требует отдельного подтверждённого действия.")
            else:
                st.caption("Сначала нажми проверку дат: Ozon вернёт свежие слоты именно для выбранного SKU и одного кластера.")
        else:
            st.warning("Сначала выбери товар, рассчитай план и проверь соответствие кластеров Ozon.")

        preflight_signature = (
            source_key, new_product_sku.strip(), str(wish_date),
            tuple((str(row["ID кластера Ozon"]), int(row["Отправить, шт."])) for _, row in matched_df.iterrows()),
        )
        if st.button("Сформировать файл для проверки поставки", key="preflight_export_button", disabled=not ready_local):
            draft_export = matched_df.copy()
            draft_export["SKU Ozon"] = new_product_sku.strip()
            draft_export["Точка отгрузки ID"] = str(origin_matches[0]["warehouse_id"])
            draft_export["Желаемая дата (не подтверждена)"] = str(wish_date)
            draft_export["Доступность Ozon"] = "Не проверено"
            draft_export["Лимит Ozon"] = "Не проверено"
            draft_export["Таймслот Ozon"] = "Не проверено"
            st.session_state.preflight_export = draft_export
            st.session_state.preflight_export_signature = preflight_signature
        if (
            "preflight_export" in st.session_state and ready_local
            and st.session_state.get("preflight_export_signature") == preflight_signature
        ):
            st.download_button(
                "Скачать план для проверки CSV",
                st.session_state.preflight_export.to_csv(index=False).encode("utf-8-sig"),
                "ozon_supply_preflight.csv", "text/csv", key="preflight_download",
            )


        st.divider()
        st.subheader("🧪 Технический черновик Ozon")
        st.caption("Используем актуальный /v1/draft/multi-cluster/create. Это операция записи только технического черновика, НЕ создание поставки.")
        st.warning("Черновик живёт около 30 минут, не отображается в кабинете и не резервирует дату. Проверяем фактические количества, не уменьшаем план.")
        valid_preflight = ready_local and len(matches) <= 20 and bool(matched_df["ID кластера Ozon"].astype(str).str.fullmatch(r"[0-9]+").all())
        expected_total = total_to_supply
        draft_signature = (
            source_label,
            tuple((str(row["ID кластера Ozon"]), int(row["Отправить, шт."])) for _, row in matched_df.iterrows()),
            str(new_product_sku).strip(),
            str(origin_matches[0]["warehouse_id"]) if origin_ok else "",
        )
        st.info("Создание технических черновиков перенесено в приватный GitHub-мониторинг. Здесь можно проверять готовый черновик, но нельзя создавать новый через открытую панель.")
        if st.button(
            "Создать ТОЛЬКО технический черновик (без поставки)",
            key="create_technical_draft",
            disabled=True,
            type="primary",
        ):
            try:
                origin_wh = origin_matches[0]
                origin_type = str(origin_wh.get("warehouse_type", ""))
                allowed_types = {
                    "WAREHOUSE_TYPE_SORTING_CENTER": "SORTING_CENTER",
                    "WAREHOUSE_TYPE_DELIVERY_POINT": "DELIVERY_POINT",
                    "WAREHOUSE_TYPE_ORDERS_RECEIVING_POINT": "ORDERS_RECEIVING_POINT",
                    "WAREHOUSE_TYPE_CROSS_DOCK": "CROSS_DOCK",
                }
                if origin_type not in allowed_types:
                    raise ValueError("Неизвестный тип точки отгрузки Ozon: " + origin_type)
                if len(matches) > 20 or not valid_preflight:
                    raise ValueError("Не пройдена предварительная проверка.")
                post_body = {
                    "clusters_info": [
                        {
                            "macrolocal_cluster_id": int(row["ID кластера Ozon"]),
                            "items": [{"sku": int(new_product_sku.strip()), "quantity": int(row["Отправить, шт."])}],
                        }
                        for _, row in matched_df.iterrows()
                    ],
                    "deletion_sku_mode": "FULL",
                    "delivery_info": {
                        "drop_off_warehouse": {
                            "warehouse_id": int(origin_wh["warehouse_id"]),
                            "warehouse_type": allowed_types[origin_type],
                        },
                        "type": "DROPOFF",
                    },
                }
                created = ozon_reference_request("/v1/draft/multi-cluster/create", post_body)
                if not isinstance(created, dict):
                    raise ValueError("Ozon вернул неожиданный ответ.")
                draft_id = int(created.get("draft_id") or 0)
                errors = created.get("errors") or []
                st.session_state.technical_draft_id = draft_id if draft_id > 0 else None
                st.session_state.technical_draft_signature = draft_signature
                st.session_state.technical_draft_errors = errors
                st.session_state.pop("technical_draft_info", None)
                if draft_id > 0:
                    st.info(f"Ozon выдал ID технического черновика №{draft_id}. Доступность {expected_total} шт. ещё НЕ подтверждена. Поставка НЕ создана.")
                else:
                    st.error("Ozon не выдал ID черновика. Поставка не создана.")
                if errors:
                    st.warning("Ozon сообщил об ограничениях: " + json.dumps(errors, ensure_ascii=False)[:2500])
                    show_cluster_limit_errors(errors)
            except urllib.error.HTTPError as error:
                response_text = error.read(1500).decode("utf-8", errors="replace")
                st.error(f"Ozon отклонил черновик: HTTP {error.code}. " + response_text)
            except Exception as error:
                st.error(f"Черновик не создан: {error}")
        def show_cluster_limit_errors(errors_data):
            if not isinstance(errors_data, list):
                return
            names_by_id = {
                str(row["ID кластера Ozon"]): (str(row["Кластер из плана"]), int(row["Отправить, шт."]))
                for _, row in matched_df.iterrows()
            }
            flagged = []
            for error in errors_data:
                if not isinstance(error, dict):
                    continue
                if error.get("error_message") != "ITEMS_COUNT_MORE_THAN_MAX":
                    continue
                for cluster_id in error.get("macrolocal_cluster_ids", []) or []:
                    cluster_name, quantity = names_by_id.get(str(cluster_id), ("Не найден в текущем плане", None))
                    flagged.append({
                        "Кластер": cluster_name,
                        "Макролокальный ID": str(cluster_id),
                        "Запрошено, шт.": quantity,
                        "Ответ Ozon": "Количество превышает допустимый предел; точное максимальное количество неизвестно",
                    })
            if flagged:
                st.error("Ozon сообщил о превышении лимита. Количество в плане НЕ изменено.")
                st.dataframe(pd.DataFrame(flagged), hide_index=True, use_container_width=True)
                st.caption("Код ошибки не содержит точного допустимого количества. Нельзя автоматически уменьшать поставку или считать её доступной.")

        if st.session_state.get("technical_draft_signature") == draft_signature:
            current_draft = st.session_state.get("technical_draft_id")
            if current_draft:
                st.info(f"Технический черновик №{current_draft}. Для проверки ответа нажми кнопку ниже, пока он не истёк.")
                if st.button("Проверить технический черновик в Ozon", key="check_technical_draft"):
                    try:
                        info = ozon_reference_request("/v2/draft/create/info", {"draft_id": int(current_draft)})
                        if not isinstance(info, dict):
                            raise ValueError("Неожиданный формат ответа.")
                        st.session_state.technical_draft_info = info
                    except urllib.error.HTTPError as error:
                        st.error(f"Ozon вернул HTTP {error.code} при проверке черновика.")
                    except Exception as error:
                        st.error(str(error))
                if "technical_draft_info" in st.session_state:
                    info = st.session_state.technical_draft_info
                    st.write("Статус Ozon:", str(info.get("status", "неизвестен")))
                    if info.get("errors"):
                        st.warning("Ошибки Ozon: " + json.dumps(info["errors"], ensure_ascii=False)[:3000])
                        show_cluster_limit_errors(info["errors"])
                    warehouses_output = []
                    for item in info.get("clusters", []) or []:
                        if not isinstance(item, dict):
                            continue
                        whs = item.get("warehouses", []) or []
                        if not whs:
                            warehouses_output.append({
                                "Кластер": item.get("cluster_name", ""),
                                "Склад Ozon": "Нет доступных вариантов",
                                "ID склада": "", "Статус": "Не подтверждено",
                            })
                        for wh in whs:
                            storage = wh.get("storage_warehouse") or {}
                            avail = wh.get("availability_status") or {}
                            warehouses_output.append({
                                "Кластер": item.get("cluster_name", ""),
                                "Склад Ozon": storage.get("name", ""),
                                "ID склада": storage.get("warehouse_id", ""),
                                "Статус": avail.get("state", "Неизвестно"),
                                "Причина": avail.get("invalid_reason", ""),
                            })
                    if warehouses_output:
                        availability_df = pd.DataFrame(warehouses_output)
                        st.dataframe(availability_df, hide_index=True, use_container_width=True)
                        summary = availability_df.groupby("Кластер", dropna=False)["Статус"].agg(list)
                        available_clusters = [str(k) for k, statuses in summary.items()
                                              if "FULL_AVAILABLE" in statuses]
                        blocked_clusters = [str(k) for k, statuses in summary.items()
                                            if "NOT_AVAILABLE" in statuses and "FULL_AVAILABLE" not in statuses]
                        unknown_clusters = [str(k) for k in summary.index
                                            if str(k) not in available_clusters and str(k) not in blocked_clusters]
                        st.write(f"✅ Доступны: **{len(available_clusters)}** кластеров. ❌ Недоступны: **{len(blocked_clusters)}**. Не подтверждены: **{len(unknown_clusters)}**.")
                        if blocked_clusters:
                            st.error("Ozon не допускает приёмку в: " + ", ".join(blocked_clusters))
                        if unknown_clusters:
                            st.warning("Статус требует уточнения: " + ", ".join(unknown_clusters))
                        # Split the full plan by actual Ozon cluster availability, without
                        # redistributing or reducing any quantity.
                        availability_by_name = {
                            " ".join(str(cluster_name).strip().casefold().replace("ё", "е").split()):
                            list(statuses) for cluster_name, statuses in summary.items()
                        }
                        approved_rows, blocked_rows, unchecked_rows = [], [], []
                        for _, planned in matched_df.iterrows():
                            item = planned.to_dict()
                            cluster_key = " ".join(str(item["Кластер из плана"]).strip().casefold().replace("ё", "е").split())
                            statuses = availability_by_name.get(cluster_key, [])
                            if "FULL_AVAILABLE" in statuses:
                                approved_rows.append(item)
                            elif "NOT_AVAILABLE" in statuses:
                                blocked_rows.append(item)
                            else:
                                unchecked_rows.append(item)
                        approved_total = sum(int(x["Отправить, шт."]) for x in approved_rows)
                        blocked_total = sum(int(x["Отправить, шт."]) for x in blocked_rows)
                        unchecked_total = sum(int(x["Отправить, шт."]) for x in unchecked_rows)
                        st.divider()
                        st.subheader("📦 Отдельное оформление доступных направлений")
                        st.write(
                            f"**Доступная группа:** {len(approved_rows)} кластеров, {approved_total} шт.  "
                            f"**Ожидают Ozon:** {len(blocked_rows)} кластеров, {blocked_total} шт.  "
                            f"**Не проверены:** {len(unchecked_rows)} кластеров, {unchecked_total} шт."
                        )
                        if approved_total + blocked_total + unchecked_total != total_to_supply:
                            st.error("Ошибка проверки сумм: группы не совпадают с исходным планом. Дальнейшее оформление заблокировано.")
                        else:
                            if approved_rows:
                                st.success("Доступные направления можно оформлять отдельно — не дожидаясь остальных.")
                                ready_df = pd.DataFrame(approved_rows)
                                st.dataframe(ready_df, hide_index=True, use_container_width=True)
                                st.download_button(
                                    "Скачать 14 доступных направлений CSV",
                                    ready_df.to_csv(index=False).encode("utf-8-sig"),
                                    "ozon_ready_clusters.csv", "text/csv",
                                    key="ready_clusters_csv",
                                )
                            if blocked_rows:
                                blocked_df = pd.DataFrame(blocked_rows)
                                st.warning("Заблокированные направления остаются в отдельной очереди мониторинга. Их количество не переносим в доступную группу.")
                                st.dataframe(blocked_df, hide_index=True, use_container_width=True)
                                st.download_button(
                                    "Скачать ожидающие кластеры CSV",
                                    blocked_df.to_csv(index=False).encode("utf-8-sig"),
                                    "ozon_blocked_clusters.csv", "text/csv",
                                    key="blocked_clusters_csv",
                                )
                            if unchecked_rows:
                                st.warning("Есть направления с неизвестным статусом — им нельзя автоматически назначать поставку.")
                            st.caption("Статус FULL_AVAILABLE подтверждает предварительную доступность направления, но не свободный таймслот. Реальную поставку создаём только после выбора даты, времени и явного подтверждения.")
                        if blocked_clusters or unknown_clusters:
                            st.info("Четыре недоступных направления НЕ блокируют подготовку остальных. Создание заявок пока не выполняется.")

                    st.caption("Этот ответ не подтверждает лимиты в штуках или свободный таймслот. Ничего автоматически не урезаем и не создаём.")

    elif not plans:
        st.caption("Рассчитай распределение во вкладке «📊 Распределение», чтобы увидеть соответствие кластеров.")
