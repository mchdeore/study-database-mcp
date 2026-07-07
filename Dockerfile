# Life Vault MCP server image (Phase 2 deploy).
# Runs the authenticated Streamable HTTP vault server. Pair with the Postgres +
# pgvector service in docker-compose.yml.

FROM python:3.12-slim

# System deps: build tools for any wheels that need them. Kept minimal.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (better layer caching) using the project metadata.
COPY pyproject.toml README.md ./
COPY servers ./servers
COPY scripts ./scripts

# Real deployment extras: knowledge core + local embeddings + HTTP serving +
# Postgres backend + PDF ingestion. Local embeddings make steady-state cost $0.
RUN pip install --no-cache-dir -e ".[knowledge,embeddings-local,serve,store-postgres,pdf-pymupdf]"

# The vault (source of truth) is mounted as a volume at runtime; create the dir.
RUN mkdir -p /app/vault

# Bind to all interfaces inside the container; Tailscale / the compose network
# controls who can actually reach it (see docs/DEPLOY.md).
EXPOSE 8765
ENV VAULT_DIR=/app/vault \
    VAULT_HOST=0.0.0.0 \
    VAULT_PORT=8765

CMD ["python", "servers/vault/server.py", "--http"]
