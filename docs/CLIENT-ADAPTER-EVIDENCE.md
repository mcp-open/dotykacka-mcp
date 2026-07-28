# Dotykačka client adapters — interní release evidence

> Datum ověření: 25. 7. 2026
> Stav: interní kandidát; žádný veřejný install nebo success claim
> Rozsah: Gemini CLI remote Extension a OpenAI/ChatGPT remote MCP handoff

## Source identity

| Vstup | Přesná identita |
|---|---|
| Connector main merge | `e2da5858be61c2f3906f598bb00c7a7286cf82c8` |
| Current correction branch | `codex/openai-submission-contract-20260725` |
| Runtime implementation commit | `639545447a36a24d51f9d54c64a064396cf245c5` |
| Hostinger-only CI boundary | `df8af17f2ac34abc29b6d3d1d41ef07f6f95242d` |
| Distribution scan target fix | `a7df1f51ad62af7449c43d473f18560f46ec4285` |
| OpenAI handoff contract commit | `58780359b9e27f2900ed979f05f1fa6873928ebb` |
| Connector version | `dotykacka` `0.1.0` |
| OpenMCP SDK | `d77b2b1b22e83d64a4fdf900f73c51c578b5f736` |
| SDK archive SHA-256 | `794d66ff6d76bd5030e6ce6901ea885df2a4e4c005d29a226edb7f4cc198f316` |
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

Samotný SDK snapshot prošel `382 passed, 1 skipped`; skip je záměrný
environment-dependent test. Connector nad přesně tímto snapshotem v čistém
Python `3.13.9` prostředí s 81 hash-locked balíčky prošel Ruff, MyPy,
`pip check`, manifest validation, render a `44 passed`.

Výsledný deterministický render:

| Target | Cesta | SHA-256 |
|---|---|---|
| OpenAI operator handoff | `openai/submission.json` | `6e1007e9a30cbc5ff61e3bd23b7aeea7c68575fb87b16a9ec8c2f30e0bb40fe8` |
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
ale nemohla vydat image ani změnit runtime. Následný CI hardening odstranil i
budoucí self-hosted build a cross-repository deploy dispatch z `main`: GitHub
workflow pouze testuje. Hostinger development build, scan, deploy a hosted E2E
smí proběhnout jen přes schválenou přímou SSH Manager release cestu.

Sloučení do `main` proběhlo přes dva reviewované pull requesty:

| Důkaz | Výsledek |
|---|---|
| PR #1 — runtime a adaptéry | `merged`; merge `db5fa538b2ca24a6b1240d7afc8c83c6ea52dcae` |
| PR #1 CI | run `30139212926`, job `89629141581` — `success` |
| PR #2 — rozpoznatelné Trivy dependency targety | `merged`; merge `e2da5858be61c2f3906f598bb00c7a7286cf82c8` |
| PR #2 CI | run `30139360633` — `success`, `44 passed` |
| Main CI | run `30139395398` — `success`, job `89629613271` |

První main distribution run `30139263385` byl správně odmítnut, protože Trivy
neidentifikovalo dependency soubory pod nestandardními názvy. Release gate se
tedy nezměkčila: build vstupy se přesunuly na
`build-inputs/runtime/requirements.txt` a
`build-inputs/release/requirements.txt`, aby je scanner analyzoval jako Python
dependency targety.

## Přesný podepsaný distribution kandidát

Main-only distribution run `30139395399` nad commitem
`e2da5858be61c2f3906f598bb00c7a7286cf82c8` skončil `success`:

| Kontrola | Výsledek |
|---|---|
| Build a scan job | `89629613214` — `success` |
| Sign a verify job | `89629769943` — `success` |
| Source quality | Ruff, mypy, manifest, render a `44 passed` |
| Dependency scan targety | release i runtime `requirements.txt` rozpoznány jako `pip` |
| Trivy | 0 zranitelností a 0 secrets v obou targetech |
| Provenance | commit, repository a main workflow identity svázány a ověřeny |
| Podpis | artifact i provenance mají ověřený keyless Cosign bundle |
| Release gate | `eligible` pro interního kandidáta |

Stažený GitHub Actions artifact
`verified-distribution-e2da5858be61c2f3906f598bb00c7a7286cf82c8`
byl znovu porovnán s release evidence. SHA-256 sedí přesně:

| Target | SHA-256 |
|---|---|
| Gemini ZIP | `b2f12179b7774efcdacab086308f0da3a92a000af3616284f2edd83e5ba18052` |
| OpenAI operator handoff | `8f5084a1ecae1e197c1b0fbba34a7a43e575e4d92596aeb7f2d797cc5dd5505c` |

GitHub repository má zapnuté immutable releases. Přesný Gemini artifact,
checksums, SBOM, Trivy report, provenance a Sigstore bundles jsou přiložené k
draft/prerelease `v0.1.0-adapters-rc.1`, který cílí na uvedený main commit.
Draft je záměrně interní a ještě není immutable publikovaný release ani
zákaznický install. OpenAI handoff není vydaný jako veřejný asset.

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
submission_state: blocked
authentication.scopes: [mcp]
```

Schéma v2 už nevyrábí fiktivní konkrétní review URL ani nepublikovanou privacy
URL. `mcp_server_url` a protected-resource metadata jsou `null`, privacy pole
je vynechané a oba gate jsou explicitně `missing`. Template zůstává pouze
interním návrhem. Všech devět nástrojů má pravdivé anotace
`readOnlyHint=true`, `destructiveHint=false`, `openWorldHint=false`.

Obsahuje explicitní brány pro provisionovaný konkrétní endpoint, scan tools,
externí demo účet, ChatGPT/Codex surface E2E, ověření organizace a domény,
živou privacy policy a zákaz breaking tool-schema změn.

Read-only kontrola veřejného development prostředí:

| Kontrola | Výsledek |
|---|---|
| Protected resource metadata template | deterministický exact workspace template; nejde o review důkaz |
| Authorization server metadata | HTTP 200; issuer `https://openmcp.cz` |
| Scope | přesně `mcp` v PRM i AS metadata |
| PKCE | pouze `S256` |
| Token auth | public client metoda `none` |
| Grants | authorization code + refresh token |
| Konkrétní review MCP endpoint | **není provisionovaný; gate `missing`, žádný placeholder** |
| Privacy policy | **není právně schválená/publikovaná; gate `missing`, žádný 404 odkaz** |

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

- OpenAI plugin submission:
  <https://developers.openai.com/plugins/deploy/submission>
- OpenAI MCP server review:
  <https://developers.openai.com/plugins/deploy/app-review>
- OpenAI remote MCP:
  <https://developers.openai.com/api/docs/guides/tools-connectors-mcp>
- Gemini CLI Extensions:
  <https://geminicli.com/docs/extensions/>
