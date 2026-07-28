"""Pydantic modely pro `dotykacka` connector.

Každý nástroj vrací model odvozený z `openmcp_sdk.envelope.EnvelopeBase`
(`data` + `provenance` + `warnings`), takže odpověď vždy nese původ dat
(Dotykačka API v2) a případná upozornění (např. useknutá agregace). Modely
jsou tenké obálky nad surovou (po PII sanitizaci) odpovědí — doménové typy
Dotykačky se úmyslně nekopírují 1:1, POS schéma je široké a proměnlivé.
"""

from __future__ import annotations

from typing import Any

from openmcp_sdk.envelope import EnvelopeBase


class _DataEnvelope(EnvelopeBase):
    """Společný základ: envelope nesoucí surová data konkrétního nástroje."""

    data: Any


class CloudInfoResult(_DataEnvelope):
    """Výstup `get_cloud_info` — základní informace o cloudu (provozovně)."""


class OrderListResult(_DataEnvelope):
    """Výstup `list_orders` — stránka účtenek/objednávek (po PII sanitizaci)."""


class OrderDetailResult(_DataEnvelope):
    """Výstup `get_order` — detail účtenky/objednávky včetně položek."""


class ProductListResult(_DataEnvelope):
    """Výstup `list_products` — stránka katalogu produktů."""


class CategoryListResult(_DataEnvelope):
    """Výstup `list_categories` — stránka kategorií produktů."""


class WarehouseListResult(_DataEnvelope):
    """Výstup `list_warehouses` — sklady provozovny."""


class CustomerListResult(_DataEnvelope):
    """Výstup `list_customers` — zákazníci s pseudonymizovanými osobními údaji."""


class EmployeeListResult(_DataEnvelope):
    """Výstup `list_employees` — zaměstnanci/obsluha provozovny."""


class SalesSummaryResult(_DataEnvelope):
    """Výstup `sales_summary` — agregované prodejní metriky za období.

    `data` je slovník s poli jako ``period``, ``document_count``, ``revenue``,
    ``average_receipt``, ``by_document_type``, ``top_products`` a ``vat`` (viz
    `connector.server.sales_summary`).
    """
