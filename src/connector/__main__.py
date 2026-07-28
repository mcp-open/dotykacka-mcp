"""`python -m connector` — Dockerfile ENTRYPOINT i lokální CLI vstupní bod."""

from __future__ import annotations

from openmcp_sdk import run_connector

from connector.pii_fields import POLICY
from connector.server import mcp, test_connection

# `run_connector` sám ověří PII salt (`runtime.pii_salt` v manifestu) a
# vynutí invariant pii_salt ⟺ pii is not None — konektor už si `require_salt()`
# nevolá ručně (SDK 0.4, viz `openmcp_sdk.runtime.run_connector`).
run_connector("connector.yaml", mcp, test_connection=test_connection, pii=POLICY)
