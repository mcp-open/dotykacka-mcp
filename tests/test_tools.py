"""Unit testy pro `dotykacka` nástroje — přímo, bez běžícího MCP transportu.

Konektor čte identitu i OAuth přístup z `openmcp_sdk.current_context().oauth`
(SDK 0.3 kontrakt, staví se paralelně). Testy proto:

* monkeypatchují `server.current_context` na lehký fake kontext s `.oauth`,
  `.config` a `.principal` (stejný nápad jako `raynet` testy s `RaynetClient`);
* monkeypatchují `server.DotykackaClient` na stub bez sítě.

Díky `importorskip` se celý modul přeskočí, dokud není `fastmcp` k dispozici —
neblokuje to ostatní (strukturální) testy.
"""

from __future__ import annotations

import types
from contextlib import contextmanager

import pytest

pytest.importorskip("fastmcp")

import httpx  # noqa: E402

from openmcp_sdk.envelope import ConnectorError, ErrorCode  # noqa: E402

from connector import server  # noqa: E402
from connector.client import BASE_URL, DotykackaClient, DotykackaError  # noqa: E402


# --------------------------------------------------------------------------- #
# Stuby
# --------------------------------------------------------------------------- #
class _FakeOAuth:
    def __init__(self, cloud_id: str = "355745136") -> None:
        self.cloud_id = cloud_id
        self.token_calls = 0
        self.invalidated = 0

    def access_token(self) -> str:
        self.token_calls += 1
        return "fake-access-token"

    def invalidate(self) -> None:
        self.invalidated += 1


class _FakeClient:
    """Stub DotykackaClient — nahrazuje síť, sleduje volání."""

    def __init__(self, responses=None, cloud=None, error: Exception | None = None) -> None:
        self.responses = responses or {}
        self.cloud = cloud if cloud is not None else {"id": "355745136", "name": "Provozovna"}
        self.error = error
        self.calls: list[tuple[str, dict | None]] = []

    def cloud_get(self, resource, params=None):
        self.calls.append((resource, params))
        if self.error is not None:
            raise self.error
        return self.responses.get(resource.split("/")[0], {"data": []})

    def get_cloud(self):
        if self.error is not None:
            raise self.error
        return self.cloud

    def close(self):
        pass


@contextmanager
def _ctx(monkeypatch, *, config=None, sub="u1", oauth=None, client=None):
    oauth = oauth or _FakeOAuth()
    fake_ctx = types.SimpleNamespace(
        oauth=oauth,
        config=config or {},
        principal=types.SimpleNamespace(sub=sub, email=None),
    )
    monkeypatch.setattr(server, "current_context", lambda: fake_ctx)
    if client is not None:
        monkeypatch.setattr(server, "DotykackaClient", lambda o: client)
    yield fake_ctx


# --------------------------------------------------------------------------- #
# Registrace a anotace
# --------------------------------------------------------------------------- #
EXPECTED_TOOLS = {
    "get_cloud_info", "list_orders", "get_order", "list_products",
    "list_categories", "list_warehouses", "list_customers", "list_employees",
    "sales_summary",
}


def test_all_tools_registered_and_read_only():
    import asyncio

    tools = asyncio.run(server.mcp.get_tools())
    assert set(tools) == EXPECTED_TOOLS
    for name, tool in tools.items():
        assert tool.annotations.readOnlyHint is True, name


def test_no_write_or_delete_tools():
    import asyncio

    tools = asyncio.run(server.mcp.get_tools())
    assert not [n for n in tools if "delete" in n.lower() or "remove" in n.lower()]
    # supports_write=false → žádný nástroj se zápisovou anotací.
    assert all(t.annotations.readOnlyHint is True for t in tools.values())


# --------------------------------------------------------------------------- #
# Envelope + provenance
# --------------------------------------------------------------------------- #
def test_list_orders_returns_envelope(monkeypatch):
    fake = _FakeClient({"orders": {"data": [{"id": 1, "totalValueRounded": 100}]}})
    with _ctx(monkeypatch, client=fake):
        result = server.list_orders()
    assert result.data == {"data": [{"id": 1, "totalValueRounded": 100}]}
    assert result.provenance.source_id == "dotykacka"
    assert result.provenance.source_url.endswith("/clouds/355745136/orders")
    assert result.warnings == []


def test_get_cloud_info_returns_envelope(monkeypatch):
    fake = _FakeClient(cloud={"id": "355745136", "name": "Kavárna"})
    with _ctx(monkeypatch, client=fake):
        result = server.get_cloud_info()
    assert result.data["name"] == "Kavárna"
    assert result.provenance.source_url.endswith("/clouds/355745136")


def test_list_orders_builds_date_filter(monkeypatch):
    fake = _FakeClient({"orders": {"data": []}})
    with _ctx(monkeypatch, client=fake):
        server.list_orders(date_from="2026-01-01", date_to="2026-02-01")
    _, params = fake.calls[0]
    assert "created|gteq|2026-01-01T00:00:00.000Z" in params["filter"]
    assert "created|lt|2026-02-01T00:00:00.000Z" in params["filter"]


def test_invalid_date_raises_invalid_input(monkeypatch):
    fake = _FakeClient({"orders": {"data": []}})
    with _ctx(monkeypatch, client=fake):
        with pytest.raises(ConnectorError) as exc:
            server.list_orders(date_from="loni")
    assert exc.value.code == ErrorCode.INVALID_INPUT


def test_limit_is_clamped(monkeypatch):
    fake = _FakeClient({"products": {"data": []}})
    with _ctx(monkeypatch, client=fake):
        server.list_products(limit=99999)
    _, params = fake.calls[0]
    assert params["limit"] == 100


# --------------------------------------------------------------------------- #
# GDPR pseudonymizace
# --------------------------------------------------------------------------- #
def test_customers_pseudonymized_by_default(monkeypatch):
    fake = _FakeClient(
        {"customers": {"data": [{"id": 7, "displayName": "Jan Novák", "email": "jan@example.cz"}]}}
    )
    with _ctx(monkeypatch, client=fake):
        result = server.list_customers()
    row = result.data["data"][0]
    assert row["email"].startswith("<EMAIL_")
    assert row["displayName"].startswith("<NAME_")
    assert row["id"] == 7  # ne-PII pole projde


def test_anonymize_false_passes_through(monkeypatch):
    fake = _FakeClient({"customers": {"data": [{"email": "jan@example.cz"}]}})
    with _ctx(monkeypatch, config={"anonymize_data": False}, client=fake):
        result = server.list_customers()
    assert result.data["data"][0]["email"] == "jan@example.cz"


def test_product_names_not_tokenized(monkeypatch):
    """`name` u produktu je název zboží, ne osobní údaj — nesmí se tokenizovat."""
    fake = _FakeClient({"products": {"data": [{"id": 1, "name": "Espresso"}]}})
    with _ctx(monkeypatch, client=fake):
        result = server.list_products()
    assert result.data["data"][0]["name"] == "Espresso"


# --------------------------------------------------------------------------- #
# Agregace
# --------------------------------------------------------------------------- #
def test_sales_summary_aggregates(monkeypatch):
    orders = {
        "data": [
            {
                "documentType": "RECEIPT",
                "totalValueRounded": 100,
                "currency": "CZK",
                "orderItems": [
                    {"name": "Espresso", "quantity": 2, "totalPriceWithVat": 80, "vat": 21},
                    {"name": "Voda", "quantity": 1, "totalPriceWithVat": 20, "vat": 12},
                ],
            },
            {"documentType": "RECEIPT", "totalValueRounded": 50, "orderItems": []},
            {"documentType": "RECEIPT", "totalValueRounded": 999, "canceledDate": "2026-01-05"},
        ]
    }
    fake = _FakeClient({"orders": orders})
    with _ctx(monkeypatch, client=fake):
        result = server.sales_summary(date_from="2026-01-01", date_to="2026-02-01")
    d = result.data
    assert d["document_count"] == 3
    assert d["valid_count"] == 2
    assert d["canceled_count"] == 1
    assert d["revenue"] == 150.0  # storno se do tržby nepočítá
    assert d["average_receipt"] == 75.0
    assert d["top_products"][0]["name"] == "Espresso"
    assert d["vat"]["21"] == 80.0
    assert d["truncated"] is False


def test_sales_summary_defaults_to_last_30_days(monkeypatch):
    fake = _FakeClient({"orders": {"data": []}})
    with _ctx(monkeypatch, client=fake):
        result = server.sales_summary()
    assert result.data["period"]["from"] and result.data["period"]["to"]


# --------------------------------------------------------------------------- #
# Mapování chyb
# --------------------------------------------------------------------------- #
def test_upstream_error_maps_to_connector_error(monkeypatch):
    fake = _FakeClient(error=DotykackaError("HTTP 500", status_code=500))
    with _ctx(monkeypatch, client=fake):
        with pytest.raises(ConnectorError) as exc:
            server.list_orders()
    assert exc.value.code == ErrorCode.UPSTREAM_ERROR


# --------------------------------------------------------------------------- #
# test_connection (supports_test: true)
# --------------------------------------------------------------------------- #
def test_test_connection_success(monkeypatch):
    fake = _FakeClient(cloud={"id": "355745136", "name": "Kavárna"})
    with _ctx(monkeypatch, client=fake):
        message = server.test_connection()
    assert "355745136" in message
    assert "Připojeno" in message


def test_test_connection_401_maps_to_invalid_input(monkeypatch):
    fake = _FakeClient(error=DotykackaError("401", status_code=401))
    with _ctx(monkeypatch, client=fake):
        with pytest.raises(ConnectorError) as exc:
            server.test_connection()
    assert exc.value.code == ErrorCode.INVALID_INPUT


def test_test_connection_5xx_maps_to_upstream_unavailable(monkeypatch):
    fake = _FakeClient(error=DotykackaError("503", status_code=503))
    with _ctx(monkeypatch, client=fake):
        with pytest.raises(ConnectorError) as exc:
            server.test_connection()
    assert exc.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert "503" not in exc.value.message


# --------------------------------------------------------------------------- #
# Klient — obnova tokenu při 401 (přes injektovaný httpx transport)
# --------------------------------------------------------------------------- #
def test_client_refreshes_token_once_on_401():
    """Upstream 401 → oauth.invalidate() + jeden retry s čerstvým tokenem."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(200, json={"data": [{"id": 1}]})

    oauth = _FakeOAuth()
    client = DotykackaClient(oauth, transport=httpx.MockTransport(handler))
    try:
        result = client.cloud_get("orders")
    finally:
        client.close()

    assert result == {"data": [{"id": 1}]}
    assert oauth.invalidated == 1
    assert calls["n"] == 2
    assert oauth.token_calls == 2  # token se bere čerstvý pro každý pokus


def test_client_sends_bearer_and_cloud_path():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"ok": True})

    oauth = _FakeOAuth(cloud_id="42")
    client = DotykackaClient(oauth, transport=httpx.MockTransport(handler))
    try:
        client.cloud_get("products", {"limit": 10})
    finally:
        client.close()

    assert seen["auth"] == "Bearer fake-access-token"
    assert seen["url"].startswith(f"{BASE_URL}/clouds/42/products")
    assert "limit=10" in seen["url"]


def test_client_persistent_401_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "revoked"})

    client = DotykackaClient(_FakeOAuth(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(DotykackaError) as exc:
            client.cloud_get("orders")
    finally:
        client.close()
    assert exc.value.status_code == 401
