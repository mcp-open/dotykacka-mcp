"""`connector.yaml` — struktura manifestu a `oauth_delegated` auth blok.

Ověřuje se přes `openmcp_sdk.manifest.load_manifest` — stejný přísný parser
(`extra="forbid"`), který používá `run_connector` při startu. Dřív tenhle
test load_manifest záměrně obcházel a parsoval YAML přímo, protože `auth`
blok byl v SDK nový a mohl chybět; dnes je `Auth`/`oauth_delegated` plně
podporovaný model (`openmcp_sdk/manifest.py:Auth`), takže obcházení už nemá
důvod — a bylo to přesně tou dírou, která dřív nechala projít nekompatibilní
`runtime:` blok (vada 1 z plánu sjednocení).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from openmcp_sdk.manifest import load_manifest

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "connector.yaml"


def _manifest():
    return load_manifest(str(MANIFEST_PATH))


def test_basic_identity() -> None:
    m = _manifest()
    assert m.slug == "dotykacka"
    assert m.name == "Dotykačka"
    assert m.version == "0.1.0"
    assert m.sdk_min_version == "0.4.0"
    assert m.category == "pokladna"


def test_oauth_delegated_auth_block() -> None:
    auth = _manifest().auth
    assert auth.type == "oauth_delegated"
    assert auth.provider == "dotykacka"

    op_fields = {f.key: f for f in auth.operator_fields}
    assert set(op_fields) == {"client_id", "client_secret"}
    assert op_fields["client_id"].required is True
    # client_secret je secret; client_id ne.
    assert op_fields["client_secret"].secret is True
    assert op_fields["client_id"].secret is False

    assert auth.runtime_secrets == ["refresh_token", "cloud_id"]


def test_credentials_empty_for_oauth_delegated() -> None:
    """oauth_delegated NESMÍ mít per-uživatelské credentials — jdou z Vaultu."""
    assert _manifest().credentials == []


def test_capabilities_read_only_with_test() -> None:
    caps = _manifest().capabilities
    assert caps.default_read_only is True
    assert caps.supports_test is True
    assert caps.supports_write is False


def test_runtime_requires_pii_salt() -> None:
    assert _manifest().runtime.pii_salt is True


def test_operator_config_cannot_disable_pii_boundary() -> None:
    op = {f.key: f for f in _manifest().operator_config}
    assert "anonymize_data" not in op
    assert set(op) == {"read_only", "timezone"}
    assert "nelze konfigurací vypnout" in _manifest().display.data_handling


def test_egress_allows_dotykacka_over_get_and_post() -> None:
    egress = _manifest().egress
    assert egress.host == "*.dotykacka.cz"
    assert egress.port == 443
    assert egress.path_prefix == "/v2"
    # POST je nutný pro /v2/signin/token (výměna refresh → access token).
    assert set(egress.methods) == {"GET", "POST"}


def test_display_block_is_rich() -> None:
    display = _manifest().display
    assert display is not None
    assert display.capabilities
    assert display.permissions
    assert display.data_handling
    assert display.example_query
    assert display.tools


def test_display_tools_match_registered_tools() -> None:
    """`display.tools` je to, co katalog ukazuje — nesmí se rozejít se serverem."""
    pytest.importorskip("fastmcp")
    import asyncio

    from connector import server

    declared = {t.name for t in _manifest().display.tools}
    registered = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert declared == registered
