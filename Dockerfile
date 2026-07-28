# dotykacka-mcp — hostovaný konektor nad openmcp_sdk. Build context připravuje
# platform/deploy/Makefile (analogicky build-connector-raynet): tar zabalí
# dotykacka-mcp + openmcp-sdk z repos/konektory a přejmenuje je na
# `dotykacka/` + `sdk/`. Digest je připnutý stejně jako release/canary image.
FROM python:3.13-alpine@sha256:399babc8b49529dabfd9c922f2b5eea81d611e4512e3ed250d75bd2e7683f4b0

WORKDIR /app

# sdk nejprve (dotykacka-mcp na něj závisí v pyproject.toml).
COPY sdk ./sdk
COPY dotykacka ./dotykacka
RUN pip install --no-cache-dir --no-compile --only-binary=:all: \
      --require-hashes -r ./dotykacka/release/runtime-requirements.lock \
    && pip install --no-cache-dir --no-compile --no-deps --no-build-isolation \
      ./sdk ./dotykacka \
    && pip check

# Non-root běh s pevným UID bez domovského adresáře a login shellu.
RUN addgroup -S -g 10001 openmcp \
    && adduser -S -D -H -u 10001 -G openmcp -s /sbin/nologin openmcp
USER 10001

# `python -m connector` volá run_connector("connector.yaml", mcp) s relativní
# cestou k manifestu — WORKDIR proto musí být adresář, který connector.yaml
# obsahuje (balík `connector` je nainstalovaný přes pip, tedy importovatelný
# nezávisle na cwd).
WORKDIR /app/dotykacka

EXPOSE 8000

# Kubernetes používá readiness/liveness probe z `runtime.health_path`.
ENTRYPOINT ["python", "-m", "connector"]
