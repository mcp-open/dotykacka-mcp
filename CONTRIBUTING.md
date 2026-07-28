# Jak přispět

## Příprava prostředí

SDK se neinstaluje z PyPI (jméno tam patří nesouvisejícímu projektu). Bere se
z gitu podle pinu v `pyproject.toml`, nebo offline z vendorovaného snapshotu:

```bash
python -m venv .venv && . .venv/bin/activate
python release/materialize_sdk.py --root . --output /tmp/openmcp-sdk-dotykacka
pip install /tmp/openmcp-sdk-dotykacka -e '.[test]'
export OPENMCP_PII_SALT="$(openssl rand -hex 32)"
```

## Před odesláním změny

```bash
ruff check src tests
mypy src
openmcp-sdk validate connector.yaml
python -m pytest tests -q
```

Všechny čtyři musí projít — přesně totéž běží v CI.

## Konvence

- **Výchozí větev je `main`.** PR se testuje, ale nebuilduje.
- Nový nástroj potřebuje: registraci přes `@tool(mcp, read_only=...)`, záznam
  v `display.tools` a test, který ho volá přímo.
- Envelope na každém nástroji — `provenance` říká, odkud data jsou, `warnings`
  o tom, zda nejsou oříznutá.
- Komentáře, docstringy i dokumentace jsou **česky**.
- Záznam v `display.tools` musí pokrývat obě locales (`cs` i `sk`).
- Osobní údaje se deklarují v `src/connector/pii_fields.py`, neřeší se ad hoc
  v kódu nástroje.
- Konektor je **vzdálený a výhradně čtecí** — nevydává `.mcpb` ani lokální
  balíček; delegovaný OAuth, týmové sdílení a odvolání přístupu remote
  transport vyžadují.
- Do repozitáře nepatří tajemství, `.env`, produkční logy ani cizí API
  specifikace.

## Bump SDK

SDK se pinuje commitem v `.sdk-ref`. Bump je jednořádkový diff, který lze
reviewovat:

```bash
echo "<nový-sha>" > .sdk-ref
```

Noční `sdk-canary` workflow běží proti SDK `main` a otevře issue, když se pin
rozejde — aby se rozdíl neobjevil až při bumpu, kdy je největší.

## Změny, které potřebují poznámku v CHANGELOG.md

Manifest, autentizace, tvar odpovědi nástroje a cokoliv, co mění
pseudonymizační tokeny. **Tokeny jsou externě viditelný kontrakt** — když se
změní, uživatel uvidí jiné ID pro stejná data.

## Bezpečnostní hranice

Tyto věci nejsou kosmetika a review si na ně dává pozor:

- `readOnlyHint` — při `read_only=true` SDK fail-closed odregistruje vše bez
  ní; chybějící anotace znamená, že nástroj v produkci tiše zmizí
- `cloud_id` v cestě smí pocházet jen z uloženého propojení, nikdy od modelu
- tělo upstream odpovědi **nikdy** nesmí jít do chybové zprávy pro model
- `egress` v manifestu musí pokrývat vše, na co klient sahá

Zranitelnosti hlaste podle [SECURITY.md](SECURITY.md), nikdy veřejným issue.
