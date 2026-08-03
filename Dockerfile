# Knap MCP server image.
#
# Defaults to streamable-http, which is what you want in a container; for stdio,
# override the command. Configure via env vars or an --env-file.
#
# The vault is NOT in the image. Mount it:
#
#   docker run -p 8000:8000 \
#     -v /path/to/vault:/vault:rw \
#     -e KNAP_VAULT_PATH=/vault \
#     knap-mcp:latest
#
# This image serves the vault with NO authentication, which is fine on a laptop
# and is not a deployment. The hosted, multi-tenant, Zitadel-authenticated build
# is knap-mcp-admin.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    KNAP_MCP_TRANSPORT=streamable-http \
    KNAP_MCP_HOST=0.0.0.0 \
    KNAP_MCP_PORT=8000

WORKDIR /app

# Dependencies first, so their layer caches independently of the source.
COPY pyproject.toml README.md ./
COPY knap_mcp ./knap_mcp
RUN pip install .

COPY scripts/healthcheck.py ./scripts/healthcheck.py

# Stamped by the build so a running container can report which commit it is.
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=${GIT_COMMIT}

# Non-root, and uid 10001 to match the sibling products. The vault is mounted
# from the host, so it has to be readable by this uid: that is the one thing
# worth knowing before the first `docker run` fails on permissions.
RUN useradd --system --uid 10001 knap
USER knap

EXPOSE 8000

# Liveness. The logic lives in scripts/healthcheck.py because the distinction it
# draws (a 4xx from /mcp is the app working) is easy to get backwards, and getting
# it backwards makes every rolling deploy roll itself back.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD ["python", "/app/scripts/healthcheck.py"]

CMD ["python", "-m", "knap_mcp"]
