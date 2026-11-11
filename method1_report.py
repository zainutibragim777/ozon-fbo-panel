"""Read Ozon availability reports for Method 1."""
from io import BytesIO


def _normalise_header(value):
    return " ".join(str(value or "").strip().casefold().replace("ё", "е").split())


def find_method1_header_row(rows):
    """Find a row with product, cluster, unit-sales and FBO-stock headings."""
    for index, row in enumerate(rows):
        labels = [_normalise_header(value) for value in row if value is not None]
        has_product = any("sku" in label or "артикул" in label or "товар" in label for label in labels)
        has_cluster = any("кластер" in label for label in labels)
        has_unit_sales = any(
            "продаж" in label
            and ("шт" in label or "штук" in label)
            and "руб" not in label
            for label in labels
        )
        has_fbo_stock = any("остат" in label and "fbo" in label for label in labels)
        if has_product and has_cluster and has_unit_sales and has_fbo_stock:
            return index
    return None


def suggest_column(columns, saved_choice=None, patterns=(), preferred_tokens=(), excluded_tokens=()):
    """Prefer a still-valid saved choice, then a unit-specific match."""
    allowed = [
        column for column in columns
        if not any(token in _normalise_header(column) for token in excluded_tokens)
    ]
    if saved_choice is not None:
        saved = next((column for column in allowed if str(column) == str(saved_choice)), None)
        if saved is not None:
            return saved
    if preferred_tokens:
        preferred = next(
            (column for column in allowed
             if any(token in _normalise_header(column) for token in preferred_tokens)
             and any(pattern in _normalise_header(column) for pattern in patterns)),
            None,
        )
        if preferred is not None:
            return preferred
    matched = next(
        (column for column in allowed
         if any(pattern in _normalise_header(column) for pattern in patterns)),
        None,
    )
    return matched if matched is not None else (allowed[0] if allowed else columns[0])


def suggest_method1_column(columns, role, saved_choice=None):
    """Choose the correct Ozon report column for a Method 1 input."""
    rules = {
        "product": {
            "patterns": ("название товара", "артикул", "sku", "товар"),
        },
        "cluster": {
            "patterns": ("кластер", "регион", "город"),
            "preferred_tokens": ("кластер",),
        },
        "daily_sales": {
            "patterns": ("среднесут", "шт./день", "штук в день"),
            "preferred_tokens": ("шт", "штук"),
            "excluded_tokens": ("руб",),
        },
        "fbo_stock": {
            "patterns": ("остаток fbo", "fbo"),
            "preferred_tokens": ("остаток fbo, шт", "остатки fbo, шт"),
            "excluded_tokens": ("до конца", "дн", "дней", "fbs", "в пути"),
        },
    }
    if role not in rules:
        raise ValueError(f"Unknown Method 1 column role: {role}")
    rule = rules[role]
    if role == "product":
        product_name = next(
            (column for column in columns if "название товара" in _normalise_header(column)),
            None,
        )
        if product_name is not None:
            return product_name
    return suggest_column(columns, saved_choice, **rule)


def read_method1_report(content, filename):
    """Read CSV/XLSX with Ozon's metadata rows above the actual table header."""
    import pandas as pd

    data = bytes(content)
    if str(filename).lower().endswith(".xlsx"):
        preview = pd.read_excel(BytesIO(data), header=None, nrows=20)
        header = find_method1_header_row(preview.itertuples(index=False, name=None))
        if header is None:
            raise ValueError(
                "Не найдена строка заголовков. Нужны столбцы SKU/артикул, "
                "кластер, продажи в штуках и остаток FBO."
            )
        return pd.read_excel(BytesIO(data), header=header)

    preview = pd.read_csv(BytesIO(data), header=None, nrows=20, sep=None, engine="python")
    header = find_method1_header_row(preview.itertuples(index=False, name=None))
    if header is None:
        raise ValueError(
            "Не найдена строка заголовков. Нужны столбцы SKU/артикул, "
            "кластер, продажи в штуках и остаток FBO."
        )
    return pd.read_csv(BytesIO(data), header=header, sep=None, engine="python")
