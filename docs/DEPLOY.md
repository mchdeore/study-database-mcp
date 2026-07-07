# Deploying Life Vault MCP (self-hosted, reachable from anywhere)

This is the operational runbook for Phase 2: run the vault on your home box and
reach it securely from any device. Design rationale lives in
`docs/vision/09-hosting-auth.md`; this file is the how-to.

## Security model (two separate layers)

1. **Secrets unlock (local):** `VAULT_MASTER_KEY` decrypts the secrets file
   (`.vault/secrets.enc`) on boot. Wrong key → the server refuses to start.
2. **API auth (remote):** every HTTP request must send
   `Authorization: Bearer <token>`. The token is stored only as a SHA-256 hash;
   rotating it invalidates the old one instantly.

Plus the network layer: **don't expose the port to the public internet** — put
the box on a Tailscale tailnet and let only your devices reach it.

## 1. Configure

Copy `.env.example` → `.env` and set at least:

```bash
POSTGRES_PASSWORD=<a long random password>
VAULT_MASTER_KEY=<a long random passphrase>   # encrypts secrets at rest
EMBEDDING_PROVIDER=local                        # free, runs on your hardware
```

Generate strong values, e.g.:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## 2. Start the stack

```bash
docker compose up -d           # Postgres+pgvector + the vault HTTP server
docker compose ps              # both healthy?
docker compose logs -f vault   # watch boot; it warns if no token is set yet
```

## 3. Set an API token (do this once)

```bash
docker compose exec vault python scripts/vault_admin.py --rotate-token
```

Copy the printed token now — it is shown only once (only its hash is stored).
Verify status:

```bash
docker compose exec vault python scripts/vault_admin.py --status
```

## 4. Index your vault

Put/keep your Markdown under `./vault` on the host (bind-mounted into the
container). Then:

```bash
docker compose exec vault python scripts/vault_index.py            # incremental
docker compose exec vault python scripts/vault_index.py --status   # row counts
```

## 5. Reach it from anywhere with Tailscale (recommended)

1. Install Tailscale on the home box and run `tailscale up`. Note its tailnet IP
   (e.g. `100.x.y.z`) or MagicDNS name.
2. Install Tailscale on your phone/laptop and join the same tailnet.
3. The compose file binds the port to `127.0.0.1` on the host. To serve it on the
   tailnet, either:
   - run `tailscale serve https / http://127.0.0.1:8765` (simplest, adds TLS), or
   - change the compose `ports` mapping to the tailnet IP, e.g.
     `"100.x.y.z:8765:8765"`.
4. Point your MCP client at `http://<tailnet-name>:8765/mcp` (or the
   `tailscale serve` HTTPS URL) with the bearer token.

Because the listener is only on the tailnet, there is **no public attack
surface** — no port forwarding, no certificates to manage manually.

### Alternative: public endpoint
If you can't use Tailscale, put Caddy in front for automatic TLS and forward to
`127.0.0.1:8765`, keeping the bearer token. This exposes a public surface — only
do it if necessary.

## 6. Connect an MCP client

Use the streamable-HTTP endpoint `http://<host>:8765/mcp` with header
`Authorization: Bearer <token>`. Any MCP client that supports HTTP transport and
custom headers works.

## Operations

```bash
# Rotate the token (old one dies immediately)
docker compose exec vault python scripts/vault_admin.py --rotate-token

# Tail the audit log (who/when/what, authorized or not)
docker compose exec vault python scripts/vault_admin.py --audit 50

# Rebuild the index from the vault (truth lives in the vault)
docker compose exec vault python scripts/vault_index.py --rebuild

# Back up (Phase 3 automates this): the vault folder is what matters
tar czf vault-backup-$(date +%F).tgz vault/
docker compose exec db pg_dump -U vault vault > index-$(date +%F).sql
```

> Backups currently target the local machine. Put the backup folder on a **second
> physical disk** — same-disk backups don't survive a disk failure.
