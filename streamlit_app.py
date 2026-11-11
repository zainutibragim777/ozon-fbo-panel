"""Личная панель планирования Ozon FBO. API только для чтения, без создания заявок."""
import hmac
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
st.caption("Панель без пароля: любой, у кого есть ссылка, может просматривать данные. Реальные заявки не создаются.")
st.info("Постоянная точка отгрузки: КАЗАНЬ_АЭРОПОРТ_КРОССДОКИНГ")
tab_plan, tab_stocks, tab_sales, tab_rules, tab_warehouses = st.tabs(["📊 Распределение", "📦 Остатки Ozon", "📈 Продажи Ozon", "⚙️ Правила", "🚚 Склады"])
with tab_plan:
    uploaded = st.file_uploader("Загрузи Excel или CSV с продажами и остатками", type=["xlsx", "csv"])
    if uploaded is not None:
        try:
            df = pd.read_excel(uploaded) if uploaded.name.lower().endswith(".xlsx") else pd.read_csv(uploaded)
        except Exception:
            st.error("Не удалось прочитать файл. Проверь его формат.")
            st.stop()
        if df.empty:
            st.error("Файл пустой.")
            st.stop()
        columns = list(df.columns)
        def select_column(title, key, patterns):
            preferred = next((c for c in columns if any(p in str(c).lower() for p in patterns)), columns[0])
            return st.selectbox(title, columns, index=columns.index(preferred), key=key)
        sku_col = select_column("Товар / артикул", "sku_column", ["артикул", "sku", "товар"])
        cluster_col = select_column("Кластер", "cluster_column", ["кластер", "регион", "город"])
        daily_col = select_column("Среднесуточные продажи, ШТУК в день", "daily_column", ["среднесут", "шт./день", "штук в день"])
        stock_col = select_column("Остатки FBO, ШТУК", "stock_column", ["остаток fbo", "fbo"])
        if len({sku_col, cluster_col, daily_col, stock_col}) != 4:
            st.warning("Нужно выбрать четыре разных столбца.")
        else:
            st.caption("Важно: продажи должны быть в штуках в день, не в рублях.")
            products = sorted(df[sku_col].dropna().astype(str).unique().tolist())
            if not products:
                st.error("В файле нет товаров.")
                st.stop()
            sku = st.selectbox("Товар", products)
            quantity = st.number_input("Количество к распределению, шт.", min_value=0, value=1650, step=1)
            minimum = st.number_input("Минимум на один товар и кластер", min_value=1, value=19, step=1)
            clusters = sorted(df[cluster_col].dropna().astype(str).unique().tolist())
            excluded = st.multiselect("Исключить кластеры", clusters, default=[c for c in clusters if any(city in c.lower() for city in ("самара", "махачкала"))])
            if st.button("Рассчитать", type="primary"):
                subset = df.loc[df[sku_col].astype(str) == sku, [cluster_col, daily_col, stock_col]].copy()
                subset[daily_col] = pd.to_numeric(subset[daily_col], errors="coerce")
                subset[stock_col] = pd.to_numeric(subset[stock_col], errors="coerce")
                if subset[[daily_col, stock_col]].isna().any().any() or (subset[[daily_col, stock_col]] < 0).any().any():
                    st.error("В продажах или остатках есть пустые, отрицательные или нечисловые значения.")
                else:
                    grouped = subset.groupby(cluster_col, as_index=False)[[daily_col, stock_col]].sum()
                    rows = [(r[cluster_col], r[daily_col], r[stock_col]) for _, r in grouped.iterrows() if str(r[cluster_col]) not in excluded]
                    try:
                        plan = calculate(rows, int(quantity), int(minimum))
                        output = pd.DataFrame([{"Кластер": c, "Отправить, шт.": n} for c, n in sorted(plan.items(), key=lambda p: -p[1])])
                        st.session_state.plan = output
                        st.session_state.plan_sku = sku
                    except ValueError as e:
                        st.error(str(e))
            if "plan" in st.session_state:
                st.subheader("Предварительный план: " + st.session_state.plan_sku)
                plan = st.session_state.plan
                st.dataframe(plan, use_container_width=True, hide_index=True)
                st.metric("Всего распределено", int(plan["Отправить, шт."].sum()) if not plan.empty else 0)
                st.download_button("Скачать план CSV", plan.to_csv(index=False).encode("utf-8-sig"), "ozon_fbo_plan.csv", "text/csv")
                st.warning("Доступность складов, ограничения маршрутов и зарубежные направления ещё не проверены.")

    st.divider()
    st.subheader("📊 Метод 2 — по географии продаж категории")
    st.caption("Распределение по долям продаж всей категории. Подходит и для новых товаров.")
    st.markdown("**Шаг 1. Выбери товар, для которого готовим поставку**")
    st.caption("SKU Ozon и артикул продавца — разные идентификаторы. В поставку попадёт только выбранный SKU.")
    if st.button("🔄 Загрузить товары из моего Ozon", key="category_load_products"):
        cid = str(st.secrets.get("OZON_CLIENT_ID", "")).strip()
        key = str(st.secrets.get("OZON_API_KEY", "")).strip()
        if not cid or not key:
            st.error("Нет OZON_CLIENT_ID / OZON_API_KEY в настройках панели.")
        else:
            try:
                products_by_sku = {}
                missing = []
                cursor = ""
                truncated = True
                for page_no in range(15):
                    req = urllib.request.Request(
                        "https://api-seller.ozon.ru/v3/product/list",
                        data=json.dumps({
                            "filter": {"visibility": "ALL"}, "last_id": cursor, "limit": 500,
                        }).encode("utf-8"),
                        headers={"Client-Id": cid, "Api-Key": key, "Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=35) as response:
                        payload = json.load(response)
                    data = payload.get("result") or {}
                    items = data.get("items") or []
                    if not isinstance(items, list):
                        raise ValueError("Ozon вернул неожиданный список товаров.")
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        raw_sku = item.get("sku")
                        sku_values = raw_sku if isinstance(raw_sku, list) else [raw_sku]
                        sku_values = [str(x).strip() for x in sku_values if str(x).strip().isdigit() and int(str(x).strip()) > 0]
                        article = str(item.get("offer_id") or "").strip()
                        name = str(item.get("name") or "").strip()
                        if not sku_values and item.get("product_id"):
                            missing.append(item)
                        for sku_value in sku_values:
                            products_by_sku[sku_value] = {
                                "sku": sku_value, "offer_id": article, "name": name,
                            }
                    next_cursor = str(data.get("last_id") or "")
                    if not items or not next_cursor or next_cursor == cursor:
                        truncated = False
                        break
                    cursor = next_cursor
                # Older Ozon responses might omit SKU from /v3/product/list.
                # Resolve those IDs only through the dedicated product information API.
                for start in range(0, len(missing), 500):
                    batch = [x.get("product_id") for x in missing[start:start + 500] if x.get("product_id")]
                    if not batch:
                        continue
                    req = urllib.request.Request(
                        "https://api-seller.ozon.ru/v3/product/info/list",
                        data=json.dumps({"product_id": batch}).encode("utf-8"),
                        headers={"Client-Id": cid, "Api-Key": key, "Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=35) as response:
                        info_response = json.load(response)
                    for item in info_response.get("items", []) or []:
                        stocks = item.get("stocks") or {}
                        entries = stocks.get("stocks") or [] if isinstance(stocks, dict) else []
                        skus = [str(x.get("sku") or "").strip() for x in entries if isinstance(x, dict)]
                        raw_sku = item.get("sku")
                        if raw_sku:
                            skus.append(str(raw_sku))
                        for sku_value in skus:
                            if sku_value.isdigit() and int(sku_value) > 0:
                                products_by_sku[sku_value] = {
                                    "sku": sku_value,
                                    "offer_id": str(item.get("offer_id") or "").strip(),
                                    "name": str(item.get("name") or "").strip(),
                                }
                st.session_state.category_ozon_catalog = products_by_sku
                st.session_state.category_ozon_catalog_truncated = truncated
                st.success(f"Загружено товаров (SKU): {len(products_by_sku)}.")
                if not products_by_sku:
                    st.warning("Ozon не вернул ни одного товара с действующим SKU.")
            except urllib.error.HTTPError as error:
                st.error(f"Не получилось загрузить каталог Ozon: HTTP {error.code}.")
            except Exception as error:
                st.error(f"Не получилось загрузить товары: {error}")

    product_catalog = st.session_state.get("category_ozon_catalog") or {}
    if st.session_state.get("category_ozon_catalog_truncated"):
        st.warning("Каталог большой: загружены не все карточки. Для полного списка потребуется дополнительная выгрузка.")
    sku_options = [""] + sorted(product_catalog, key=lambda sku: (
        (product_catalog[sku].get("offer_id") or "").casefold(), int(sku)
    ))
    if st.session_state.get("category_geo_product_choice", "") not in sku_options:
        st.session_state.category_geo_product_choice = ""
    selected_category_sku = st.selectbox(
        "Выбери товар Ozon (артикул / SKU)",
        sku_options,
        format_func=lambda sku: (
            "— Сначала выбери товар —" if not sku
            else f"{product_catalog[sku].get('name') or product_catalog[sku].get('offer_id') or 'Товар'}"
                 f" · артикул: {product_catalog[sku].get('offer_id') or '—'} · SKU {sku}"
        ),
        key="category_geo_product_choice",
    )
    previous_product = st.session_state.get("category_geo_previous_choice")
    if previous_product != selected_category_sku:
        for session_key in (
            "category_geo_result_new", "category_geo_result_qty_new",
            "category_geo_result_sku", "category_geo_result_offer_id",
            "technical_draft_id", "technical_draft_signature", "technical_draft_info",
            "preflight_export", "preflight_export_signature", "selected_cluster_slots",
        ):
            st.session_state.pop(session_key, None)
        st.session_state.category_geo_previous_choice = selected_category_sku
    if selected_category_sku:
        current_product = product_catalog[selected_category_sku]
        st.success(
            f"Выбран товар: {current_product.get('name') or current_product.get('offer_id') or 'без названия'}"
            f" · артикул продавца: {current_product.get('offer_id') or '—'}"
            f" · SKU Ozon: {selected_category_sku}"
        )
        st.markdown("**Шаг 2. Загрузи Excel по географии категории или введи доли**")
    else:
        st.info("Сначала нажми «Загрузить товары из моего Ozon» и выбери нужный товар. Excel пока загружать нельзя.")

    if selected_category_sku:
        geo_source = st.radio(
            "Источник процентов", ["Ввести доли вручную", "Excel из Ozon"],
            horizontal=True, index=1, key="geo_source",
        )
        geo = None
        if geo_source == "Excel из Ozon":
            category_file = st.file_uploader(
                "Загрузи Excel: Аналитика категорий → География, «Мой магазин» выключен",
                type=["xlsx"], key=f"category_geo_file_{selected_category_sku}",
            )
            if category_file is not None:
                try:
                    raw = pd.read_excel(category_file, header=None)
                    if raw.shape[0] < 2 or raw.shape[1] < 2:
                        raise ValueError(
                            "Ozon выгрузил пустой отчёт (только период, без таблицы). "
                            "Переключи «Источник процентов» на «Ввести доли вручную»."
                        )
                    header_idx = next((
                        i for i in range(min(len(raw), 40))
                        if any("кластер доставки" in str(v).lower() for v in raw.iloc[i])
                        and any("доля в категории" in str(v).lower() for v in raw.iloc[i])
                    ), None)
                    if header_idx is None:
                        raise ValueError(
                            "Не найдены столбцы «Кластер доставки» и «Доля в категории». "
                            "Проверь тип отчёта или введи доли вручную."
                        )
                    headers = raw.iloc[header_idx].astype(str).str.strip().tolist()
                    geo = raw.iloc[header_idx + 1:].copy()
                    geo.columns = headers
                    geo = geo[["Кластер доставки", "Доля в категории"]].copy()
                    geo.columns = ["Кластер", "Доля, %"]
                    geo["Кластер"] = geo["Кластер"].fillna("").astype(str).str.strip()
                    def parse_percent(value):
                        # Ozon exports numeric percentage cells as decimal fractions:
                        # 0.201 means 20.1%. Its row 6 is a text description, not data.
                        if pd.isna(value):
                            return float("nan")
                        try:
                            if isinstance(value, str):
                                v = value.replace(" ", "").replace("\\xa0", "").replace(",", ".").strip()
                                if v.endswith("%"):
                                    return float(v[:-1])
                                number = float(v)
                            else:
                                number = float(value)
                            return number * 100 if 0 <= number <= 1 else number
                        except (TypeError, ValueError):
                            return float("nan")
                    geo["Доля, %"] = pd.to_numeric(
                        geo["Доля, %"].map(parse_percent), errors="coerce"
                    )
                    geo = geo[(geo["Кластер"] != "") & geo["Доля, %"].notna()]
                except (ValueError, KeyError, IndexError) as error:
                    st.warning(str(error))
                    geo = None
                except Exception:
                    st.error("Не удалось открыть Excel. Можно ввести доли вручную.")
                    geo = None
        else:
            st.info("Введи проценты из столбца «Доля в категории» в Ozon. Можешь добавлять строки.")
            initial_geo = pd.DataFrame([
                {"Кластер": "Москва, МО и Дальние регионы", "Доля, %": 20.1},
                {"Кластер": "Ростов", "Доля, %": 9.2},
                {"Кластер": "Санкт-Петербург и СЗО", "Доля, %": 8.9},
                {"Кластер": "Новосибирск", "Доля, %": 5.7},
                {"Кластер": "Казань", "Доля, %": 5.4},
                {"Кластер": "Екатеринбург", "Доля, %": 5.2},
                {"Кластер": "Воронеж", "Доля, %": 5.1},
                {"Кластер": "Беларусь", "Доля, %": 4.5},
                {"Кластер": "Ярославль", "Доля, %": 4.2},
            ])
            st.caption("Первые строки заполнены по твоему скриншоту. Это НЕ все кластеры: добавь остальные перед расчётом.")
            geo = st.data_editor(
                initial_geo, num_rows="dynamic", hide_index=True, use_container_width=True,
                column_config={
                    "Кластер": st.column_config.TextColumn("Кластер", required=True),
                    "Доля, %": st.column_config.NumberColumn("Доля, %", min_value=0., max_value=100., format="%.2f"),
                },
                key="category_manual_shares",
            )
        if geo is not None:
            try:
                geo = geo.copy()
                geo["Кластер"] = geo["Кластер"].fillna("").astype(str).str.strip()
                geo = geo[geo["Кластер"] != ""]
                geo["Доля, %"] = pd.to_numeric(geo["Доля, %"], errors="coerce")
                if geo.empty or geo["Доля, %"].isna().any() or (geo["Доля, %"] < 0).any() or (geo["Доля, %"] > 100).any():
                    raise ValueError("Проверь названия кластеров и проценты от 0 до 100.")
                if geo["Кластер"].str.casefold().duplicated().any():
                    raise ValueError("Повторяются названия кластеров.")
                share_total = float(geo["Доля, %"].sum())
                if share_total > 100.2:
                    raise ValueError(f"Сумма долей {share_total:.2f}% — больше 100%. Проверь проценты.")
                if share_total < 99.0:
                    st.warning(
                        f"Введены не все кластеры: сумма долей {share_total:.1f}%, "
                        "должна быть около 100%. Расчёт заблокирован, чтобы не исказить распределение."
                    )
                excluded = (geo["Доля, %"] < 0.5) | geo["Кластер"].str.contains("самара|махачкала",case=False,na=False)
                eligible = geo.loc[~excluded].copy()
                st.caption(f"Кластеров в отчёте: {len(geo)}. В распределении: {len(eligible)}. Исключены доли ниже 0,5%, Самара и Махачкала.")
                qty = st.number_input("Количество для метода 2", min_value=0, value=1650, step=1, key="category_geo_qty")
                st.caption("Метод для новых товаров: остатки FBO не нужны. Распределяем только по долям рынка категории.")
                if st.button(
                    "Рассчитать по категории", type="primary", key="category_geo_calculate_v3",
                    disabled=share_total < 99 or share_total > 100.2 or eligible.empty,
                ):
                    try:
                        plan = calculate([
                            (row["Кластер"], float(row["Доля, %"]), 0.0)
                            for _, row in eligible.iterrows()
                        ], int(qty), 19)
                        result = eligible[eligible["Кластер"].isin(plan)][["Кластер", "Доля, %"]].copy()
                        result["Отправить, шт."] = result["Кластер"].map(plan).astype(int)
                        result = result.sort_values("Отправить, шт.", ascending=False)
                        result["SKU Ozon"] = selected_category_sku
                        result["Артикул продавца"] = str(current_product.get("offer_id") or "")
                        st.session_state.category_geo_result_new = result
                        st.session_state.category_geo_result_qty_new = int(qty)
                        st.session_state.category_geo_result_sku = selected_category_sku
                        st.session_state.category_geo_result_offer_id = current_product.get("offer_id", "")
                    except ValueError as error:
                        st.session_state.pop("category_geo_result_new", None)
                        st.error(str(error))
                if st.session_state.get("category_geo_result_sku") != selected_category_sku:
                    st.session_state.pop("category_geo_result_new", None)
                if "category_geo_result_new" in st.session_state:
                    st.caption(f"Товар в плане: {current_product.get('offer_id') or '—'} · SKU Ozon {selected_category_sku}")
                    result = st.session_state.category_geo_result_new
                    st.dataframe(result, hide_index=True, use_container_width=True)
                    st.metric("Распределено, шт.", int(result["Отправить, шт."].sum()))
                    st.download_button(
                        "Скачать план по категории CSV", result.to_csv(index=False).encode("utf-8-sig"),
                        f"ozon_category_allocation_{selected_category_sku}.csv", "text/csv", key="category_download_v3",
                    )
                    st.warning("План предварительный, без проверки складов. При изменении исходных данных нажми «Рассчитать» снова. Заявки не создаются.")
            except ValueError as error:
                st.error(str(error))

    st.divider()
    st.subheader("🐊 Быстрый расчёт по кластерам")
    st.caption("Пока Ozon не отдаёт географию продаж автоматически, введи продажи и остатки по кластерам вручную. Это предварительный расчёт, без создания поставок.")
    if "ozon_sales_rows" in st.session_state:
        available_skus = [r for r in st.session_state.ozon_sales_rows if str(r.get("SKU", "")).strip()]
        if available_skus:
            chosen = st.selectbox(
                "Товар из Ozon",
                available_skus,
                format_func=lambda r: str(r.get("Товар") or r.get("SKU"))[:85] + " · " + str(r.get("SKU")),
                key="manual_plan_product",
            )
            st.caption(f"Общие продажи: {chosen.get('В день, шт.', 0)} шт./день. Для расчёта ниже нужна разбивка по кластерам.")
    else:
        st.info("Для выбора товара сначала открой вкладку «Продажи Ozon» и нажми «Получить продажи из Ozon».")
    manual_quantity = st.number_input("Сколько единиц распределить", min_value=0, value=1650, step=1, key="manual_plan_quantity")
    st.caption("Введи среднесуточные продажи по каждому кластеру и реальные остатки FBO в нём. Добавляй строки кнопкой внизу таблицы.")
    manual_default = pd.DataFrame([
        {"Кластер": "", "Продажи, шт./день": 0.0, "Остаток FBO, шт.": 0.0},
    ])
    manual_table = st.data_editor(
        manual_default, num_rows="dynamic", hide_index=True, use_container_width=True,
        column_config={
            "Кластер": st.column_config.TextColumn("Кластер", required=True),
            "Продажи, шт./день": st.column_config.NumberColumn("Продажи, шт./день", min_value=0.0, format="%.2f"),
            "Остаток FBO, шт.": st.column_config.NumberColumn("Остаток FBO, шт.", min_value=0.0, format="%.0f"),
        },
        key="manual_cluster_editor",
    )
    if st.button("Рассчитать по кластерам", key="manual_cluster_calculate", type="primary"):
        try:
            manual_table = manual_table.copy()
            manual_table["Кластер"] = manual_table["Кластер"].fillna("").astype(str).str.strip()
            manual_table = manual_table[manual_table["Кластер"] != ""]
            if manual_table.empty:
                raise ValueError("Сначала заполни названия кластеров и их продажи.")
            if manual_table["Кластер"].str.lower().duplicated().any():
                raise ValueError("Один кластер указан несколько раз. Объедини его в одну строку.")
            for field in ("Продажи, шт./день", "Остаток FBO, шт."):
                manual_table[field] = pd.to_numeric(manual_table[field], errors="coerce")
                if manual_table[field].isna().any() or (manual_table[field] < 0).any():
                    raise ValueError(f"Проверь значения в столбце «{field}».")
            if (manual_table["Остаток FBO, шт."] % 1 != 0).any():
                raise ValueError("Остатки должны быть целыми штуками.")
            excluded_cities = manual_table["Кластер"].str.contains("самара|махачкала", case=False, na=False)
            if excluded_cities.any():
                st.info("Самара и Махачкала исключаются из расчёта автоматически.")
            usable = manual_table.loc[~excluded_cities]
            rows = [(r["Кластер"], r["Продажи, шт./день"], r["Остаток FBO, шт."]) for _, r in usable.iterrows()]
            plan = calculate(rows, int(manual_quantity), 19)
            result = pd.DataFrame([{"Кластер": k, "Отправить, шт.": v} for k, v in sorted(plan.items(), key=lambda x: -x[1])])
            st.session_state.manual_cluster_plan = result
            st.session_state.manual_cluster_plan_quantity = int(manual_quantity)
        except ValueError as error:
            st.session_state.pop("manual_cluster_plan", None)
            st.error(str(error))
    if "manual_cluster_plan" in st.session_state:
        result = st.session_state.manual_cluster_plan
        st.success(f"Распределено {int(result['Отправить, шт.'].sum())} из {st.session_state.manual_cluster_plan_quantity} шт.")
        st.dataframe(result, use_container_width=True, hide_index=True)
        st.download_button("Скачать предварительный план CSV", result.to_csv(index=False).encode("utf-8-sig"), "manual_cluster_plan.csv", "text/csv", key="manual_cluster_download")
        st.warning("Это расчёт по введённым вручную данным. Склады, лимиты приёмки, даты и маршруты ещё не проверены. Не используй его как подтверждённую поставку.")

with tab_stocks:
    st.subheader("Актуальные остатки FBO")
    st.caption("Только чтение данных Ozon. Не создаёт и не изменяет поставки.")
    client_id = str(st.secrets.get("OZON_CLIENT_ID", ""))
    api_key = str(st.secrets.get("OZON_API_KEY", ""))
    if not client_id or not api_key:
        st.warning("Для подключения добавь OZON_CLIENT_ID и OZON_API_KEY в Streamlit Secrets. Пароль APP_PASSWORD не удаляй.")
    elif st.button("🔄 Обновить остатки из Ozon", type="primary"):
        try:
            records = []
            limit = 1000
            offset = 0
            # Bounded pagination avoids an accidental long-running request.
            for _ in range(30):
                request = urllib.request.Request(
                    "https://api-seller.ozon.ru/v2/analytics/stock_on_warehouses",
                    data=json.dumps({"limit": limit, "offset": offset, "warehouse_type": "ALL"}).encode("utf-8"),
                    headers={"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = json.load(response)
                result = payload.get("result", {}) if isinstance(payload, dict) else {}
                page = result.get("rows", []) if isinstance(result, dict) else []
                if not isinstance(page, list):
                    raise ValueError("Ozon вернул неожиданный формат данных.")
                records.extend(page)
                if len(page) < limit:
                    break
                offset += limit
            st.session_state.ozon_stock_rows = records
            st.session_state.ozon_stock_truncated = len(page) == limit
        except urllib.error.HTTPError as error:
            st.error(f"Ozon вернул HTTP {error.code}. Проверь доступ к методу и ключи.")
        except Exception:
            st.error("Не удалось получить остатки. Попробуй позже.")
    if "ozon_stock_rows" in st.session_state:
        records = st.session_state.ozon_stock_rows
        st.success(f"Получено строк: {len(records)}")
        if st.session_state.get("ozon_stock_truncated"):
            st.warning("Получены не все данные: достигнут предел загрузки.")
        if records:
            stock_df = pd.DataFrame(records)
            st.dataframe(stock_df, use_container_width=True, hide_index=True)
            st.download_button("Скачать остатки CSV", stock_df.to_csv(index=False).encode("utf-8-sig"), "ozon_fbo_stocks.csv", "text/csv")
        st.caption("Эти остатки пока не подставляются в расчёт: сначала проверим соответствие складов кластерам и товаров артикулам.")

with tab_sales:
    st.subheader("Продажи Ozon по товарам")
    st.caption("За выбранный период, в штуках. Только чтение — поставки не создаются.")
    sales_days = st.selectbox("Период", [7, 14, 28], index=2, format_func=lambda n: f"Последние {n} завершённых дней")
    if not client_id or not api_key:
        st.warning("Добавь OZON_CLIENT_ID и OZON_API_KEY в Streamlit Secrets.")
    elif st.button("🔄 Получить продажи из Ozon", type="primary"):
        try:
            date_to = date.today() - timedelta(days=1)
            date_from = date_to - timedelta(days=sales_days - 1)
            sales_rows = []
            limit = 1000
            for offset in range(0, 30000, limit):
                body = {
                    "date_from": date_from.isoformat(),
                    "date_to": date_to.isoformat(),
                    "metrics": ["ordered_units"],
                    "dimension": ["sku"],
                    "filters": [],
                    "sort": [],
                    "limit": limit,
                    "offset": offset,
                }
                request = urllib.request.Request(
                    "https://api-seller.ozon.ru/v1/analytics/data",
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = json.load(response)
                result = payload.get("result", {}) if isinstance(payload, dict) else {}
                page = result.get("data", []) if isinstance(result, dict) else []
                if not isinstance(page, list):
                    raise ValueError("Неожиданный формат ответа Ozon")
                for item in page:
                    dimensions = item.get("dimensions", [])
                    sku = dimensions[0].get("id", "") if dimensions else ""
                    label = dimensions[0].get("name", "") if dimensions else ""
                    metrics = item.get("metrics", [])
                    units = metrics[0] if metrics else 0
                    sales_rows.append({"SKU": str(sku), "Товар": label, "Продано, шт.": units, "В день, шт.": round(float(units) / sales_days, 2)})
                if len(page) < limit:
                    break
            st.session_state.ozon_sales_rows = sales_rows
            st.session_state.ozon_sales_period = (date_from.isoformat(), date_to.isoformat())
            st.session_state.ozon_sales_truncated = len(page) == limit
        except urllib.error.HTTPError as error:
            st.error(f"Ozon вернул HTTP {error.code}. Проверь права API-ключа.")
        except Exception:
            st.error("Не удалось получить продажи. Попробуй позже.")
    if "ozon_sales_rows" in st.session_state:
        sales_df = pd.DataFrame(st.session_state.ozon_sales_rows, columns=["SKU", "Товар", "Продано, шт.", "В день, шт."])
        start, end = st.session_state.ozon_sales_period
        st.success(f"Получено товаров: {len(sales_df)}. Период: {start} — {end}")
        if st.session_state.get("ozon_sales_truncated"):
            st.warning("Достигнут предел выгрузки: данные могут быть неполными.")
        st.dataframe(sales_df, use_container_width=True, hide_index=True)
        st.download_button("Скачать продажи CSV", sales_df.to_csv(index=False).encode("utf-8-sig"), "ozon_sales.csv", "text/csv")
        st.info("Пока это продажи по SKU, без разбивки по кластерам. Для распределения поставок нужна отдельная проверка продаж по кластерам и соответствия остатков.")

with tab_rules:
    st.write("Минимум 19 шт. **каждого товара** на кластер.")
    st.write("Самара и Махачкала исключены по умолчанию.")
    st.write("Учитываем среднесуточные продажи в штуках и остатки FBO.")
    st.write("Один город — один склад назначения. Товары объединяем, если склад принимает всё.")
    st.write("Количество не уменьшаем без нового согласования.")
with tab_warehouses:
    current_selected_sku = str(st.session_state.get("category_geo_result_sku") or "")
    selected_in_picker = str(st.session_state.get("category_geo_product_choice") or "")
    show_old_crocodile_plan = (
        current_selected_sku == "2143372681"
        and selected_in_picker == current_selected_sku
    )
    if show_old_crocodile_plan:
        st.warning("Это историческая проверка товара КРОКОДИЛ (SKU 2143372681). Слоты и количества относятся только к нему.")
        st.subheader("📅 Разные даты и время для каждого кластера")
        st.caption("Один кластер — одна будущая поставка. Здесь выбираем диапазон дат и часов, затем свободный интервал для каждого направления отдельно.")
        calendar_path = Path(__file__).with_name("individual_slot_summary.json")
        if not calendar_path.exists():
            st.info("Проверка отдельных кластеров в Ozon ещё не завершена. После получения результатов здесь появятся доступные интервалы.")
        else:
            try:
                calendar_data = json.loads(calendar_path.read_text(encoding="utf-8"))
                cluster_slots = calendar_data.get("clusters") or []
                checked_at = str(calendar_data.get("checked_at_utc", ""))
                st.caption(f"Последняя проверка: {checked_at} UTC. Свободные интервалы могли измениться — это не бронь.")
                starts = [
                    date.fromisoformat(item["date"])
                    for cluster in cluster_slots for item in cluster.get("slots", [])
                    if item.get("date")
                ]
                default_from = min(starts) if starts else date.today()
                default_to = max(starts) if starts else date.today() + timedelta(days=13)
                picked_range = st.date_input(
                    "Диапазон дат",
                    value=(default_from, default_to),
                    key="independent_date_range",
                )
                hours_from = st.time_input("Время с", value=time(8, 0), step=3600, key="independent_time_from")
                hours_to = st.time_input("Время до", value=time(22, 0), step=3600, key="independent_time_to")
                if not isinstance(picked_range, (tuple, list)) or len(picked_range) != 2:
                    st.info("Выбери обе даты диапазона.")
                elif hours_from >= hours_to:
                    st.warning("Конец временного диапазона должен быть позже начала.")
                else:
                    start_date, end_date = picked_range
                    options_by_cluster = {}
                    for cluster in cluster_slots:
                        available_options = []
                        for slot in cluster.get("slots", []):
                            try:
                                begin = datetime.fromisoformat(slot["from"])
                                finish = datetime.fromisoformat(slot["to"])
                            except (KeyError, ValueError, TypeError):
                                continue
                            if start_date <= begin.date() <= end_date and begin.time() >= hours_from and finish.time() <= hours_to:
                                available_options.append((begin, finish))
                        available_options.sort(key=lambda pair: pair[0])
                        options_by_cluster[cluster["name"]] = available_options
                    slot_summary = pd.DataFrame([{
                        "Кластер": item["name"],
                        "Количество, шт.": int(item["quantity"]),
                        "Статус проверки": (
                            "Слоты найдены" if item.get("status") == "SLOTS_FOUND"
                            else "Ошибка API — повторяем" if item.get("status") in ("HTTP_ERROR", "ERROR")
                            else str(item.get("status") or "Не проверен")
                        ),
                        "Доступных интервалов": len(options_by_cluster.get(item["name"], [])),
                        "Первый подходящий": (
                            options_by_cluster[item["name"]][0][0].strftime("%d.%m %H:%M")
                            if options_by_cluster.get(item["name"]) else "Нет в диапазоне"
                        ),
                    } for item in cluster_slots])
                    st.dataframe(slot_summary, hide_index=True, use_container_width=True)
                    if cluster_slots:
                        selected_name = st.selectbox(
                            "Выбери кластер для выбора времени",
                            [item["name"] for item in cluster_slots],
                            key="selected_individual_cluster",
                        )
                        choices = options_by_cluster.get(selected_name, [])
                        if choices:
                            selected_slot_index = st.selectbox(
                                "Свободное время для этого кластера (Москва)",
                                range(len(choices)),
                                format_func=lambda i: choices[i][0].strftime("%d.%m.%Y %H:%M") + "–" + choices[i][1].strftime("%H:%M"),
                                key="selected_individual_slot",
                            )
                            if st.button("Сохранить выбранный интервал", key="save_individual_slot"):
                                chosen_start, chosen_end = choices[selected_slot_index]
                                if "selected_cluster_slots" not in st.session_state:
                                    st.session_state.selected_cluster_slots = {}
                                st.session_state.selected_cluster_slots[selected_name] = {
                                    "Кластер": selected_name,
                                    "Количество, шт.": next(int(item["quantity"]) for item in cluster_slots if item["name"] == selected_name),
                                    "Дата": chosen_start.date().isoformat(),
                                    "С": chosen_start.strftime("%H:%M"),
                                    "До": chosen_end.strftime("%H:%M"),
                                }
                                st.success("Выбор сохранён в панели. Поставка НЕ создана.")
                        else:
                            st.warning("Для этого кластера пока нет подтверждённых слотов в выбранном диапазоне. Если Ozon вернул ошибку API, это НЕ означает отсутствие свободных мест.")
                        if st.session_state.get("selected_cluster_slots"):
                            chosen_df = pd.DataFrame(list(st.session_state.selected_cluster_slots.values()))
                            st.subheader("Сохранённые варианты поставок")
                            st.dataframe(chosen_df, hide_index=True, use_container_width=True)
                            st.download_button(
                                "Скачать выбранные даты CSV",
                                chosen_df.to_csv(index=False).encode("utf-8-sig"),
                                "ozon_individual_slot_choices.csv",
                                "text/csv",
                                key="individual_chosen_slots_csv",
                            )
                            st.caption("Сохранение выбора не бронирует слот и не создаёт поставку. Перед оформлением потребуется обновить доступность и подтвердить создание.")
            except (OSError, ValueError, TypeError, KeyError) as error:
                st.error(f"Не удалось прочитать интервалы: {error}")
        st.divider()
        st.subheader("📦 Отдельное оформление доступных направлений")
        st.caption("Сохранённый снимок проверки Ozon от 10.11.2026 для КРОКОДИЛА, SKU 2143372681. Видно всегда, даже после обновления страницы. Статусы могут измениться.")
        snapshot_rows = [["Москва, МО и Дальние регионы",4039,357,"Доступен"],["Ростов",4071,163,"Доступен"],["Санкт-Петербург и СЗО",4007,158,"Доступен"],["Новосибирск",4067,101,"Доступен"],["Казань",4041,96,"Доступен"],["Екатеринбург",4066,92,"Доступен"],["Воронеж",4036,91,"Ожидает"],["Беларусь",4001,80,"Ожидает"],["Ярославль",4051,75,"Доступен"],["Красноярск",4043,66,"Ожидает"],["Уфа",4040,62,"Ожидает"],["Тюмень",4046,60,"Доступен"],["Краснодар",4065,60,"Доступен"],["Дальний Восток",4002,57,"Доступен"],["Саратов",4049,48,"Доступен"],["Невинномысск",4076,36,"Доступен"],["Пермь",4070,25,"Доступен"],["Омск",4068,23,"Доступен"]]
        snapshot = pd.DataFrame(snapshot_rows, columns=["Кластер", "Макролокальный ID", "Отправить, шт.", "Статус на 10.11.2026"])
        ready_snapshot = snapshot[snapshot["Статус на 10.11.2026"] == "Доступен"].copy()
        waiting_snapshot = snapshot[snapshot["Статус на 10.11.2026"] == "Ожидает"].copy()
        st.write(
            f"**Группа 1 — оформляем отдельно:** {len(ready_snapshot)} кластеров, "
            f"**{int(ready_snapshot['Отправить, шт.'].sum())} шт.**  "
            f"**Группа 2 — мониторинг:** {len(waiting_snapshot)} кластера, "
            f"**{int(waiting_snapshot['Отправить, шт.'].sum())} шт.**"
        )
        if int(snapshot["Отправить, шт."].sum()) != 1650:
            st.error("Ошибка контрольной суммы. Данные снимка не использовать!")
        else:
            st.dataframe(ready_snapshot, hide_index=True, use_container_width=True)
            st.download_button(
                "Скачать план для 14 доступных кластеров CSV",
                ready_snapshot.to_csv(index=False).encode("utf-8-sig"),
                "ozon_14_ready_clusters.csv", "text/csv", key="snapshot_14_csv",
            )
            with st.expander("Показать 4 направления в ожидании"):
                st.dataframe(waiting_snapshot, hide_index=True, use_container_width=True)
            st.info("Это сохранённый план, а не действующие заявки. Для создания каждой реальной поставки ещё нужны актуальная проверка Ozon, свободный слот и твоё подтверждение.")
        st.divider()
    else:
        st.info("Старые слоты и план КРОКОДИЛА скрыты. Для выбранного нового товара проверь свой рассчитанный план ниже. Даты проверяются отдельно под его SKU.")
    st.subheader("🚚 Кластеры Ozon для кросс-докинга")
    st.caption("Точка отгрузки: КАЗАНЬ_АЭРОПОРТ_КРОССДОКИНГ. Для кросс-докинга Ozon планирует поставки на кластеры.")
    st.warning("Пока только просмотр справочника Ozon. Доступность приёмки, лимиты и даты не подтверждены. Реальные поставки не создаются.")
    client_id_wh = str(st.secrets.get("OZON_CLIENT_ID", ""))
    api_key_wh = str(st.secrets.get("OZON_API_KEY", ""))

    def ozon_reference_request(endpoint, body):
        request = urllib.request.Request(
            "https://api-seller.ozon.ru" + endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Client-Id": client_id_wh, "Api-Key": api_key_wh, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    if not client_id_wh or not api_key_wh:
        st.error("Не найдены API-ключи в Streamlit Secrets.")
    elif st.button("🔄 Загрузить кластеры и точку отгрузки из Ozon", type="primary", key="fetch_fbo_clusters"):
        try:
            all_clusters = []
            for cluster_type in ("CLUSTER_TYPE_OZON", "CLUSTER_TYPE_CIS"):
                response = ozon_reference_request("/v1/cluster/list", {"cluster_type": cluster_type})
                cluster_list = response.get("clusters", []) if isinstance(response, dict) else []
                if not isinstance(cluster_list, list):
                    raise ValueError("Ozon вернул неожиданный формат кластеров.")
                all_clusters.extend({**item, "_requested_cluster_type": cluster_type} for item in cluster_list if isinstance(item, dict))
            macro_response = ozon_reference_request("/v2/cluster/list", {})
            macro_result = macro_response.get("result", []) if isinstance(macro_response, dict) else []
            if not isinstance(macro_result, list) or not macro_result:
                raise ValueError("Ozon не вернул макролокальные кластеры /v2/cluster/list.")
            def clean_cluster_name(value):
                return " ".join(str(value).strip().casefold().replace("ё", "е").split())
            macro_by_name = {}
            for record in macro_result:
                if not isinstance(record, dict):
                    continue
                detail = (record.get("data") or {}).get("macrolocal_cluster") or {}
                name = str(detail.get("name") or "").strip()
                macro_id = record.get("macrolocal_cluster_id")
                if name and macro_id is not None:
                    macro_by_name[clean_cluster_name(name)] = {
                        "macrolocal_cluster_id": macro_id,
                        "country": str((detail.get("country") or {}).get("name") or ""),
                    }
            enriched_clusters = []
            for item in all_clusters:
                name = str(item.get("name") or "").strip()
                macro = macro_by_name.get(clean_cluster_name(name), {})
                # v1 ID belongs to a legacy cluster and MUST NOT be sent as macrolocal_cluster_id.
                candidate = item.get("macrolocal_cluster_id") or macro.get("macrolocal_cluster_id")
                enriched_clusters.append({
                    **item,
                    "_macrolocal_cluster_id": candidate,
                    "_country": macro.get("country", ""),
                    "_macro_id_verified": candidate is not None,
                })
            st.session_state.fbo_cluster_reference = enriched_clusters
            st.session_state.fbo_cluster_error = None
        except urllib.error.HTTPError as err:
            st.session_state.fbo_cluster_error = f"Кластеры: Ozon вернул HTTP {err.code}."
        except Exception as err:
            st.session_state.fbo_cluster_error = f"Кластеры: {err}"
        try:
            dropoff_response = ozon_reference_request(
                "/v1/warehouse/fbo/list",
                {"filter_by_supply_type": ["CREATE_TYPE_CROSSDOCK"], "search": "КАЗАНЬ_АЭРОПОРТ"},
            )
            found_points = dropoff_response.get("search", []) if isinstance(dropoff_response, dict) else []
            st.session_state.fbo_dropoff_points = found_points if isinstance(found_points, list) else []
            st.session_state.fbo_dropoff_error = None
        except urllib.error.HTTPError as err:
            st.session_state.fbo_dropoff_error = f"Точка отгрузки: Ozon вернул HTTP {err.code}."
        except Exception as err:
            st.session_state.fbo_dropoff_error = f"Точка отгрузки: {err}"

    if st.session_state.get("fbo_cluster_error"):
        st.error(st.session_state.fbo_cluster_error)
    if st.session_state.get("fbo_dropoff_error"):
        st.warning(st.session_state.fbo_dropoff_error)
    if "fbo_cluster_reference" in st.session_state:
        clusters_ref = st.session_state.fbo_cluster_reference
        cluster_rows = [
            {"Кластер Ozon": str(x.get("name", "")), "ID кластера": str(x.get("id", "")),
             "Макролокальный ID": str(x.get("_macrolocal_cluster_id") or ""),
             "Страна": x.get("_country") or ("СНГ" if x.get("_requested_cluster_type") == "CLUSTER_TYPE_CIS" else "Россия")}
            for x in clusters_ref if isinstance(x, dict) and x.get("name")
        ]
        st.success(f"Получено кластеров Ozon: {len(cluster_rows)}")
        st.dataframe(pd.DataFrame(cluster_rows), hide_index=True, use_container_width=True)
        st.download_button("Скачать справочник кластеров", pd.DataFrame(cluster_rows).to_csv(index=False).encode("utf-8-sig"),
                           "ozon_fbo_clusters.csv", "text/csv", key="download_fbo_clusters")
    if "fbo_dropoff_points" in st.session_state:
        points = st.session_state.fbo_dropoff_points
        st.subheader("Точка отгрузки — Казань")
        if points:
            point_rows = [{
                "Название": str(p.get("name", "")),
                "ID": str(p.get("warehouse_id", "")),
                "Адрес": str(p.get("address", "")),
                "Тип": str(p.get("warehouse_type", "")),
            } for p in points if isinstance(p, dict)]
            st.dataframe(pd.DataFrame(point_rows), hide_index=True, use_container_width=True)
        else:
            st.info("Точка отгрузки не найдена по поиску. Нужно уточнить её название в Ozon.")

    plans = []
    if "category_geo_result_new" in st.session_state:
        plans.append(("Метод 2 — новый товар", "category_geo_result_new"))
    if "manual_cluster_plan" in st.session_state:
        plans.append(("Метод 1 — ручные данные", "manual_cluster_plan"))
    if "plan" in st.session_state:
        plans.append(("Метод 1 — Excel", "plan"))
    if plans and "fbo_cluster_reference" in st.session_state:
        st.divider()
        st.subheader("Сопоставление плана с кластерами Ozon")
        source_label = st.selectbox("План распределения", [p[0] for p in plans], key="fbo_cluster_plan_source")
        source_key = next(key for label, key in plans if label == source_label)
        planning = st.session_state[source_key][["Кластер", "Отправить, шт."]].copy()
        official = st.session_state.fbo_cluster_reference
        named = {str(cl.get("name", "")).strip().casefold(): cl for cl in official if isinstance(cl, dict)}
        def normalized_name(name):
            return " ".join(str(name).strip().casefold().replace("ё", "е").split())
        names = {normalized_name(cl.get("name", "")): cl for cl in official if isinstance(cl, dict)}
        matches = []
        for _, row in planning.iterrows():
            name = str(row["Кластер"]).strip()
            hit = names.get(normalized_name(name))
            matches.append({
                "Кластер из плана": name,
                "Кластер Ozon": str(hit.get("name", "")) if hit else "",
                "ID кластера Ozon": str(hit.get("_macrolocal_cluster_id") or "") if hit else "",
                "Отправить, шт.": int(row["Отправить, шт."]),
                "Статус": "Совпало" if hit and hit.get("_macrolocal_cluster_id") else "Нет макролокального ID",
            })
        matched_df = pd.DataFrame(matches)
        st.dataframe(matched_df, hide_index=True, use_container_width=True)
        matched = sum(item["Статус"] == "Совпало" for item in matches)
        st.info(f"Проверено макролокальных ID: {matched} из {len(matches)}. Отсутствующие ID не передаются в черновик.")
        mapping_export = matched_df.copy()
        if source_key == "category_geo_result_new":
            mapping_export["SKU Ozon"] = str(st.session_state.get("category_geo_result_sku") or "")
            mapping_export["Артикул продавца"] = str(st.session_state.get("category_geo_result_offer_id") or "")
        st.download_button(
            "Скачать сопоставление CSV", mapping_export.to_csv(index=False).encode("utf-8-sig"),
            "ozon_cluster_mapping.csv", "text/csv", key="download_cluster_mapping",
        )

        st.divider()
        st.subheader("🔎 Предварительная проверка перед поставкой")
        st.caption("Проверка локальная: не создаёт черновиков и не запрашивает неподтверждённые лимиты Ozon.")
        total_to_supply = int(matched_df["Отправить, шт."].sum())
        origin_matches = [
            p for p in st.session_state.get("fbo_dropoff_points", [])
            if "КАЗАНЬ" in str(p.get("name", "")).upper()
            and "АЭРОПОРТ" in str(p.get("name", "")).upper()
            and "КРОССДОКИНГ" in str(p.get("name", "")).upper()
        ]
        origin_ok = len(origin_matches) == 1 and bool(origin_matches[0].get("warehouse_id"))
        all_matched = matched == len(matches)
        minimum_ok = bool((matched_df["Отправить, шт."] >= 19).all())
        unique_clusters_ok = not matched_df["Кластер из плана"].duplicated().any()
        no_excluded_cities = not matched_df["Кластер из плана"].str.contains("самара|махачкала", case=False, na=False).any()
        st.write(f"План: **{total_to_supply} шт.**, кластеров: **{len(matches)}**.")
        st.write(f"{'✅' if all_matched else '❌'} Все кластеры имеют ID Ozon.")
        st.write(f"{'✅' if origin_ok else '❌'} Точка отгрузки Казань найдена однозначно.")
        st.write(f"{'✅' if minimum_ok else '❌'} Минимум 19 штук в каждом кластере.")
        st.write(f"{'✅' if unique_clusters_ok and no_excluded_cities else '❌'} Нет повторов кластеров, Самары и Махачкалы.")
        if source_key == "category_geo_result_new":
            new_product_sku = str(st.session_state.get("category_geo_result_sku") or "")
            source_offer_id = str(st.session_state.get("category_geo_result_offer_id") or "")
            st.write(f"**Товар из метода 2:** артикул продавца **{source_offer_id or '—'}**, SKU Ozon **{new_product_sku or 'НЕ ВЫБРАН'}**.")
            st.caption("SKU привязан к выбранному товару. Здесь его нельзя поменять вручную.")
        else:
            new_product_sku = st.text_input(
                "SKU Ozon для этого плана (обязательно)", value="", key="preflight_sku",
                help="Не артикул продавца и не ID карточки. Введи именно SKU Ozon."
            )
        product_matches_plan = (
            source_key != "category_geo_result_new"
            or (
                new_product_sku != ""
                and new_product_sku == str(st.session_state.get("category_geo_product_choice") or "")
            )
        )
        if not product_matches_plan:
            st.error("Товар поменялся после расчёта! Вернись в Метод 2 и рассчитай заново.")
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
