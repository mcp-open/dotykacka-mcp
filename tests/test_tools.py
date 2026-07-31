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

import httpx
import pytest

pytest.importorskip("fastmcp")

from openmcp_sdk.envelope import ConnectorError, ErrorCode  # noqa: E402
from openmcp_sdk.http import UpstreamClient as RealUpstreamClient  # noqa: E402

from connector import server  # noqa: E402


# --------------------------------------------------------------------------- #
# Stuby
# --------------------------------------------------------------------------- #
class _FakeOAuth:
    def __init__(self, cloud_id: object = "355745136") -> None:
        self.cloud_id = cloud_id
        self.token_calls = 0
        self.invalidated = 0

    def access_token(self) -> str:
        self.token_calls += 1
        return "fake-access-token"

    def invalidate(self) -> None:
        self.invalidated += 1


class _RotatingOAuth(_FakeOAuth):
    def access_token(self) -> str:
        self.token_calls += 1
        return "expired-token" if self.invalidated == 0 else "fresh-token"


class _FakeClient:
    """Stub `UpstreamClient` — nahrazuje síť, sleduje volání na úrovni cesty."""

    def __init__(self, responses=None, cloud=None, error: Exception | None = None) -> None:
        self.responses = responses or {}
        self.cloud = cloud if cloud is not None else {"id": "355745136", "name": "Provozovna"}
        self.error = error
        self.calls: list[tuple[str, dict | None]] = []
        self.init_kwargs: dict = {}

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


class _PagingClient(_FakeClient):
    """Vrací stránky ze seznamu a záměrně neposkytuje totalItemsCount."""

    def __init__(self, records: list[dict]) -> None:
        super().__init__()
        self.records = records

    def get_json(self, path: str, params=None):
        self.calls.append((path, params))
        page = params["page"]
        limit = params["limit"]
        start = (page - 1) * limit
        return {"data": self.records[start : start + limit]}


class _DotykackaPagingClient(_PagingClient):
    """Dotykačka na prázdné stránce orders vrací 404, ne 200 + prázdná data."""

    def get_json(self, path: str, params=None):
        if params is None:
            return _FakeClient.get_json(self, path, params)
        page = params["page"]
        limit = params["limit"]
        start = (page - 1) * limit
        if start >= len(self.records):
            self.calls.append((path, params))
            raise ConnectorError(
                ErrorCode.INVALID_INPUT,
                "upstream nenašel požadovaný zdroj",
                status=404,
            )
        return super().get_json(path, params)


@contextmanager
def _ctx(monkeypatch, *, config=None, sub="u1", owner_id=None, oauth=None, client=None):
    oauth = oauth or _FakeOAuth()
    fake_ctx = types.SimpleNamespace(
        oauth=oauth,
        config=config or {},
        principal=types.SimpleNamespace(
            sub=sub,
            email=None,
            credential_owner_id=owner_id,
        ),
    )
    monkeypatch.setattr(server, "current_context", lambda: fake_ctx)
    if client is not None:
        def _client_factory(**kwargs):
            client.init_kwargs = kwargs
            return client

        monkeypatch.setattr(server, "UpstreamClient", _client_factory)
    yield fake_ctx


# --------------------------------------------------------------------------- #
# Registrace a anotace
# --------------------------------------------------------------------------- #
EXPECTED_TOOLS = {
    "get_cloud_info",
    "list_orders",
    "get_order",
    "list_products",
    "list_categories",
    "list_warehouses",
    "list_customers",
    "list_employees",
    "sales_summary",
}


def test_all_tools_registered_and_read_only():
    import asyncio

    tools = asyncio.run(server.mcp.list_tools())
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.annotations.readOnlyHint is True, tool.name


def test_no_write_or_delete_tools():
    import asyncio

    tools = asyncio.run(server.mcp.list_tools())
    assert not [
        tool.name
        for tool in tools
        if "delete" in tool.name.lower() or "remove" in tool.name.lower()
    ]
    # supports_write=false → žádný nástroj se zápisovou anotací.
    assert all(tool.annotations.readOnlyHint is True for tool in tools)


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
    assert fake.calls[0][1]["sort"] == "-created"


def test_order_tools_request_only_items_not_payment_logs(monkeypatch):
    fake = _FakeClient(
        {
            "orders": {"data": []},
            "orders/42": {"id": 42, "orderItems": []},
        }
    )
    with _ctx(monkeypatch, client=fake):
        server.list_orders(include_items=True)
        server.get_order(42)
    assert fake.calls[0][1]["include"] == "orderItems"
    assert fake.calls[1][1]["include"] == "orderItems"
    assert all("moneyLogs" not in params["include"] for _, params in fake.calls)


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
    with _ctx(monkeypatch, client=fake), pytest.raises(ConnectorError) as exc:
        server.list_orders(date_from="loni")
    assert exc.value.code == ErrorCode.INVALID_INPUT


def test_date_rejects_timestamp_instead_of_silently_truncating(monkeypatch):
    fake = _FakeClient({"orders": {"data": []}})
    with _ctx(monkeypatch, client=fake), pytest.raises(ConnectorError) as exc:
        server.list_orders(date_from="2026-01-01T12:00:00")
    assert exc.value.code == ErrorCode.INVALID_INPUT


@pytest.mark.parametrize(
    ("date_from", "date_to"),
    [("2026-02-01", "2026-02-01"), ("2026-02-02", "2026-02-01")],
)
def test_date_range_must_be_strictly_increasing(monkeypatch, date_from, date_to):
    fake = _FakeClient({"orders": {"data": []}})
    with _ctx(monkeypatch, client=fake), pytest.raises(ConnectorError) as exc:
        server.list_orders(date_from=date_from, date_to=date_to)
    assert exc.value.code == ErrorCode.INVALID_INPUT
    assert fake.calls == []


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


def test_legacy_anonymize_false_cannot_disable_pii_boundary(monkeypatch):
    fake = _FakeClient({"customers": {"data": [{"email": "jan@example.cz"}]}})
    with _ctx(monkeypatch, config={"anonymize_data": False}, client=fake):
        result = server.list_customers()
    assert result.data["data"][0]["email"].startswith("<EMAIL_")


def test_product_names_not_tokenized(monkeypatch):
    """`name` u produktu je název zboží, ne osobní údaj — nesmí se tokenizovat."""
    fake = _FakeClient({"products": {"data": [{"id": 1, "name": "Espresso"}]}})
    with _ctx(monkeypatch, client=fake):
        result = server.list_products()
    assert result.data["data"][0]["name"] == "Espresso"


def test_product_barcode_remains_catalog_data(monkeypatch):
    fake = _FakeClient({"products": {"data": [{"id": 1, "barcode": "8591234567890"}]}})
    with _ctx(monkeypatch, client=fake):
        result = server.list_products()
    assert result.data["data"][0]["barcode"] == "8591234567890"


def test_customer_schema_specific_pii_is_protected(monkeypatch):
    fake = _FakeClient(
        {
            "customers": {
                "data": [
                    {
                        "addressLine1": "Dlouhá 5",
                        "addressLine2": "byt 7",
                        "companyName": "Jan Novák servis",
                        "companyId2": "SK2020123456",
                        "barcode": "LOYALTY-123",
                        "modifiedBy": 987,
                        "note": "Bydlí na Dlouhé 5 v Ostravě.",
                        "internalNote": "Manželka Eva Nováková vyzvedává objednávky.",
                        "headerPrint": "Jan Novák, Dlouhá 5, Ostrava",
                    }
                ]
            }
        }
    )
    with _ctx(monkeypatch, client=fake):
        row = server.list_customers().data["data"][0]
    assert row["addressLine1"].startswith("<ADDR_")
    assert row["addressLine2"].startswith("<ADDR_")
    assert row["companyName"].startswith("<NAME_")
    assert row["companyId2"].startswith("<TAXNUM_")
    assert row["barcode"].startswith("<ID_")
    assert row["modifiedBy"].startswith("<ID_")
    assert row["note"].startswith("<TEXT_")
    assert row["internalNote"].startswith("<TEXT_")
    assert row["headerPrint"].startswith("<TEXT_")
    assert "Novák" not in str(row)
    assert "Dlouhá" not in str(row)


def test_employee_generic_name_is_pseudonymized(monkeypatch):
    fake = _FakeClient({"employees": {"data": [{"id": 3, "name": "Jan Novák"}]}})
    with _ctx(monkeypatch, client=fake):
        result = server.list_employees()
    assert result.data["data"][0]["name"].startswith("<NAME_")


def test_same_user_different_cloud_has_different_pseudonym(monkeypatch):
    fake = _FakeClient({"customers": {"data": [{"name": "Jan Novák"}]}})
    with _ctx(monkeypatch, oauth=_FakeOAuth("1001"), client=fake):
        first = server.list_customers().data["data"][0]["name"]
    with _ctx(monkeypatch, oauth=_FakeOAuth("1002"), client=fake):
        second = server.list_customers().data["data"][0]["name"]
    assert first != second


def test_team_credential_owner_makes_tokens_stable_across_members(monkeypatch):
    fake = _FakeClient({"customers": {"data": [{"name": "Jan Novák"}]}})
    with _ctx(monkeypatch, sub="member-a", owner_id="team-1", client=fake):
        first = server.list_customers().data["data"][0]["name"]
    with _ctx(monkeypatch, sub="member-b", owner_id="team-1", client=fake):
        second = server.list_customers().data["data"][0]["name"]
    assert first == second


def test_different_credential_owners_are_pii_isolated(monkeypatch):
    fake = _FakeClient({"customers": {"data": [{"name": "Jan Novák"}]}})
    with _ctx(monkeypatch, sub="member", owner_id="team-1", client=fake):
        first = server.list_customers().data["data"][0]["name"]
    with _ctx(monkeypatch, sub="member", owner_id="team-2", client=fake):
        second = server.list_customers().data["data"][0]["name"]
    assert first != second


def test_user_owner_scope_preserves_legacy_token(monkeypatch):
    fake = _FakeClient({"customers": {"data": [{"name": "Jan Novák"}]}})
    with _ctx(monkeypatch, sub="user-1", owner_id=None, client=fake):
        legacy = server.list_customers().data["data"][0]["name"]
    with _ctx(monkeypatch, sub="user-1", owner_id="user-1", client=fake):
        hosted = server.list_customers().data["data"][0]["name"]
    assert hosted == legacy


@pytest.mark.parametrize(
    "cloud_id",
    [None, True, "", "0", "01", " 1", "1 ", "-1", "1/2", "2147483648", "9999999999"],
)
def test_invalid_cloud_id_fails_closed_before_http(monkeypatch, cloud_id):
    fake = _FakeClient()
    with _ctx(monkeypatch, oauth=_FakeOAuth(cloud_id), client=fake), pytest.raises(
        ConnectorError
    ) as exc:
        server.list_orders()
    assert exc.value.code == ErrorCode.CREDENTIAL_INVALID
    assert fake.calls == []


@pytest.mark.parametrize("cloud_id", [1, "1", "2147483647"])
def test_valid_cloud_id_uses_canonical_string(monkeypatch, cloud_id):
    fake = _FakeClient({"orders": {"data": []}})
    with _ctx(monkeypatch, oauth=_FakeOAuth(cloud_id), client=fake):
        server.list_orders()
    assert fake.calls[0][0] == f"/clouds/{cloud_id}/orders"


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
                    {
                        "name": "Stornovaný produkt",
                        "quantity": 1,
                        "totalPriceWithVat": 999,
                        "vat": 21,
                    }
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


def test_sales_summary_uses_only_required_include(monkeypatch):
    fake = _FakeClient({"orders": {"data": []}})
    with _ctx(monkeypatch, client=fake):
        server.sales_summary(date_from="2026-01-01", date_to="2026-02-01")
    assert fake.calls[0][1]["include"] == "orderItems"
    assert "moneyLogs" not in fake.calls[0][1]["include"]


@pytest.mark.parametrize(("count", "truncated"), [(500, False), (501, True)])
def test_sales_summary_truncation_probes_beyond_exact_cap(monkeypatch, count, truncated):
    records = [
        {"id": index, "totalValueRounded": 1, "orderItems": []}
        for index in range(count)
    ]
    fake = _DotykackaPagingClient(records)
    with _ctx(monkeypatch, client=fake):
        result = server.sales_summary(date_from="2026-01-01", date_to="2026-02-01")
    assert result.data["document_count"] == min(count, 500)
    assert result.data["truncated"] is truncated
    assert [params["page"] for _, params in fake.calls] == [1, 2, 3, 4, 5, 6]


def test_sales_summary_empty_404_confirms_cloud_then_returns_zero(monkeypatch):
    fake = _DotykackaPagingClient([])
    with _ctx(monkeypatch, client=fake):
        result = server.sales_summary(date_from="2026-01-01", date_to="2026-02-01")
    assert result.data["document_count"] == 0
    assert result.data["revenue"] == 0.0
    assert result.data["truncated"] is False
    assert [path for path, _ in fake.calls] == [
        "/clouds/355745136/orders",
        "/clouds/355745136",
    ]


def test_sales_summary_empty_404_preserves_missing_cloud(monkeypatch):
    fake = _DotykackaPagingClient([])
    original = fake.get_json

    def missing_cloud(path, params=None):
        if params is None:
            fake.calls.append((path, params))
            raise ConnectorError(
                ErrorCode.INVALID_INPUT,
                "upstream nenašel požadovaný zdroj",
                status=404,
            )
        return original(path, params)

    monkeypatch.setattr(fake, "get_json", missing_cloud)
    with _ctx(monkeypatch, client=fake), pytest.raises(ConnectorError) as exc:
        server.sales_summary(date_from="2026-01-01", date_to="2026-02-01")
    assert exc.value.code == ErrorCode.INSTANCE_UNKNOWN
    assert exc.value.status == 404
    assert [path for path, _ in fake.calls] == [
        "/clouds/355745136/orders",
        "/clouds/355745136",
    ]


def test_sales_summary_later_404_is_end_without_cloud_probe(monkeypatch):
    records = [
        {"id": index, "totalValueRounded": 1, "orderItems": []}
        for index in range(100)
    ]
    fake = _DotykackaPagingClient(records)
    with _ctx(monkeypatch, client=fake):
        result = server.sales_summary(date_from="2026-01-01", date_to="2026-02-01")
    assert result.data["document_count"] == 100
    assert result.data["truncated"] is False
    assert [params["page"] for _, params in fake.calls] == [1, 2]


def test_sales_summary_boundary_probe_propagates_non_404(monkeypatch):
    records = [
        {"id": index, "totalValueRounded": 1, "orderItems": []}
        for index in range(500)
    ]
    fake = _PagingClient(records)
    original = fake.get_json

    def fail_probe(path, params=None):
        if params["page"] == 6:
            fake.calls.append((path, params))
            raise ConnectorError(
                ErrorCode.RATE_LIMITED,
                "upstream odmítl request pro rate limit",
                status=429,
            )
        return original(path, params)

    monkeypatch.setattr(fake, "get_json", fail_probe)
    with _ctx(monkeypatch, client=fake), pytest.raises(ConnectorError) as exc:
        server.sales_summary(date_from="2026-01-01", date_to="2026-02-01")
    assert exc.value.code == ErrorCode.RATE_LIMITED
    assert exc.value.status == 429


def test_sales_summary_marks_truncated_if_upstream_oversized_page_crosses_cap(monkeypatch):
    records = [
        {"id": index, "totalValueRounded": 1, "orderItems": []}
        for index in range(501)
    ]
    fake = _FakeClient({"orders": {"data": records}})
    with _ctx(monkeypatch, client=fake):
        result = server.sales_summary(date_from="2026-01-01", date_to="2026-02-01")
    assert result.data["document_count"] == 500
    assert result.data["truncated"] is True
    assert len(fake.calls) == 1


def test_non_finite_numbers_do_not_escape_to_summary(monkeypatch):
    fake = _FakeClient(
        {
            "orders": {
                "data": [
                    {
                        "totalValueRounded": "NaN",
                        "orderItems": [
                            {
                                "name": "Vadná hodnota",
                                "quantity": "Infinity",
                                "totalPriceWithVat": "-Infinity",
                                "vat": 21,
                            }
                        ],
                    }
                ]
            }
        }
    )
    with _ctx(monkeypatch, client=fake):
        result = server.sales_summary(date_from="2026-01-01", date_to="2026-02-01")
    assert result.data["revenue"] == 0.0
    assert result.data["top_products"][0]["revenue"] == 0.0
    assert result.data["top_products"][0]["quantity"] == 0.0


def test_extreme_finite_numbers_do_not_crash_or_escape_as_infinity(monkeypatch):
    fake = _FakeClient(
        {
            "orders": {
                "data": [
                    {
                        "totalValueRounded": "1e10000",
                        "orderItems": [
                            {
                                "name": "Vadný exponent",
                                "quantity": "1e10000",
                                "totalPriceWithVat": "1e10000",
                                "vat": 21,
                            }
                        ],
                    }
                ]
            }
        }
    )
    with _ctx(monkeypatch, client=fake):
        result = server.sales_summary(date_from="2026-01-01", date_to="2026-02-01")
    assert result.data["revenue"] == 0.0
    assert result.data["average_receipt"] == 0.0
    assert result.data["max_receipt"] == 0.0
    assert result.data["top_products"][0]["revenue"] == 0.0
    assert result.data["top_products"][0]["quantity"] == 0.0


# --------------------------------------------------------------------------- #
# Mapování chyb
# --------------------------------------------------------------------------- #
def test_upstream_error_maps_to_connector_error(monkeypatch):
    fake = _FakeClient(
        error=ConnectorError(
            ErrorCode.UPSTREAM_ERROR,
            "upstream selhal se stavem 500",
            status=500,
        )
    )
    with _ctx(monkeypatch, client=fake), pytest.raises(ConnectorError) as exc:
        server.list_orders()
    assert exc.value.code == ErrorCode.UPSTREAM_ERROR


def test_read_tool_refreshes_403_once_with_a_new_bearer_token(monkeypatch):
    oauth = _RotatingOAuth()
    authorizations: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        authorizations.append(request.headers["Authorization"])
        if len(authorizations) == 1:
            return httpx.Response(
                403,
                json={"reason": "ACCESS_TOKEN_EXPIRED"},
                request=request,
            )
        return httpx.Response(200, json={"data": []}, request=request)

    def client_factory(**kwargs):
        return RealUpstreamClient(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(server, "UpstreamClient", client_factory)
    with _ctx(monkeypatch, oauth=oauth):
        result = server.list_orders()
    assert result.data == {"data": []}
    assert oauth.invalidated == 1
    assert oauth.token_calls == 2
    assert authorizations == ["Bearer expired-token", "Bearer fresh-token"]


def test_read_tool_persistent_permission_403_stops_after_two_calls(monkeypatch):
    oauth = _RotatingOAuth()
    authorizations: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        authorizations.append(request.headers["Authorization"])
        return httpx.Response(
            403,
            json={"reason": "DOMAIN_FORBIDDEN"},
            request=request,
        )

    def client_factory(**kwargs):
        return RealUpstreamClient(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(server, "UpstreamClient", client_factory)
    with _ctx(monkeypatch, oauth=oauth), pytest.raises(ConnectorError) as exc:
        server.list_orders()
    assert exc.value.code == ErrorCode.FORBIDDEN
    assert exc.value.status == 403
    assert oauth.invalidated == 1
    assert oauth.token_calls == 2
    assert authorizations == ["Bearer expired-token", "Bearer fresh-token"]


# --------------------------------------------------------------------------- #
# test_connection (supports_test: true)
# --------------------------------------------------------------------------- #
def test_test_connection_success(monkeypatch):
    fake = _FakeClient(cloud={"id": "355745136", "name": "Kavárna"})
    with _ctx(monkeypatch, client=fake):
        message = server.test_connection()
    assert message == "Připojení k Dotykačce je funkční."
    assert "355745136" not in message
    assert fake.init_kwargs["retry"] is server.NO_RETRY
    assert fake.init_kwargs["timeout"] == server._PROBE_TIMEOUT_S
    assert fake.init_kwargs["connect_timeout"] == server._PROBE_CONNECT_TIMEOUT_S


def test_test_connection_401_maps_to_credential_invalid(monkeypatch):
    fake = _FakeClient(
        error=ConnectorError(
            ErrorCode.FORBIDDEN,
            "upstream odmítl přístupové údaje",
            status=401,
        )
    )
    with _ctx(monkeypatch, client=fake), pytest.raises(ConnectorError) as exc:
        server.test_connection()
    assert exc.value.code == ErrorCode.CREDENTIAL_INVALID


def test_test_connection_refreshes_once_after_expired_token_403(monkeypatch):
    oauth = _RotatingOAuth()
    authorizations: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        authorizations.append(request.headers["Authorization"])
        if len(authorizations) == 1:
            return httpx.Response(
                403,
                json={"reason": "ACCESS_TOKEN_EXPIRED"},
                request=request,
            )
        return httpx.Response(
            200,
            json={"id": "355745136", "name": "Kavárna"},
            request=request,
        )

    def client_factory(**kwargs):
        return RealUpstreamClient(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(server, "UpstreamClient", client_factory)
    with _ctx(monkeypatch, oauth=oauth):
        message = server.test_connection()
    assert message == "Připojení k Dotykačce je funkční."
    assert oauth.invalidated == 1
    assert oauth.token_calls == 2
    assert authorizations == ["Bearer expired-token", "Bearer fresh-token"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, ErrorCode.CREDENTIAL_INVALID),
        (403, ErrorCode.PROVIDER_PERMISSION_DENIED),
        (404, ErrorCode.INSTANCE_UNKNOWN),
        (410, ErrorCode.INSTANCE_UNKNOWN),
        (429, ErrorCode.RATE_LIMITED),
    ],
)
def test_test_connection_client_errors_are_actionable(monkeypatch, status, expected):
    """`/clouds/{cloud_id}` nese jen uložené `cloud_id` — 4xx = vadné propojení.

    404 znamená, že cloud pod tímto propojením neexistuje (přepojený nebo
    zrušený účet). Jako UPSTREAM_UNAVAILABLE to platforma četla jako dočasný
    výpadek a credentials nechala označené jako zdravé.
    """
    fake = _FakeClient(
        error=ConnectorError(
            ErrorCode.INVALID_INPUT,
            f"upstream odmítl {status}",
            status=status,
        )
    )
    oauth = _FakeOAuth()
    with _ctx(monkeypatch, oauth=oauth, client=fake), pytest.raises(ConnectorError) as exc:
        server.test_connection()
    assert exc.value.code == expected
    assert str(status) not in exc.value.message
    assert oauth.invalidated == (1 if status == 403 else 0)
    assert len(fake.calls) == (2 if status == 403 else 1)


def test_test_connection_5xx_maps_to_upstream_unavailable(monkeypatch):
    fake = _FakeClient(
        error=ConnectorError(
            ErrorCode.UPSTREAM_ERROR,
            "upstream selhal se stavem 503",
            status=503,
        )
    )
    oauth = _FakeOAuth()
    with _ctx(monkeypatch, oauth=oauth, client=fake), pytest.raises(ConnectorError) as exc:
        server.test_connection()
    assert exc.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert "503" not in exc.value.message
    assert oauth.invalidated == 0
    assert len(fake.calls) == 1


def test_test_connection_timeout_is_not_retried_or_masked(monkeypatch):
    oauth = _FakeOAuth()
    fake = _FakeClient(
        error=ConnectorError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            "upstream neodpověděl včas",
        )
    )
    with _ctx(monkeypatch, oauth=oauth, client=fake), pytest.raises(
        ConnectorError
    ) as exc:
        server.test_connection()
    assert exc.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert exc.value.status is None
    assert oauth.invalidated == 0
    assert len(fake.calls) == 1


def test_missing_delegated_oauth_fails_closed(monkeypatch):
    monkeypatch.setattr(
        server,
        "current_context",
        lambda: types.SimpleNamespace(
            oauth=None,
            config={},
            principal=types.SimpleNamespace(sub="u1", email=None),
        ),
    )
    with pytest.raises(ConnectorError) as exc:
        server.list_orders()
    assert exc.value.code == ErrorCode.CREDENTIAL_INVALID
