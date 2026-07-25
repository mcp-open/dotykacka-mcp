# Dotykačka client adapters — interní release evidence

> Datum ověření: 25. 7. 2026
> Stav: interní kandidát; žádný veřejný install nebo success claim
> Rozsah: Gemini CLI remote Extension a OpenAI/ChatGPT remote MCP handoff

## Source identity

| Vstup | Přesná identita |
|---|---|
| Connector branch | `codex/client-adapters-20260723` |
| Connector implementation commit | `639545447a36a24d51f9d54c64a064396cf245c5` |
| Connector version | `dotykacka` `0.1.0` |
| OpenMCP SDK | `eedc35a7de7ca61c6823d89a5048f9eff98e78ff` |
| SDK archive SHA-256 | `602bc73eb75cac3fc98fb3231249e3e32ef4ec7f17bb91734504aefe6c52f19a` |
| Template baseline | `34918795eed58a0d60928f318450b748689ce34d` |
| Python | `3.13.9` |
| FastMCP | `3.4.4` |

SDK snapshot je uložený jako přesně pojmenovaný archiv, svázaný s `.sdk-ref`
a kontrolovaný přes `release/vendor/openmcp-sdk.sha256`. Materializace odmítá
jiný commit, změněný checksum, traversal, odkazy, speciální soubory, duplicity
i neprázdný cílový adresář.

## Connector a render gate

Lokální kontrola nad čerstvě materializovaným snapshotem:

```text
ruff check src tests                         PASS
mypy src                                    PASS (5 source files)
openmcp-sdk validate connector.yaml         PASS
pytest tests -q                             PASS (44)
render-adapters                             PASS
```

Samotný SDK snapshot prošel `380 passed, 1 skipped`; skip je záměrný
environment-dependent test. Connector nad přesně tímto snapshotem prošel
`44 passed`.

Výsledný deterministický render:

| Target | Cesta | SHA-256 |
|---|---|---|
| OpenAI operator handoff | `openai/submission.json` | `8f5084a1ecae1e197c1b0fbba34a7a43e575e4d92596aeb7f2d797cc5dd5505c` |
| Gemini remote Extension | `openmcp-dotykacka-0.1.0-gemini.zip` | `b2f12179b7774efcdacab086308f0da3a92a000af3616284f2edd83e5ba18052` |

Render obsahuje devět namespaced read-only tools. Nevytváří MCPB, lokální
executable, hook, working directory ani vložený review workspace/token.

## Hosted runtime a credential-backed E2E

Předchozí FastMCP 3 konfigurace používala výchozí stateful Streamable HTTP.
Session byla pouze v paměti procesu, zatímco Kubernetes Service rozdělovala
požadavky mezi dvě repliky bez affinity. Následný request proto mohl na jiném
podu skončit HTTP 404. SDK nyní explicitně používá `stateless_http=True`, což
odpovídá gateway kontraktu: gateway dělá nový upstream handshake pro každý
tool call.

Nasazený development runtime:

| Kontrola | Výsledek |
|---|---|
| Image | `localhost:30500/openmcp/mcp-dotykacka@sha256:9bb9d60a07977baed6d2ab262e3b5e0c80d1254507acc50dfb3be39097509310` |
| Base | `python:3.13-alpine@sha256:399babc8b49529dabfd9c922f2b5eea81d611e4512e3ed250d75bd2e7683f4b0` |
| Trivy image scan | 0 HIGH, 0 CRITICAL, 0 secrets |
| Dependency integrity | hash-locked install + `pip check` PASS |
| Rollout | 2/2 ready, 0 restartů |
| Health | `/healthz` PASS |
| Load-balanced MCP | 20× initialize → initialized → tools/list, oba pody zasaženy, 0 HTTP 404 |
| Tool contract | přesně 9 očekávaných read-only tools |
| Provider E2E | reálná publikovaná credential verze; `get_cloud_info` + `list_customers` PASS |
| Privacy assertion | 0 raw e-mailů, 0 OAuth/client secrets v odpovědích |

Při auditu byl zároveň odstraněn operátorský přepínač, který mohl vypnout
pseudonymizaci. Starší konfigurace s hodnotou `false` je nyní ignorována
fail-closed. OAuth klient, `cloud_id`, odvozený PII klíč i provenance se
snapshotují v jedné session, aby se během provider callu nemíchal kontext.

Tento důkaz potvrzuje hostovaný connector runtime a jeho skutečné provider
volání. Nenahrazuje ještě reálný Gemini CLI ani ChatGPT klientský OAuth/tool
E2E uvedený v otevřených branách níže.

GitHub Actions workflow-dispatch nad přesným implementation commitem:

| Důkaz | Výsledek |
|---|---|
| Hardening workflow run | `30136270788` — `success` nad `1080d32ee1491a7246c2d6ff9713103854c1800a` |
| Hardening test job | `89620657990` — `success` |
| Workflow run | `30018983977` — `success` |
| Test job | `89246421217` — `success` |
| Build job | `89246657441` — očekávaně `skipped` na feature branch |
| Deploy job | `89246658430` — očekávaně `skipped` na feature branch |

Feature větev tedy ověřila zdroj, locky, vendored SDK, manifest, render a testy,
ale nemohla vydat image ani změnit runtime. Build/deploy zůstávají vyhrazené
pro push do `main`.

## Gemini CLI exact-surface ověření

Ověřený host: oficiální `@google/gemini-cli` `0.52.0`.

V izolovaném `GEMINI_CLI_HOME` mimo uživatelský profil prošlo:

```text
gemini extensions validate <rendered-gemini>                  PASS
gemini extensions install <rendered-gemini> --consent
  --skip-settings                                             PASS
gemini extensions list                                        PASS
gemini extensions uninstall openmcp-dotykacka                 PASS
```

Host během instalace správně:

- zobrazil trust potvrzení;
- označil chybějící povinné nastavení `OpenMCP workspace URL`;
- registroval právě jeden remote MCP server `dotykacka`;
- načetl `GEMINI.md`;
- po uninstall odstranil instalovaný adresář.

`gemini-extension.json` používá:

- `httpUrl: ${OPENMCP_MCP_URL}`;
- `authProviderType: dynamic_discovery`;
- jediný externí OAuth scope `mcp`;
- workspace URL jako `sensitive: true`;
- přesný allowlist devíti tools.

To dokládá interní install lifecycle na Linuxu. Není to ještě credential-backed
OAuth/tool E2E ani compatibility důkaz pro všechny podporované OS/verze.

## OpenAI/ChatGPT handoff a live discovery

Handoff je záměrně:

```text
artifact_kind: operator_handoff
installable: false
authentication.scopes: [mcp]
```

Obsahuje explicitní brány pro konkrétní review endpoint, scan tools, externí
demo účet, web/mobile E2E, ověření organizace a domény, živou privacy policy a
zákaz breaking tool-schema změn.

Read-only kontrola veřejného development prostředí:

| Kontrola | Výsledek |
|---|---|
| Protected resource metadata pro review URL | HTTP 200; resource odpovídá přesné workspace URL |
| Authorization server metadata | HTTP 200; issuer `https://openmcp.cz` |
| Scope | přesně `mcp` v PRM i AS metadata |
| PKCE | pouze `S256` |
| Token auth | public client metoda `none` |
| Grants | authorization code + refresh token |
| Konkrétní review MCP endpoint | **HTTP 404 — blokuje submission** |
| Privacy policy `https://openmcp.cz/soukromi` | **HTTP 404 — blokuje submission** |

Původní SDK kandidát nesprávně vyžadoval OIDC `offline_access`, zatímco
schválený OpenMCP kontrakt i živý authorization server používají externě pouze
`mcp` a vlastní rotující refresh token. SDK, template i tento connector byly
opravené a znovu připnuté; platformový runtime se kvůli tomu neměnil.

## Co ještě musí projít před zveřejněním

### Gemini

1. reálná workspace URL s Dotykačka release;
2. dynamic discovery → browser OAuth → callback → token → MCP initialize;
3. `tools/list` a bezpečný read-only tool call;
4. update/uninstall/reinstall s reviewovaným Git tagem;
5. platform/version compatibility záznam v release registru.

### OpenAI/ChatGPT

1. provisionovaný konkrétní review workspace pouze s tímto release;
2. schválená a živá privacy policy i právní údaje;
3. ověřená OpenAI organizace a MCP doména;
4. demo účet bez MFA a bez dalšího nastavení;
5. scan živých tool schemas/annotations;
6. authorization code + PKCE `S256`, resource audience, refresh rotation a
   revoke E2E;
7. přesné test prompts/results v ChatGPT web i mobile;
8. schválení a až potom publish.

Do splnění těchto bodů nesmí web ani release registry změnit stav na
`verified`, zobrazit install CTA nebo tvrdit zákaznický success.

## Autoritativní externí požadavky

- OpenAI Apps SDK authentication:
  <https://developers.openai.com/apps-sdk/build/auth#mcp-authorization-spec-requirements>
- OpenAI app/plugin submission:
  <https://developers.openai.com/apps-sdk/deploy/submission>
- OpenAI plugin domain verification:
  <https://learn.chatgpt.com/docs/submit-plugins#domain-verification>
- Gemini CLI Extensions:
  <https://geminicli.com/docs/extensions/>
