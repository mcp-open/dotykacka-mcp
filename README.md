# Dotykačka MCP

Read-only konektor pro Dotykačka / Dotypos API v2. Běží jako vzdálený OpenMCP
server; delegovaný OAuth přístup i konkrétní provozovna se vážou na uživatele a
pracovní prostor. Osobní údaje zákazníků a zaměstnanců se před odpovědí
pseudonymizují.

## Co tento repository vydává

- hostovaný Dotykačka runtime;
- interní Gemini CLI remote Extension kandidát;
- interní OpenAI/ChatGPT operator handoff kandidát.

Dotykačka není lokální executable ani MCPB. Vzdálený OAuth, týmové sdílení,
centrální odvolání přístupu a bezpečná rotace credentials vyžadují remote
transport.

## Lokální vývoj

`openmcp-sdk` není na PyPI (jméno tam patří nesouvisejícímu projektu).
`pip install -e '.[test]'` ho stáhne z GitHubu podle commitu připnutého
v `pyproject.toml`; postup níže místo toho použije vendorovaný snapshot
z repozitáře, takže staví offline a přesně proti tomu SDK, se kterým se
staví produkční image.

```bash
python -m venv .venv
. .venv/bin/activate
python release/materialize_sdk.py --root . --output /tmp/openmcp-sdk-dotykacka
pip install /tmp/openmcp-sdk-dotykacka -e '.[test]'
ruff check src tests
mypy src
openmcp-sdk validate connector.yaml
pytest -q
```

## Vyrenderování klientských kandidátů

Výstupní adresář musí být nový nebo prázdný:

```bash
openmcp-sdk render-adapters connector.yaml distribution.yaml \
  --output /tmp/openmcp-dotykacka-adapters
```

Remote render záměrně nevytvoří `.mcpb`:

- `openai/submission.json` je `operator_handoff` s `installable: false`;
- `openmcp-dotykacka-0.1.1-gemini.zip` je kontrolovaný operátorský bundle,
  nikoli přímá veřejná instalace.

## Gemini CLI — interní pilot

Po rozbalení kandidáta:

```bash
gemini extensions validate /cesta/k/rozbalenemu/gemini
gemini extensions install /cesta/k/rozbalenemu/gemini
gemini extensions update openmcp-dotykacka
gemini extensions uninstall openmcp-dotykacka
```

Veřejná instalace musí používat reviewovaný Git tag:

```bash
gemini extensions install \
  https://github.com/mcp-open/dotykacka-mcp --ref <podepsany-tag>
```

Instalace si vyžádá přesnou HTTPS adresu pracovního prostoru. Extension ji
uloží jako citlivé nastavení a OAuth spustí přes standardní dynamic discovery.
Uživatel nikdy nevkládá access token, refresh token, heslo ani API klíč do
konverzace.

## OpenAI/ChatGPT — interní pilot

`openai/submission.json` není automaticky instalovatelný plugin. Je to
fail-closed podklad pro ruční Apps/Plugin submission a obsahuje:

- konkrétní review MCP URL a workspace URL template;
- přesný externí OAuth scope `mcp` a protected-resource metadata URL;
- přesných devět namespaced read-only nástrojů;
- povinné review a web/mobile E2E brány.

Před skutečným submission musí být současně splněno:

1. konkrétní review workspace existuje, obsahuje pouze tento connector release
   a je veřejně dosažitelný přes HTTPS;
2. protected-resource a authorization-server metadata, authorization code,
   PKCE `S256`, `resource` audience a rotující refresh-token lifecycle projdou
   E2E; OIDC `offline_access` se nežádá;
3. OpenAI organizace i MCP doména jsou ověřené;
4. privacy policy a právní údaje jsou schválené a živé;
5. review účet funguje bez MFA a bez dalšího nastavení;
6. tool scan odpovídá živému serveru a testy projdou v ChatGPT web i mobile.

Dokud tyto body nemají důkaz, kandidát se nesmí označit jako publikovaný,
schválený ani zákaznicky instalovatelný.

## Release a supply chain

`distribution-release.yml` je main-only interní candidate pipeline. Používá
checksum-bound SDK snapshot, přesné dependency locky, content manifest, SBOM,
Trivy scan, keyless Cosign podpis, provenance a fail-closed release gate.
GitHub CI pouze testuje zdroj a renderované adaptéry; neobsahuje self-hosted
build, cluster credential ani deploy dispatch. Hostinger development image se
buildí, skenuje, nasazuje a ověřuje pouze schválenou přímou SSH Manager release
cestou a přesný výsledek se zapisuje do platform evidence.

Hosted transport je explicitně bezstavový, takže requesty bezpečně procházejí
přes více replik bez session affinity. Runtime image používá digest-pinned
Alpine base; release scan musí mít nula HIGH/CRITICAL nálezů a nula secrets.
Pseudonymizaci osobních údajů nelze operátorskou konfigurací vypnout.

Veřejné CTA a platformová release eligibility se povolí až po clean-client
E2E a zápisu konkrétních per-platform důkazů do platformového release registru.

Aktuální interní validační výsledky a otevřené externí brány jsou v
[`docs/CLIENT-ADAPTER-EVIDENCE.md`](docs/CLIENT-ADAPTER-EVIDENCE.md).

## Přispívání a bezpečnost

- Postup a nároky na změny: [CONTRIBUTING.md](CONTRIBUTING.md)
- Hlášení zranitelností: [SECURITY.md](SECURITY.md) — nikdy ne přes veřejné issue
- Historie změn: [CHANGELOG.md](CHANGELOG.md)

## Licence

MIT — viz [LICENSE](LICENSE).
