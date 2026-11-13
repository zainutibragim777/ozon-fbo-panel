"""Offline checks for the read-only KAN API client."""
from datetime import date
from unittest.mock import patch

import kan_api


class FakeResponse:
    body = b'{"results": [{"sku": 1}]}'

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def raises(expected, callback):
    try:
        callback()
    except expected:
        return
    raise AssertionError(f"Expected {expected.__name__}")


with patch("kan_api.urlopen", return_value=FakeResponse()) as open_url:
    payload = kan_api.fetch_analytics(
        "secret-token", "3064", date(2026, 10, 16), date(2026, 11, 12),
    )
request = open_url.call_args.args[0]
assert request.full_url.startswith(kan_api.ANALYTICS_URL + "?")
assert request.get_header("Authorization") == "Token secret-token"
assert "shop_id=3064" in request.full_url
assert "date__gte=2026-10-16" in request.full_url
assert "date__lte=2026-11-12" in request.full_url
assert payload == {"results": [{"sku": 1}]}

with patch("kan_api.urlopen", return_value=FakeResponse()):
    FakeResponse.body = b'{"next": "https://example.test/page-2", "results": [{"sku": 1}]}'
    raises(
        kan_api.KanApiError,
        lambda: kan_api.fetch_analytics(
            "secret-token", "3064", date(2026, 10, 16), date(2026, 11, 12),
        ),
    )
    FakeResponse.body = b'{"results": [{"sku": 1}]}'

raises(
    kan_api.KanApiError,
    lambda: kan_api.fetch_analytics("token", "", date(2026, 11, 2), date(2026, 11, 1)),
)
raises(
    kan_api.KanApiError,
    lambda: kan_api.fetch_analytics("token", "3064", date(2026, 11, 2), date(2026, 11, 1)),
)

assert kan_api._find_rows([{"sku": 1}]) == [{"sku": 1}]
assert kan_api._find_rows({"count": 1, "results": [{"article": "A"}]}) == [{"article": "A"}]
assert kan_api._find_rows({"payload": {"rows": [{"cluster": "Казань"}]}}) == [
    {"cluster": "Казань"}
]
assert kan_api._find_rows("bad response") is None

print("KAN API read-only request and response shape: PASS")
