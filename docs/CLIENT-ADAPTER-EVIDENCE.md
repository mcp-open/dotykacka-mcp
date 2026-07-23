# Dotykačka client adapters — interní release evidence

> Datum ověření: 23. 7. 2026
> Stav: interní kandidát; žádný veřejný install nebo success claim
> Rozsah: Gemini CLI remote Extension a OpenAI/ChatGPT remote MCP handoff

## Source identity

| Vstup | Přesná identita |
|---|---|
| Connector branch | `codex/client-adapters-20260723` |
| Connector version | `dotykacka` `0.1.0` |
| OpenMCP SDK | `502f59facbd0cd738826cf02608344ecdbb9112b` |
| SDK archive SHA-256 | `55b043b540eb7e1a50feb6fa41689084bb7ec95b2e0964931ac732d8e55e2096` |
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

Výsledný deterministický render:

| Target | Cesta | SHA-256 |
|---|---|---|
| OpenAI operator handoff | `openai/submission.json` | `8f5084a1ecae1e197c1b0fbba34a7a43e575e4d92596aeb7f2d797cc5dd5505c` |
| Gemini remote Extension | `openmcp-dotykacka-0.1.0-gemini.zip` | `b2f12179b7774efcdacab086308f0da3a92a000af3616284f2edd83e5ba18052` |

Render obsahuje devět namespaced read-only tools. Nevytváří MCPB, lokální
executable, hook, working directory ani vložený review workspace/token.

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
