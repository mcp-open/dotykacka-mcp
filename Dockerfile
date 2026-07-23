# dotykacka-mcp — hostovaný konektor nad openmcp_sdk. Build context připravuje
# platform/deploy/Makefile (analogicky build-connector-raynet): tar zabalí
# dotykacka-mcp + openmcp-sdk z repos/konektory a přejmenuje je na
# `dotykacka/` + `sdk/`. Digest je připnutý stejně jako release/canary image.
FROM python:3.13-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91

WORKDIR /app

# sdk nejprve (dotykacka-mcp na něj závisí v pyproject.toml).
COPY sdk ./sdk
COPY dotykacka ./dotykacka
RUN pip install --no-cache-dir --no-compile ./sdk ./dotykacka

# Non-root běh — stejné defaulty jako template/raynet Dockerfile.
RUN useradd --uid 10001 --system --no-create-home --shell /usr/sbin/nologin openmcp
USER 10001

# `python -m connector` volá run_connector("connector.yaml", mcp) s relativní
# cestou k manifestu — WORKDIR proto musí být adresář, který connector.yaml
# obsahuje (balík `connector` je nainstalovaný přes pip, tedy importovatelný
# nezávisle na cwd).
WORKDIR /app/dotykacka

EXPOSE 8000

# Kubernetes používá readiness/liveness probe z `runtime.health_path`.
ENTRYPOINT ["python", "-m", "connector"]
