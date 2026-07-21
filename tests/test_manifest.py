"""`connector.yaml` — struktura manifestu a nový `oauth_delegated` auth blok.

Konektor cílí na SDK 0.3.0 (`sdk_min_version`), který teprve zavádí `auth` blok
a `oauth_delegated` typ. Aby testy nezávisely na tom, že je nová verze SDK už
nainstalovaná (staví se paralelně), ověřují tvar manifestu přímo z YAML místo
přes `openmcp_sdk.manifest.load_manifest`. Cíl je, aby byl manifest korektní a
konzistentní se serverem (`display.tools` == reálně zaregistrované nástroje).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "connector.yaml"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_basic_identity() -> None:
    m = _manifest()
    assert m["slug"] == "dotykacka"
    assert m["name"] == "Dotykačka"
    assert m["version"] == "0.1.0"
    assert m["sdk_min_version"] == "0.3.0"
    assert m["category"] == "pokladna"


def test_oauth_delegated_auth_block() -> None:
    auth = _manifest()["auth"]
    assert auth["type"] == "oauth_delegated"
    assert auth["provider"] == "dotykacka"

    op_fields = {f["key"]: f for f in auth["operator_fields"]}
    assert set(op_fields) == {"client_id", "client_secret"}
    assert op_fields["client_id"]["required"] is True
    # client_secret je secret; client_id ne.
    assert op_fields["client_secret"].get("secret") is True
    assert op_fields["client_id"].get("secret", False) is False

    assert auth["runtime_secrets"] == ["refresh_token", "cloud_id"]


def test_credentials_empty_for_oauth_delegated() -> None:
    """oauth_delegated NESMÍ mít per-uživatelské credentials — jdou z Vaultu."""
    assert _manifest()["credentials"] == []


def test_capabilities_read_only_with_test() -> None:
    caps = _manifest()["capabilities"]
    assert caps["default_read_only"] is True
    assert caps["supports_test"] is True
    assert caps["supports_write"] is False


def test_operator_config_has_gdpr_toggle() -> None:
    op = {f["key"]: f for f in _manifest()["operator_config"]}
    assert op["anonymize_data"]["default"] is True
    assert op["anonymize_data"]["type"] == "bool"


def test_egress_allows_dotykacka_over_get_and_post() -> None:
    egress = _manifest()["egress"]
    assert egress["host"] == "*.dotykacka.cz"
    assert egress["port"] == 443
    assert egress["path_prefix"] == "/v2"
    # POST je nutný pro /v2/signin/token (výměna refresh → access token).
    assert set(egress["methods"]) == {"GET", "POST"}


def test_display_block_is_rich() -> None:
    display = _manifest()["display"]
    assert display["capabilities"]
    assert display["permissions"]
    assert display["data_handling"]
    assert display["example_query"]
    assert display["tools"]


def test_display_tools_match_registered_tools() -> None:
    """`display.tools` je to, co katalog ukazuje — nesmí se rozejít se serverem."""
    pytest.importorskip("fastmcp")
    import asyncio

    from connector import server

    declared = {t["name"] for t in _manifest()["display"]["tools"]}
    registered = set(asyncio.run(server.mcp.get_tools()))
    assert declared == registered
