"""Sdílené fixtures.

`OPENMCP_PII_SALT` je v provozu povinný (bez něj konektor nenaběhne — viz
`openmcp_sdk.pii.derive_key`). Testy ho proto dostanou automaticky.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _pii_salt(monkeypatch):
    monkeypatch.setenv("OPENMCP_PII_SALT", "test-salt-nikdy-v-produkci")
