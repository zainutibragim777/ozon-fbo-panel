"""Личная панель планирования Ozon FBO. API только для чтения, без создания заявок."""
import hmac
import math
import json
import urllib.request
import urllib.error
from datetime import date, timedelta
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
            excluded = st.multiselect("Исключить кластеры", clusters, default=[c for c in clusters if "самара" in c.lower()])
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
    geo_source = st.radio(
        "Источник процентов", ["Ввести доли вручную", "Excel из Ozon"],
        horizontal=True, key="geo_source",
    )
    geo = None
    if geo_source == "Excel из Ozon":
        category_file = st.file_uploader(
            "Загрузи Excel: Аналитика категорий → География, «Мой магазин» выключен",
            type=["xlsx"], key="category_geo_file",
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
            excluded = (geo["Доля, %"] < 0.5) | geo["Кластер"].str.contains("самара",case=False,na=False)
            eligible = geo.loc[~excluded].copy()
            st.caption(f"Кластеров в отчёте: {len(geo)}. В распределении: {len(eligible)}. Исключены доли ниже 0,5% и Самара.")
            qty = st.number_input("Количество для метода 2", min_value=0, value=1650, step=1, key="category_geo_qty")
            st.caption("Укажи реальные остатки FBO. Ноль означает, что остаток в кластере неизвестен или равен нулю — проверь перед расчётом.")
            stocks = eligible[["Кластер", "Доля, %"]].copy()
            stocks["Остаток FBO, шт."] = 0
            stocks = st.data_editor(
                stocks, hide_index=True, use_container_width=True, disabled=["Кластер", "Доля, %"],
                column_config={"Остаток FBO, шт.": st.column_config.NumberColumn(min_value=0,step=1)},
                key="category_geo_stock_table_v2",
            )
            if st.button(
                "Рассчитать по категории", type="primary", key="category_geo_calculate_v2",
                disabled=share_total < 99 or share_total > 100.2 or eligible.empty,
            ):
                vals = pd.to_numeric(stocks["Остаток FBO, шт."],errors="coerce")
                if vals.isna().any() or (vals < 0).any() or (vals % 1 != 0).any():
                    st.error("Остатки должны быть целыми неотрицательными числами.")
                else:
                    try:
                        plan = calculate([
                            (row["Кластер"], float(row["Доля, %"]), float(stock))
                            for (_, row), stock in zip(stocks.iterrows(), vals)
                        ], int(qty), 19)
                        result = stocks[stocks["Кластер"].isin(plan)].copy()
                        result["Отправить, шт."] = result["Кластер"].map(plan).astype(int)
                        result = result.sort_values("Отправить, шт.",ascending=False)
                        st.session_state.category_geo_result = result
                    except ValueError as error:
                        st.error(str(error))
            if "category_geo_result" in st.session_state:
                result = st.session_state.category_geo_result
                st.dataframe(result, hide_index=True, use_container_width=True)
                st.metric("Распределено, шт.", int(result["Отправить, шт."].sum()))
                st.download_button(
                    "Скачать план по категории CSV", result.to_csv(index=False).encode("utf-8-sig"),
                    "ozon_category_allocation.csv", "text/csv", key="category_download_v2",
                )
                st.warning("План предварительный. При изменении исходных данных нажми «Рассчитать» снова. Заявки не создаются.")
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
            excluded_samara = manual_table["Кластер"].str.contains("самара", case=False, na=False)
            if excluded_samara.any():
                st.info("Самара исключена из расчёта автоматически.")
            usable = manual_table.loc[~excluded_samara]
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
    st.write("Самара исключена по умолчанию.")
    st.write("Учитываем среднесуточные продажи в штуках и остатки FBO.")
    st.write("Один город — один склад назначения. Товары объединяем, если склад принимает всё.")
    st.write("Количество не уменьшаем без нового согласования.")
with tab_warehouses:
    st.warning("Реальная проверка складов и создание поставок пока не подключены.")
    st.write("В дальнейшем здесь появятся доступные даты и время для ручного выбора.")
