# Bezpečnostní politika

Nálezy **neposílejte do veřejných issue**. Pošlete je na
**security@openmcp.cz** s popisem dopadu a kroky k reprodukci. Do hlášení
nevkládejte refresh tokeny, `cloud_id` ani zákaznická data.

Podporovány jsou poslední dvě minor verze konektoru.

## Co nás zajímá nejvíc

- osobní údaj zákazníka nebo zaměstnance, který projde do modelu bez
  pseudonymizace — typicky pole, které chybí v `src/connector/pii_fields.py`;
- přístup k cloudu, který uživateli nepatří — delegovaný OAuth váže
  `refresh_token` i `cloud_id` na konkrétní propojení a nic jiného než
  uložený `cloud_id` se do cesty dostat nesmí;
- obejití read-only filtru: konektor nemá jediný zapisující nástroj;
- volání mimo `egress` allowlist;
- cokoli, co dostane access nebo refresh token do chybové zprávy pro model
  nebo do logu.

## Co bezpečnostní chyba není

- **Model se dá přemluvit textem z pokladního systému** (názvem produktu,
  poznámkou u účtenky). To je vlastnost LLM; konektor jen ohraničuje dopad.
- **Odvolaná autorizace vrací `credential_invalid`** a konektor přestane
  fungovat. To je správné chování — přístup skutečně zanikl.
- Chybějící `OPENMCP_PII_SALT` shodí start. To je záměr: tichý fallback na
  náhodný salt by rozbil stabilitu tokenů až po restartu.
