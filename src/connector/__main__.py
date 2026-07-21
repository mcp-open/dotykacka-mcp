"""`python -m connector` — Dockerfile ENTRYPOINT i lokální CLI vstupní bod."""

from __future__ import annotations

from openmcp_sdk import run_connector

from connector.pii import require_salt
from connector.server import mcp, test_connection

# Fail-fast: bez PII saltu by pseudonymizace ztratila stabilitu tokenů, a to
# tiše — projevilo by se to až po prvním restartu podu. Radši nenastartovat.
require_salt()

run_connector("connector.yaml", mcp, test_connection=test_connection)
