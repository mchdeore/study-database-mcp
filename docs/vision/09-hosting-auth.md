# 09 — Hosting, auth & remote access

Requirement: runs on your home box, reachable from anywhere, password-protected,
without handing your life to the public internet.

## Transport (DECISION)

Serve the MCP over **Streamable HTTP** (the network MCP transport), not just
stdio. stdio stays available for a local client on the same machine. HTTP is what
lets a remote client connect.

## Two layers of protection

There is a difference between *unlocking the box's secrets* and *letting a remote
client in*. Keep them separate:

1. **Secrets unlock (local):** on boot, the server decrypts `.secrets/secrets.enc`
   using a master key (OS keychain / env / one-time prompt). This is the
   "first-time credentials it asks for" moment. See `07`.
2. **API auth (remote):** every MCP request must present a credential. Options,
   in order of recommendation:

## Remote access options

### DECISION (recommended): Tailscale + bearer token
- Put the home box on a **Tailscale** tailnet. Your phone/laptop join the same
  tailnet. The MCP listens only on the tailnet IP — **never exposed to the public
  internet**, no port-forwarding, no certificates to manage.
- Add a **bearer token** check on the MCP endpoint as a second factor, so even on
  the tailnet a client must present the secret.
- This is the most time-efficient *and* the most secure for a single owner.

### Alternative: public endpoint via reverse proxy
- A reverse proxy (**Caddy**, automatic Let's Encrypt TLS) terminates HTTPS and
  forwards to the MCP. Protect with either a strong **bearer token** or full
  **OAuth 2.1** (the MCP auth spec) if a client requires it.
- More moving parts and a public attack surface; only if you can't use Tailscale.

(Tailscale vs. public proxy is a fork — see `12-open-questions.md`. Default:
Tailscale.)

## Auth implementation notes

- Token stored hashed; compared in constant time. Rotatable via a CLI command.
- Owner-only tools (`set_credential`, `prune --delete`, `rebuild-index`) require
  the authenticated owner token; they're never anonymous even locally.
- Rate-limit and log every authenticated session (who/when/what tool) to an audit
  log you can read.

## Deployment (DECISION)

- **Docker Compose** on the home box: one service for Postgres+pgvector, one for
  the MCP server, optional one for the scheduler. `docker compose up -d` and it's
  running. (If you pick SQLite, the DB service disappears — even simpler.)
- A local embedding model (and optional local LLM via Ollama) can be additional
  services if you go fully local (see `10-cost.md`).
- Backups (DECISION): nightly `git commit` of the vault + a `pg_dump` written to a
  **local backup folder on the server machine**. The vault backup is the one that
  matters. *Caveat:* same-machine backups don't survive disk failure or theft —
  put the backup folder on a **second physical disk** in the box (recommended),
  and optionally copy to an external drive now and then.

## Resilience

- If the DB dies, search is down but **no data is lost** (truth is the vault);
  `rebuild-index` restores service.
- The server boots read-only-safe: if secrets can't decrypt, it refuses to start
  rather than running unauthenticated.
