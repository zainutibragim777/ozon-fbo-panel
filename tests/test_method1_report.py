"""Regression checks for Ozon availability report headers and unit selection."""
from method1_report import find_method1_header_row, suggest_column, suggest_method1_column

rows = [
    ("Период: 15.10.2026 - 11.11.2026",),
    ("Дата обновления: 12.11.2026 01:34 МСК",),
    ("Фильтры: FBO, FBS",),
    ("Рекомендуемая поставка: 28 дней",),
    (),
    (
        "SKU", "Артикул", "Название товара",
        "Рекомендуемая поставка, шт на 28 дней", "Рекомендация", "Кластер",
        "Схема продаж", "Дней без остатка за 28 дней", "Доля локальных продаж",
        "Среднесуточные продажи, руб. за 28дн", "Признак товара",
        "До конца остатка FBO, дн", "До конца остатка FBS, дн",
        "Остаток FBO, шт", "Остаток FBS, шт",
        "Товары в пути на склад озон, шт",
        "Среднесуточные продажи, шт. за 28дн",
    ),
]
assert find_method1_header_row(rows) == 5

headers = rows[5]
assert suggest_column(headers, patterns=("среднесут", "шт./день"), preferred_tokens=("шт", "штук"), excluded_tokens=("руб",)) == headers[16]
assert suggest_column(
    headers,
    saved_choice=headers[9],
    patterns=("среднесут", "шт./день"),
    preferred_tokens=("шт", "штук"),
    excluded_tokens=("руб",),
) == headers[16]
assert suggest_column(
    headers,
    saved_choice=headers[16],
    patterns=("среднесут",),
    preferred_tokens=("шт",),
    excluded_tokens=("руб",),
) == headers[16]
assert find_method1_header_row([("SKU", "Продажи, руб.")]) is None

# The availability export contains tempting but incorrect neighbors: SKU, a
# recommendation column, and stock-cover duration. Prefer the human product
# name and FBO stock measured in pieces.
assert suggest_method1_column(headers, "product") == "Название товара"
assert suggest_method1_column(headers, "cluster") == "Кластер"
assert suggest_method1_column(headers, "daily_sales") == headers[16]
assert suggest_method1_column(headers, "fbo_stock") == "Остаток FBO, шт"
assert suggest_method1_column(headers, "product", "SKU") == "Название товара"
assert suggest_method1_column(headers, "fbo_stock", "До конца остатка FBO, дн") == "Остаток FBO, шт"

print("Method 1 Ozon report header and units selection: PASS")
