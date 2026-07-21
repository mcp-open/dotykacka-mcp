"""Unit testy pro `dotykacka` nástroje — přímo, bez běžícího MCP transportu.

Konektor čte identitu i OAuth přístup z `openmcp_sdk.current_context().oauth`.
Testy proto:

* monkeypatchují `server.current_context` na lehký fake kontext s `.oauth`,
  `.config` a `.principal`;
* monkeypatchují `server.UpstreamClient` na stub bez sítě — retry, timeout,
  401-refresh a bearer hlavičku už otestoval `openmcp-sdk` (`tests/test_http.py`)
  obecně; tady se testuje jen doménová logika (cloud-scoped cesta, PII, agregace).

Díky `importorskip` se celý modul přeskočí, dokud není `fastmcp` k dispozici —
neblokuje to ostatní (strukturální) testy.
"""

from __future__ import annotations

import types
from contextlib import contextmanager

import pytest

pytest.importorskip("fastmcp")

from openmcp_sdk.envelope import ConnectorError, ErrorCode  # noqa: E402

from connector import server  # noqa: E402


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
    """Stub `UpstreamClient` — nahrazuje síť, sleduje volání na úrovni cesty."""

    def __init__(self, responses=None, cloud=None, error: Exception | None = None) -> None:
        self.responses = responses or {}
        self.cloud = cloud if cloud is not None else {"id": "355745136", "name": "Provozovna"}
        self.error = error
        self.calls: list[tuple[str, dict | None]] = []

    @staticmethod
    def seg(value: object) -> str:
        return str(value)

    def get_json(self, path: str, params=None):
        self.calls.append((path, params))
        if self.error is not None:
            raise self.error
        # path je "/clouds/{cloud_id}" (get_cloud) nebo "/clouds/{cloud_id}/{resource}".
        resource = path.split("/", 3)[3] if path.count("/") >= 3 else ""
        if not resource:
            return self.cloud
        return self.responses.get(resource.split("/")[0], {"data": []})

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
        monkeypatch.setattr(server, "UpstreamClient", lambda **kw: client)
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
# Envelope + provenance + cloud-scoped cesta
# --------------------------------------------------------------------------- #
def test_list_orders_returns_envelope(monkeypatch):
    fake = _FakeClient({"orders": {"data": [{"id": 1, "totalValueRounded": 100}]}})
    with _ctx(monkeypatch, client=fake):
        result = server.list_orders()
    assert result.data == {"data": [{"id": 1, "totalValueRounded": 100}]}
    assert result.provenance.source_id == "dotykacka"
    assert result.provenance.source_url.endswith("/clouds/355745136/orders")
    assert result.warnings == []
    # Cloud-scoped cesta se skládá `/clouds/{cloud_id}/{resource}` — jediné,
    # co je u dotykačky nad rámec obecného SDK klienta.
    assert fake.calls[0][0] == "/clouds/355745136/orders"


def test_get_cloud_info_returns_envelope(monkeypatch):
    fake = _FakeClient(cloud={"id": "355745136", "name": "Kavárna"})
    with _ctx(monkeypatch, client=fake):
        result = server.get_cloud_info()
    assert result.data["name"] == "Kavárna"
    assert result.provenance.source_url.endswith("/clouds/355745136")
    assert fake.calls[0][0] == "/clouds/355745136"


def test_list_orders_builds_date_filter(monkeypatch):
    fake = _FakeClient({"orders": {"data": []}})
    with _ctx(monkeypatch, client=fake):
        server.list_orders(date_from="2026-01-01", date_to="2026-02-01")
    _, params = fake.calls[0]
    assert "created|gteq|2025-12-31T23:00:00.000Z" in params["filter"]
    assert "created|lt|2026-01-31T23:00:00.000Z" in params["filter"]


def test_invalid_date_raises_invalid_input(monkeypatch):
    fake = _FakeClient({"orders": {"data": []}})
    with _ctx(monkeypatch, client=fake):
        with pytest.raises(ConnectorError) as exc:
            server.list_orders(date_from="loni")
    assert exc.value.code == ErrorCode.INVALID_INPUT


def test_date_rejects_timestamp_instead_of_silently_truncating(monkeypatch):
    fake = _FakeClient({"orders": {"data": []}})
    with _ctx(monkeypatch, client=fake):
        with pytest.raises(ConnectorError) as exc:
            server.list_orders(date_from="2026-01-01T12:00:00")
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


def test_employee_generic_name_is_pseudonymized(monkeypatch):
    fake = _FakeClient({"employees": {"data": [{"id": 3, "name": "Jan Novák"}]}})
    with _ctx(monkeypatch, client=fake):
        result = server.list_employees()
    assert result.data["data"][0]["name"].startswith("<NAME_")


def test_same_user_different_cloud_has_different_pseudonym(monkeypatch):
    fake = _FakeClient({"customers": {"data": [{"name": "Jan Novák"}]}})
    with _ctx(monkeypatch, oauth=_FakeOAuth("cloud-a"), client=fake):
        first = server.list_customers().data["data"][0]["name"]
    with _ctx(monkeypatch, oauth=_FakeOAuth("cloud-b"), client=fake):
        second = server.list_customers().data["data"][0]["name"]
    assert first != second


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
            {
                "documentType": "RECEIPT",
                "totalValueRounded": 999,
                "canceledDate": "2026-01-05",
                "orderItems": [
                    {"name": "Stornovaný produkt", "quantity": 1, "totalPriceWithVat": 999, "vat": 21}
                ],
            },
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
    assert all(p["name"] != "Stornovaný produkt" for p in d["top_products"])
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
    fake = _FakeClient(error=ConnectorError(ErrorCode.UPSTREAM_ERROR, "upstream selhal se stavem 500", status=500))
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
    fake = _FakeClient(error=ConnectorError(ErrorCode.FORBIDDEN, "upstream odmítl přístupové údaje", status=401))
    with _ctx(monkeypatch, client=fake):
        with pytest.raises(ConnectorError) as exc:
            server.test_connection()
    assert exc.value.code == ErrorCode.INVALID_INPUT


def test_test_connection_5xx_maps_to_upstream_unavailable(monkeypatch):
    fake = _FakeClient(error=ConnectorError(ErrorCode.UPSTREAM_ERROR, "upstream selhal se stavem 503", status=503))
    with _ctx(monkeypatch, client=fake):
        with pytest.raises(ConnectorError) as exc:
            server.test_connection()
    assert exc.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert "503" not in exc.value.message
