"""Личная панель планирования Ozon FBO. Без запросов к API и создания заявок."""
import hmac
import math
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Ozon FBO Control", page_icon="📦", layout="wide")

def check_login():
    try:
        password = str(st.secrets.get("APP_PASSWORD", ""))
    except Exception:
        password = ""
    if not password:
        st.error("Панель закрыта: настрой APP_PASSWORD в Streamlit Secrets перед запуском.")
        st.stop()
    if not st.session_state.get("logged_in", False):
        st.title("🔒 Личная панель Ozon FBO")
        value = st.text_input("Пароль", type="password")
        if st.button("Войти", type="primary"):
            if hmac.compare_digest(value, password):
                st.session_state.logged_in = True
                st.rerun()
            else:
                st.error("Неверный пароль")
        st.stop()

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

check_login()
st.title("📦 Ozon FBO Control")
st.caption("Первая версия — только планирование. Реальные заявки не создаются.")
st.info("Постоянная точка отгрузки: КАЗАНЬ_АЭРОПОРТ_КРОССДОКИНГ")
if st.button("Выйти"):
    st.session_state.logged_in = False
    st.rerun()

tab_plan, tab_rules, tab_warehouses = st.tabs(["📊 Распределение", "⚙️ Правила", "🚚 Склады"])
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
with tab_rules:
    st.write("Минимум 19 шт. **каждого товара** на кластер.")
    st.write("Самара исключена по умолчанию.")
    st.write("Учитываем среднесуточные продажи в штуках и остатки FBO.")
    st.write("Один город — один склад назначения. Товары объединяем, если склад принимает всё.")
    st.write("Количество не уменьшаем без нового согласования.")
with tab_warehouses:
    st.warning("Реальная проверка складов и создание поставок пока не подключены.")
    st.write("В дальнейшем здесь появятся доступные даты и время для ручного выбора.")
