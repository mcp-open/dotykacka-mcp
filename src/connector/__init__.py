"""dotykacka-mcp — MCP konektor nad Dotykačka / Dotypos POS API v2.

Balík se jmenuje neutrálně `connector` (stejně jako template a raynet) — slug
žije výhradně v `connector.yaml`, ne v názvu balíku. Konektor je read-only a
používá delegovanou OAuth autorizaci: identitu, konfiguraci i OAuth přístup
(`current_context().oauth`) plní SDK transport před každým voláním nástroje.
"""
