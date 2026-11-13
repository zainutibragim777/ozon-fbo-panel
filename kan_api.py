"""Read-only access to KAN's documented v2 API."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


PRODUCTS_URL = "https://kultura-analitiki.ru/api/v2/products/"
ANALYTICS_URL = "https://kultura-analitiki.ru/api/v2/analytics/"
_ALLOWED_HOST = "kultura-analitiki.ru"
_TIMEOUT_SECONDS = 30


class KanApiError(RuntimeError):
    """A sanitized error returned while communicating with KAN."""


def _get_json(url, token, params=None):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != _ALLOWED_HOST:
        raise KanApiError("Разрешён запрос только к официальному API КАН.")
    secret = str(token or "").strip()
    if not secret:
        raise KanApiError("Токен API КАН не настроен в Streamlit Secrets.")
    request_url = url + ("?" + urlencode(params) if params else "")
    request = Request(
        request_url,
        headers={"Authorization": "Token " + secret, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code in (401, 403):
            raise KanApiError("КАН отклонил доступ. Проверь токен и права подписки.") from error
        if error.code == 404:
            raise KanApiError("Адрес API КАН не найден.") from error
        raise KanApiError(f"API КАН вернул ошибку HTTP {error.code}.") from error
    except (TimeoutError, URLError) as error:
        raise KanApiError("Не удалось связаться с API КАН. Проверь интернет и повтори попытку.") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise KanApiError("API КАН вернул ответ, который не удалось прочитать как JSON.") from error


def fetch_products(token):
    """Fetch product records for connection checks and shop discovery."""
    return _get_json(PRODUCTS_URL, token)


def fetch_analytics(token, shop_id, date_from, date_to):
    """Fetch read-only analytics for one KAN shop and an inclusive date range."""
    if not str(shop_id).strip():
        raise KanApiError("Укажи ID магазина КАН.")
    if date_from > date_to:
        raise KanApiError("Начало периода должно быть раньше окончания.")
    payload = _get_json(
        ANALYTICS_URL,
        token,
        {
            "shop_id": str(shop_id).strip(),
            "date__gte": date_from.isoformat(),
            "date__lte": date_to.isoformat(),
        },
    )
    if isinstance(payload, dict) and payload.get("next"):
        raise KanApiError(
            "КАН вернул аналитику несколькими страницами. "
            "Не использую неполную выгрузку для расчёта."
        )
    return payload


def _find_rows(payload):
    if isinstance(payload, list):
        return payload if not payload or all(isinstance(row, dict) for row in payload) else None
    if not isinstance(payload, dict):
        return None
    for key in ("results", "data", "items", "records", "rows"):
        candidate = payload.get(key)
        if isinstance(candidate, list) and (not candidate or all(isinstance(row, dict) for row in candidate)):
            return candidate
    for value in payload.values():
        rows = _find_rows(value)
        if rows is not None:
            return rows
    return None


def records_to_frame(payload):
    """Convert common JSON API list/pagination shapes to a tabular preview."""
    import pandas as pd

    rows = _find_rows(payload)
    if rows is None and isinstance(payload, dict):
        rows = [payload] if payload else []
    if rows is None:
        raise KanApiError("Формат ответа API КАН не похож на таблицу записей.")
    if rows and not all(isinstance(row, dict) for row in rows):
        raise KanApiError("В ответе API КАН есть строки в неожиданном формате.")
    return pd.json_normalize(rows) if rows else pd.DataFrame()
