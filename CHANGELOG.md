# Changelog

## 0.1.2 — 2026-07-31

- Runtime je připnutý na SDK revizi `88ecbf8a`, která rozlišuje
  chybějící upstream záznamy (HTTP 404/410) jako `not_found` místo obecného
  `invalid_input`. Platforma tak může bezpečně poradit, že zadaný záznam
  neexistuje, aniž by zveřejnila upstream odpověď.

## 0.1.1 — 2026-07-31

- Opravené pořadí nejnovějších objednávek, validace období, bezpečné zpracování
  vadných čísel, minimální projekce bez platebních logů a pravdivé zkrácení.
- Zpřesněná ochrana osobních údajů podle aktuálního schématu Dotykačka a stabilní
  pseudonymy pro sdílené týmové přihlašovací údaje.
- Bezpečnější validace `cloud_id`, klasifikace testu spojení a omezený timeout
  bez opakování dočasných upstream chyb; expirovaný access token hlášený stavem
  403 se obnoví jen jednou a trvalý 403 zůstane chybou oprávnění.
- Hraniční sonda agregace chápe dokumentovanou 404 prázdné stránky jako přesně
  500 dokladů, ale nepolyká rate limit, permission ani upstream chyby.
- Připnutý OpenMCP SDK 0.4.3 a opravený release gate pro přesný vendored snapshot.
