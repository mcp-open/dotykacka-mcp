"""HTTP klient pro Dotykačka / Dotypos API v2 (delegovaná OAuth autorizace).

Na rozdíl od raynetu (statický API klíč z Vaultu) tento konektor drží přístup
přes `openmcp_sdk` runtime OAuth kontext — `current_context().oauth`, tzv.
``DelegatedOAuthClient``. Ten:

* ``access_token() -> str`` — vrátí platný (cachovaný, ~1 h) access token;
  výměnu refresh_token → access token přes ``POST /v2/signin/token`` (viz
  předloha ``predlohy/dotykacka/dotypos_auth.py``) řeší SDK, ne tento klient;
* ``cloud_id`` — id cloudu (provozovny), pro který uživatel přístup schválil;
* ``invalidate()`` — zahodí cachovaný access token (voláme při upstream 401,
  pak jednou zkusíme znovu s čerstvým tokenem).

Klient tedy jen skládá `Authorization: Bearer <token>` a cesty
``/clouds/{cloud_id}/…`` proti base ``https://api.dotykacka.cz/v2``. Retry na
přechodné stavy (429/5xx) i backoff jsou stejné jako v raynet klientu.
"""

from __future__ import annotations

import logging
import random
import time
import urllib.parse
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dotykacka.cz/v2"

# Přechodné stavy, u nichž má smysl opakovat pokus.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 4
_BACKOFF_BASE = 0.5  # s — exponenciálně: 0.5, 1, 2 …
_BACKOFF_CAP = 8.0


class DelegatedOAuth(Protocol):
    """Kontrakt runtime OAuth klienta, který SDK vystaví na `current_context().oauth`.

    Zapsaný jako `Protocol`, aby konektor nezávisel na konkrétní SDK třídě a
    šel snadno mockovat v testech (SDK 0.3 kontrakt se dolaďuje paralelně).
    """

    @property
    def cloud_id(self) -> str: ...

    def access_token(self) -> str: ...

    def invalidate(self) -> None: ...


class DotykackaError(RuntimeError):
    """Chyba komunikace s Dotykačka API (včetně HTTP a rate-limit stavů).

    `status_code` nese HTTP stav, pokud ho Dotykačka vrátila (401/429/4xx/5xx) —
    volající (`connector.server.test_connection`) tak může chybu klasifikovat
    strukturovaně místo parsování textu zprávy. `None` = chyba bez HTTP stavu
    (síťová/timeout, nevalidní JSON tělo).
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _seg(value: Any) -> str:
    """Znormalizuj id na bezpečný segment cesty (percent-encoding zneškodní `/`, `..`)."""
    text = str(value).strip()
    if not text:
        raise DotykackaError("prázdné id v cestě")
    return urllib.parse.quote(text, safe="")


class DotykackaClient:
    def __init__(
        self, oauth: DelegatedOAuth, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._oauth = oauth
        # `transport` je injektovatelný jen kvůli testům (httpx.MockTransport);
        # v provozu zůstává None a httpx použije reálný síťový transport.
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            transport=transport,
        )
        # Poslední hodnoty rate-limit hlaviček (pro diagnostiku).
        self.rate_limit: dict[str, str] = {}

    @property
    def cloud_id(self) -> str:
        return self._oauth.cloud_id

    def close(self) -> None:
        self._client.close()

    # -- veřejné API -----------------------------------------------------------
    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET s retry na přechodné chyby a jedním obnovením tokenu při 401."""
        return self._request("GET", path, params=params)

    def cloud_get(self, resource: str, params: dict[str, Any] | None = None) -> Any:
        """GET zdroje v rámci cloudu: `/clouds/{cloud_id}/{resource}`."""
        return self.get(f"/clouds/{_seg(self.cloud_id)}/{resource}", params)

    def get_cloud(self) -> Any:
        """Detail cloudu (provozovny): `/clouds/{cloud_id}`."""
        return self.get(f"/clouds/{_seg(self.cloud_id)}")

    # -- retry helper ----------------------------------------------------------
    def _sleep_for(self, attempt: int, resp: httpx.Response | None) -> float:
        if resp is not None:
            ra = resp.headers.get("Retry-After")
            if ra and ra.isdigit():
                return min(float(ra), _BACKOFF_CAP)
        delay = min(_BACKOFF_BASE * (2 ** (attempt - 1)), _BACKOFF_CAP)
        return delay + random.uniform(0, delay * 0.25)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        # Jednorázová obrana: při upstream 401 zahodíme cachovaný token a zkusíme
        # ještě jednou s čerstvým — token mohl mezitím vypršet (SDK cache).
        refreshed = False
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            token = self._oauth.access_token()
            resp: httpx.Response | None = None
            try:
                resp = self._client.request(
                    method,
                    path,
                    params=clean,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt < _MAX_ATTEMPTS:
                    delay = self._sleep_for(attempt, None)
                    logger.warning(
                        "Síťová chyba při %s %s (pokus %d/%d): %s — opakuji za %.1fs",
                        method, path, attempt, _MAX_ATTEMPTS, exc, delay,
                    )
                    time.sleep(delay)
                    continue
                raise DotykackaError(
                    f"Síťová chyba při {method} {path} (po {attempt} pokusech): {exc}"
                ) from exc

            for h in ("X-Ratelimit-Limit", "X-Ratelimit-Remaining", "X-Ratelimit-Reset"):
                if h in resp.headers:
                    self.rate_limit[h] = resp.headers[h]

            # Vypršelý access token → jednou zneplatni cache a zkus znovu.
            if resp.status_code == 401 and not refreshed:
                refreshed = True
                self._oauth.invalidate()
                logger.info("Dotykačka 401 — obnovuji access token a zkouším znovu.")
                continue

            # Přechodné stavy → opakuj (kromě posledního pokusu).
            if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS:
                delay = self._sleep_for(attempt, resp)
                logger.warning(
                    "%s %s → HTTP %d (pokus %d/%d) — opakuji za %.1fs",
                    method, path, resp.status_code, attempt, _MAX_ATTEMPTS, delay,
                )
                time.sleep(delay)
                continue

            if resp.status_code == 429:
                raise DotykackaError(
                    "Překročen rate limit Dotykačka API (429) i po opakování.",
                    status_code=429,
                )
            if resp.status_code == 401:
                raise DotykackaError(
                    "Dotykačka odmítla přístupový token (401) — autorizace vypršela "
                    "nebo byla odvolána. Zkus konektor znovu propojit.",
                    status_code=401,
                )
            if resp.status_code == 403:
                raise DotykackaError(
                    "Dotykačka odmítla přístup (403) — schválený rozsah oprávnění "
                    "nepokrývá tento zdroj.",
                    status_code=403,
                )
            if resp.status_code >= 400:
                raise DotykackaError(
                    f"HTTP {resp.status_code} při {method} {path}: {resp.text[:500]}",
                    status_code=resp.status_code,
                )

            try:
                return resp.json()
            except ValueError as exc:
                raise DotykackaError(
                    f"Neplatná JSON odpověď z {path}: {resp.text[:200]}",
                    status_code=resp.status_code,
                ) from exc

        raise DotykackaError(
            f"{method} {path} selhal po {_MAX_ATTEMPTS} pokusech "
            f"(poslední chyba: {last_exc or 'přechodný HTTP stav'})."
        )
