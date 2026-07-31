"""FastMCP server nad Dotykačka / Dotypos POS API v2 (read-only) s GDPR pseudonymizací.

Delegovaná OAuth autorizace: identitu i OAuth přístup plní SDK transport před
každým voláním nástroje. Nástroj si přes ``current_context().oauth``
(``DelegatedOAuthClient``) vyžádá krátkodobý access token a ``cloud_id``, sestaví
sdílený `openmcp_sdk.http.UpstreamClient` (``token_provider=ctx.oauth``) a volá
Dotykačka API. Refresh_token → access token výměnu (``POST /v2/signin/token``)
i cache tokenu řeší SDK; při upstream 401 klient jednou zavolá
``oauth.invalidate()`` a zkusí to znovu — to už dělá `UpstreamClient` sám.

Vzory (plain funkce místo přímého `@mcp.tool` dekorátoru, envelope výstup,
per-request PII sanitizace, provenance) jsou 1:1 s `raynet` konektorem — viz
docstring `connectors/raynet-mcp/src/connector/server.py`.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from openmcp_sdk import (
    ConnectorError,
    DelegatedOAuthClient,
    ErrorCode,
    Provenance,
    current_context,
    now_utc_iso,
)
from openmcp_sdk.http import NO_RETRY, SERVER_ERRORS_ONLY, UpstreamClient
from openmcp_sdk.http import encode_segment as _seg
from openmcp_sdk.pii import derive_key
from pydantic import Field

from connector.pii_fields import POLICY, DotykackaPseudonymizer
from connector.schemas import (
    CategoryListResult,
    CloudInfoResult,
    CustomerListResult,
    EmployeeListResult,
    OrderDetailResult,
    OrderListResult,
    ProductListResult,
    SalesSummaryResult,
    WarehouseListResult,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dotykacka.cz/v2"

# Default a tvrdý strop parametru `limit` u list nástrojů. LLM občas pošle limit
# v tisících — clamp chrání kontextové okno i Dotykačka API rate-limit.
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100

# Strop, kolik dokladů smí `sales_summary` stáhnout. Chrání paměť i API rate-limit;
# u delšího období se výsledek označí `truncated=True`.
_MAX_RECORDS = 500
_PAGE_LIMIT = 100

# `cloudId` je podle Dotykačka API kladné celočíselné ID. Přijímáme jen
# kanonický zápis: okolní mezery či vedoucí nuly by jinak mohly ve výměně OAuth
# tokenu, URL a PII tenant scope označovat tutéž provozovnu různě.
_CLOUD_ID_RE = re.compile(r"^[1-9][0-9]{0,18}$")

# Diagnostický request control plane musí doběhnout pod jeho tvrdým timeoutem.
# Jeden pokus brání tomu, aby výchozí 4 × 30 s retry udělalo z dočasné 5xx
# falešný credential timeout.
_PROBE_TIMEOUT_S = 5.0
_PROBE_CONNECT_TIMEOUT_S = 2.0

mcp: FastMCP = FastMCP(
    "dotykacka",
    instructions=(
        "Čtecí přístup k pokladně Dotykačka / Dotypos (API v2). Osobní data "
        "zákazníků jsou v odpovědích pseudonymizována stabilními tokeny typu "
        "<EMAIL_3f9c1a2b4d5e>, <PHONE_…>, <NAME_…> — stejná hodnota dá stejný "
        "token, ale reálné hodnoty nejsou dostupné a token nelze rozklíčovat "
        "zpět. Prodejní čísla, produkty a kategorie procházejí beze změny. "
        "K vyhledání dokladu použij id nebo časový filtr, ne osobní údaj."
    ),
)


# --- Sdílené popisy parametrů (Annotated[…, Field]) — LLM je vidí ve schématu. ---
_D_LIMIT = Field(
    description="Počet záznamů na stránku (max 100, víc se ořízne).",
    ge=1,
    le=_MAX_LIMIT,
)
_D_PAGE = Field(description="Číslo stránky (1 = od začátku).", ge=1)
_D_DATE_FROM = Field(description="Začátek období, den YYYY-MM-DD (včetně).")
_D_DATE_TO = Field(description="Konec období, den YYYY-MM-DD (exkluzivně).")


def _clamp_limit(limit: int) -> int:
    if limit <= 0:
        return _DEFAULT_LIMIT
    return min(limit, _MAX_LIMIT)


def _oauth() -> DelegatedOAuthClient:
    """Vrať povinný runtime OAuth klient nebo bezpečně odmítni volání."""
    oauth = current_context().oauth
    if oauth is None:
        raise ConnectorError(
            ErrorCode.CREDENTIAL_INVALID,
            "Chybí platná autorizace Dotykačky",
        )
    return oauth


def _normalize_cloud_id(value: Any) -> str:
    """Validuj uložený OAuth selector dřív, než vstoupí do tokenu, URL a PII scope."""
    if isinstance(value, bool):
        raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Uložené cloud ID je neplatné.")
    raw = str(value) if isinstance(value, int) else value
    if not isinstance(raw, str) or not _CLOUD_ID_RE.fullmatch(raw):
        raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Uložené cloud ID je neplatné.")
    return raw


def _pii_owner_scope(principal: Any) -> str:
    """Vlastník credentials, ne aktuální člen týmu, určuje stabilitu PII tokenů."""
    owner_id = getattr(principal, "credential_owner_id", None)
    if isinstance(owner_id, str) and owner_id:
        return owner_id
    sub = getattr(principal, "sub", None)
    if isinstance(sub, str) and sub:
        return sub
    raise ConnectorError(ErrorCode.INTERNAL, "Chybí ověřená identita požadavku.")


def _iso(dt: datetime) -> str:
    """UTC ISO timestamp ve formátu, který Dotykačka filtr očekává (viz předloha)."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _configured_timezone() -> ZoneInfo:
    """Časové pásmo provozovny; POS den se nesmí řezat na půlnoci UTC."""
    raw = str(current_context().config.get("timezone", "Europe/Prague")).strip()
    try:
        return ZoneInfo(raw)
    except ZoneInfoNotFoundError as exc:
        raise ConnectorError(ErrorCode.INVALID_INPUT, "Neplatné časové pásmo konektoru.") from exc


def _day(value: str, label: str) -> datetime:
    """Parsuj YYYY-MM-DD jako lokální půlnoc provozovny a převeď ji na UTC."""
    try:
        if len(value) != 10:
            raise ValueError
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ConnectorError(
            ErrorCode.INVALID_INPUT, f"{label} musí být datum ve formátu YYYY-MM-DD."
        ) from exc
    return datetime.combine(
        parsed,
        datetime.min.time(),
        tzinfo=_configured_timezone(),
    ).astimezone(UTC)


class _Session:
    """HTTP klient + pseudonymizér pro **jedno volání nástroje**.

    Klient žije po dobu celého volání (agregace v `sales_summary` prochází víc
    stránek — jinak by každá znamenala nový TLS handshake). Pseudonymizér je taky
    per-request, protože jeho HMAC klíč je odvozený ze `sub`.
    """

    def __init__(self) -> None:
        ctx = current_context()
        oauth = _oauth()
        try:
            self.cloud_id = _normalize_cloud_id(oauth.cloud_id)
        except AttributeError as exc:
            raise ConnectorError(
                ErrorCode.CREDENTIAL_INVALID, "Uložené cloud ID je neplatné."
            ) from exc
        # `ctx.oauth` splňuje `openmcp_sdk.http.TokenProvider` (access_token() +
        # invalidate()) — `UpstreamClient` samo obnoví token jednou při 401.
        self.client = UpstreamClient(
            base_url=BASE_URL, token_provider=oauth, retry=SERVER_ERRORS_ONLY
        )
        # Pseudonymizace je povinná bezpečnostní hranice, ne provozní přepínač.
        # Cloud je skutečný datový tenant. Jeden uživatel může přepojit jiný
        # cloud; jeho tokeny pak nesmí být korelovatelné s předchozím cloudem.
        self.pseudo = DotykackaPseudonymizer(
            derive_key(_pii_owner_scope(ctx.principal), self.cloud_id),
            POLICY,
        )

    def sanitize(self, data: Any, *, person_names: bool = False) -> Any:
        return self.pseudo.sanitize(data, person_scope=person_names)

    def cloud_get(self, resource: str, params: dict[str, Any] | None = None) -> Any:
        """GET zdroje v rámci cloudu: `/clouds/{cloud_id}/{resource}`."""
        cloud = self.client.seg(self.cloud_id)
        path = f"/clouds/{cloud}/{resource}" if resource else f"/clouds/{cloud}"
        return self.client.get_json(path, params)

    def get_cloud(self) -> Any:
        """Detail cloudu (provozovny): `/clouds/{cloud_id}`."""
        return self.cloud_get("")

    def provenance(self, resource: str) -> Provenance:
        """Provenance svázaná se stejným OAuth/cloud snapshotem jako HTTP call."""
        cloud = self.client.seg(self.cloud_id)
        return Provenance(
            source_id="dotykacka",
            source_url=f"{BASE_URL}/clouds/{cloud}/{resource}".rstrip("/"),
            retrieved_at=now_utc_iso(),
            freshness="live",
        )

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *exc: object) -> None:
        self.client.close()


def _fetch(
    session: _Session,
    resource: str,
    params: dict[str, Any] | None = None,
    *,
    person_names: bool = False,
) -> Any:
    """GET zdroje v rámci cloudu + (volitelně) PII sanitizace.

    Chyby jsou už `ConnectorError` — mapuje je sdílený `UpstreamClient`.
    """
    data = session.cloud_get(resource, params)
    return session.sanitize(data, person_names=person_names)


def _page_items(payload: Any) -> list[dict[str, Any]]:
    """Vytáhni pole záznamů z odpovědi Dotykačky ({data:[…]} nebo přímo list)."""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _collect_orders(
    session: _Session,
    date_from: str | None,
    date_to: str | None,
    include_items: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """Projdi stránky objednávek v období a vrať (záznamy, truncated).

    Stahuje po ``_PAGE_LIMIT`` až do ``_MAX_RECORDS`` (bezpečnostní strop) — u
    delšího období se výsledek označí truncated=True (agregace se tiše neusekne).
    """
    params: dict[str, Any] = {"sort": "-created", "limit": _PAGE_LIMIT}
    filters = _order_date_filter(date_from, date_to)
    if filters:
        params["filter"] = filters
    if include_items:
        params["include"] = "orderItems"

    records: list[dict[str, Any]] = []
    page = 1
    truncated = False
    while True:
        payload = _fetch(session, "orders", {**params, "page": page})
        batch = _page_items(payload)
        if not batch:
            break
        records.extend(batch)
        if len(records) >= _MAX_RECORDS:
            overflow = len(records) > _MAX_RECORDS
            records = records[:_MAX_RECORDS]
            # Plná poslední povolená stránka sama neříká, zda existuje 501.
            # Pokud upstream neposkytne spolehlivý total, levná sonda další
            # stránky rozliší přesně 500 od 500+ bez stažení dalších dat.
            total = payload.get("totalItemsCount") if isinstance(payload, dict) else None
            if overflow:
                truncated = True
            elif isinstance(total, int) and not isinstance(total, bool) and total >= 0:
                truncated = total > _MAX_RECORDS
            elif len(batch) == _PAGE_LIMIT:
                probe = _fetch(session, "orders", {**params, "page": page + 1})
                truncated = bool(_page_items(probe))
            break
        if len(batch) < _PAGE_LIMIT:
            break
        page += 1
    return records, truncated


def _order_date_filter(date_from: str | None, date_to: str | None) -> str | None:
    """Sestav Dotykačka `filter` výraz pro rozsah pole `created`."""
    parsed_from = _day(date_from, "date_from") if date_from else None
    parsed_to = _day(date_to, "date_to") if date_to else None
    if parsed_from is not None and parsed_to is not None and parsed_from >= parsed_to:
        raise ConnectorError(
            ErrorCode.INVALID_INPUT, "date_from musí být dříve než date_to."
        )
    clauses = []
    if parsed_from is not None:
        clauses.append(f"created|gteq|{_iso(parsed_from)}")
    if parsed_to is not None:
        clauses.append(f"created|lt|{_iso(parsed_to)}")
    return ";".join(clauses) if clauses else None


# =============================================================================
# Nástroje — účtenky / objednávky
# =============================================================================
def list_orders(
    date_from: Annotated[str | None, _D_DATE_FROM] = None,
    date_to: Annotated[str | None, _D_DATE_TO] = None,
    include_items: Annotated[
        bool, Field(description="Připojit položky dokladu (orderItems).")
    ] = False,
    limit: Annotated[int, _D_LIMIT] = _DEFAULT_LIMIT,
    page: Annotated[int, _D_PAGE] = 1,
) -> OrderListResult:
    """Seznam účtenek/objednávek (jedna stránka) s volitelným filtrem podle data.

    `date_from`/`date_to` jsou dny YYYY-MM-DD (od včetně, do exkluzivně) nad polem
    `created`. Bez nich vrátí nejnovější doklady. Osobní údaje zákazníků jsou
    pseudonymizovány; prodejní čísla procházejí. Pro víc dokladů stránkuj `page`.
    """
    params: dict[str, Any] = {
        "sort": "-created",
        "limit": _clamp_limit(limit),
        "page": max(1, page),
    }
    filters = _order_date_filter(date_from, date_to)
    if filters:
        params["filter"] = filters
    if include_items:
        params["include"] = "orderItems"
    with _Session() as session:
        data = _fetch(session, "orders", params)
        provenance = session.provenance("orders")
    return OrderListResult(data=data, provenance=provenance, warnings=[])


def get_order(
    order_id: Annotated[int | str, Field(description="ID účtenky/objednávky.")],
    include_items: Annotated[
        bool, Field(description="Připojit položky dokladu (orderItems).")
    ] = True,
) -> OrderDetailResult:
    """Detail účtenky/objednávky podle ID, ve výchozím stavu včetně položek."""
    params = {"include": "orderItems"} if include_items else None
    with _Session() as session:
        data = _fetch(session, f"orders/{_seg(order_id)}", params)
        provenance = session.provenance("orders")
    return OrderDetailResult(data=data, provenance=provenance, warnings=[])


# =============================================================================
# Nástroje — katalog a provozovna
# =============================================================================
def list_products(
    limit: Annotated[int, _D_LIMIT] = _DEFAULT_LIMIT,
    page: Annotated[int, _D_PAGE] = 1,
) -> ProductListResult:
    """Katalog produktů (název, cena, DPH, kategorie). Bez osobních dat."""
    with _Session() as session:
        data = _fetch(session, "products", {"limit": _clamp_limit(limit), "page": max(1, page)})
        provenance = session.provenance("products")
    return ProductListResult(data=data, provenance=provenance, warnings=[])


def list_categories(
    limit: Annotated[int, _D_LIMIT] = _DEFAULT_LIMIT,
    page: Annotated[int, _D_PAGE] = 1,
) -> CategoryListResult:
    """Kategorie produktů. Bez osobních dat."""
    with _Session() as session:
        data = _fetch(session, "categories", {"limit": _clamp_limit(limit), "page": max(1, page)})
        provenance = session.provenance("categories")
    return CategoryListResult(data=data, provenance=provenance, warnings=[])


def list_warehouses(
    limit: Annotated[int, _D_LIMIT] = _DEFAULT_LIMIT,
    page: Annotated[int, _D_PAGE] = 1,
) -> WarehouseListResult:
    """Sklady provozovny. Bez osobních dat."""
    with _Session() as session:
        data = _fetch(session, "warehouses", {"limit": _clamp_limit(limit), "page": max(1, page)})
        provenance = session.provenance("warehouses")
    return WarehouseListResult(data=data, provenance=provenance, warnings=[])


def list_customers(
    limit: Annotated[int, _D_LIMIT] = _DEFAULT_LIMIT,
    page: Annotated[int, _D_PAGE] = 1,
) -> CustomerListResult:
    """Zákazníci provozovny. Jména, e-maily, telefony a adresy jsou pseudonymizovány.

    K vyhledání konkrétního zákazníka použij jeho id — osobní údaje jsou tokeny a
    nedají se rozklíčovat zpět.
    """
    with _Session() as session:
        data = _fetch(
            session,
            "customers",
            {"limit": _clamp_limit(limit), "page": max(1, page)},
            person_names=True,
        )
        provenance = session.provenance("customers")
    return CustomerListResult(data=data, provenance=provenance, warnings=[])


def list_employees(
    limit: Annotated[int, _D_LIMIT] = _DEFAULT_LIMIT,
    page: Annotated[int, _D_PAGE] = 1,
) -> EmployeeListResult:
    """Zaměstnanci/obsluha provozovny. Osobní údaje jsou pseudonymizovány."""
    with _Session() as session:
        data = _fetch(
            session,
            "employees",
            {"limit": _clamp_limit(limit), "page": max(1, page)},
            person_names=True,
        )
        provenance = session.provenance("employees")
    return EmployeeListResult(data=data, provenance=provenance, warnings=[])


def get_cloud_info() -> CloudInfoResult:
    """Základní informace o cloudu (provozovně), pro který je konektor propojen."""
    with _Session() as session:
        data = session.sanitize(session.get_cloud())
        provenance = session.provenance("")
    return CloudInfoResult(data=data, provenance=provenance, warnings=[])


# =============================================================================
# Analytika — prodejní souhrn (spojení stránek objednávek + odvozené metriky)
# =============================================================================
def _num(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default
    return parsed if parsed.is_finite() else default


def _quantized_float(value: Decimal, quantum: Decimal) -> float:
    """Vrať vždy JSON-bezpečné konečné číslo i pro vadný extrémní upstream."""
    try:
        result = float(value.quantize(quantum))
    except (InvalidOperation, OverflowError, ValueError):
        return 0.0
    return result if isfinite(result) else 0.0


def _money(value: Decimal) -> float:
    return _quantized_float(value, Decimal("0.01"))


def _quantity(value: Decimal) -> float:
    return _quantized_float(value, Decimal("0.001"))


def sales_summary(
    date_from: Annotated[str | None, _D_DATE_FROM] = None,
    date_to: Annotated[str | None, _D_DATE_TO] = None,
) -> SalesSummaryResult:
    """Souhrn prodejů za období — tržby, počty dokladů, průměrná účtenka, top produkty, DPH.

    Projde účtenky v období (`created`, dny YYYY-MM-DD; bez zadání posledních 30
    dní), oddělí stornované, a spočítá: tržbu (nestornovaných), počet dokladů,
    průměrnou a mediánovou účtenku, rozpad podle typu dokladu, top 15 produktů
    podle tržby a tržbu podle sazby DPH. U dlouhého období se stáhne max 500
    dokladů a výsledek se označí `truncated=True`. (Obdoba `analyze.py` z předlohy.)
    """
    if not date_from and not date_to:
        today = datetime.now(_configured_timezone()).date()
        date_to = (today + timedelta(days=1)).isoformat()
        date_from = (today - timedelta(days=29)).isoformat()

    with _Session() as session:
        orders, truncated = _collect_orders(session, date_from, date_to, include_items=True)
        provenance = session.provenance("orders")

    valid = [o for o in orders if not o.get("canceledDate")]
    canceled = [o for o in orders if o.get("canceledDate")]
    values = sorted(_num(o.get("totalValueRounded")) for o in valid)
    revenue_decimal = sum(values, Decimal("0"))
    revenue = _money(revenue_decimal)
    count_valid = len(valid)

    def _median(xs: list[Decimal]) -> float:
        m = len(xs)
        if not m:
            return 0.0
        value = xs[m // 2] if m % 2 else (xs[m // 2 - 1] + xs[m // 2]) / 2
        return _money(value)

    by_type: dict[str, int] = defaultdict(int)
    for o in orders:
        by_type[str(o.get("documentType", "?"))] += 1

    prod_rev: dict[str, Decimal] = defaultdict(Decimal)
    prod_qty: dict[str, Decimal] = defaultdict(Decimal)
    vat_rev: dict[str, Decimal] = defaultdict(Decimal)
    for o in valid:
        for it in o.get("orderItems", []) or []:
            if not isinstance(it, dict) or it.get("canceledDate"):
                continue
            name = it.get("name") or "(bez názvu)"
            tot = _num(it.get("totalPriceWithVat"))
            prod_rev[name] += tot
            prod_qty[name] += _num(it.get("quantity"))
            vat_rev[str(it.get("vat"))] += tot

    top_products = [
        {
            "name": name,
            "revenue": _money(rev),
            "quantity": _quantity(prod_qty[name]),
        }
        for name, rev in sorted(prod_rev.items(), key=lambda kv: -kv[1])[:15]
    ]
    currency = next((o.get("currency") for o in valid if o.get("currency")), None)

    summary = {
        "period": {"from": date_from, "to": date_to},
        "currency": currency,
        "document_count": len(orders),
        "valid_count": count_valid,
        "canceled_count": len(canceled),
        "revenue": revenue,
        "average_receipt": _money(revenue_decimal / count_valid) if count_valid else 0.0,
        "median_receipt": _median(values),
        "max_receipt": _money(values[-1]) if values else 0.0,
        "min_receipt": _money(values[0]) if values else 0.0,
        "by_document_type": dict(by_type),
        "top_products": top_products,
        "vat": {k: _money(v) for k, v in sorted(vat_rev.items(), key=lambda kv: -kv[1])},
        "truncated": truncated,
    }
    warnings = (
        [f"Období obsahuje víc než {_MAX_RECORDS} dokladů — souhrn je z podmnožiny dat."]
        if truncated
        else []
    )
    return SalesSummaryResult(
        data=summary,
        provenance=provenance,
        warnings=warnings,
    )


# =============================================================================
# Interní test spojení (SDK `POST /test`, supports_test: true)
# =============================================================================
def test_connection() -> str:
    """Ad-hoc test spojení s Dotykačkou — ověří, že OAuth přístup funguje.

    Lehký jednorázový GET `/clouds/{cloud_id}` (bez PII sanitizace ani envelope)
    má krátký tvrdý timeout. Chyby se klasifikují strukturovaně přes
    `ConnectorError.status`, ne parsováním textu: 400/401 jsou vadné nebo
    odvolané credentials, 403 chybějící oprávnění, 404/410 neznámý cloud, 429
    omezení provozu a ostatní selhání dočasná nedostupnost upstreamu. Klientovi
    se nikdy nevrací syrový text výjimky ani identifikátor cloudu.
    """
    try:
        oauth = _oauth()
        cloud_id = _normalize_cloud_id(oauth.cloud_id)
        client = UpstreamClient(
            base_url=BASE_URL,
            token_provider=oauth,
            retry=NO_RETRY,
            timeout=_PROBE_TIMEOUT_S,
            connect_timeout=_PROBE_CONNECT_TIMEOUT_S,
        )
    except (ConnectorError, AttributeError, KeyError) as exc:
        if isinstance(exc, ConnectorError):
            raise
        raise ConnectorError(
            ErrorCode.CREDENTIAL_INVALID,
            "Chybí platná autorizace Dotykačky",
        ) from exc

    try:
        client.get_json(f"/clouds/{client.seg(cloud_id)}")
    except ConnectorError as exc:
        if exc.status in (400, 401) or exc.code is ErrorCode.CREDENTIAL_INVALID:
            raise ConnectorError(
                ErrorCode.CREDENTIAL_INVALID,
                "Autorizace Dotykačky vypršela nebo byla odvolána — propoj konektor znovu.",
                status=exc.status,
            ) from exc
        if exc.status == 403 or exc.code is ErrorCode.PROVIDER_PERMISSION_DENIED:
            raise ConnectorError(
                ErrorCode.PROVIDER_PERMISSION_DENIED,
                "Dotykačka odepřela přístup — účet nemá potřebné oprávnění k API.",
                status=exc.status,
            ) from exc
        if exc.status in (404, 410) or exc.code is ErrorCode.INSTANCE_UNKNOWN:
            raise ConnectorError(
                ErrorCode.INSTANCE_UNKNOWN,
                "Dotykačka nezná uložený cloud — propoj konektor znovu.",
                status=exc.status,
            ) from exc
        if exc.status == 429 or exc.code is ErrorCode.RATE_LIMITED:
            raise ConnectorError(
                ErrorCode.RATE_LIMITED,
                "Dotykačka dočasně omezila počet požadavků. Zkus to prosím později.",
                status=exc.status,
            ) from exc
        raise ConnectorError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            "Nepodařilo se spojit s Dotykačkou — zkus to prosím znovu.",
            status=exc.status,
        ) from exc
    finally:
        client.close()

    return "Připojení k Dotykačce je funkční."


# =============================================================================
# Registrace nástrojů
# =============================================================================
_READ_ONLY = ToolAnnotations(readOnlyHint=True)

for _tool in (
    get_cloud_info,
    list_orders,
    get_order,
    list_products,
    list_categories,
    list_warehouses,
    list_customers,
    list_employees,
    sales_summary,
):
    mcp.tool(_tool, annotations=_READ_ONLY)
