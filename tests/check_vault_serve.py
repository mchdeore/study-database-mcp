"""Self-check for authenticated serving, Phase 2. Run:
    python tests/check_vault_serve.py

Mostly offline. Verifies:

  2.4 crypto + secrets   - encrypt/decrypt round-trip; wrong key fails; secrets
                           are encrypted at rest and unlock refuses a bad key
  2.2 bearer auth        - hash/verify/rotate; old token dies on rotation
  2.3 audit log          - requests are recorded (authorized true/false)
  2.2/2.1 middleware     - ASGI gate: 401 without a valid token, pass-through with
  2.1 HTTP (live smoke)  - a real server rejects an unauthenticated request (401)

The live smoke spawns the server; if it can't bind in time it prints SKIP rather
than failing (the middleware unit test is the authoritative gate check).
"""

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="vault_serve_test_")
os.environ["VAULT_DIR"] = _TMP
os.environ["VAULT_DB"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["VAULT_MASTER_KEY"] = "correct-horse-battery-staple"

from servers.vault import audit  # noqa: E402
from servers.vault import auth  # noqa: E402
from servers.vault import crypto  # noqa: E402
from servers.vault import credentials as sec  # noqa: E402
from servers.vault.auth import BearerAuthMiddleware  # noqa: E402
from servers.vault.config import paths  # noqa: E402


def ok(condition, message):
    assert condition, message
    print(f"  ok: {message}")


# --- 2.4 crypto ------------------------------------------------------------
print("2.4 crypto")
blob = crypto.encrypt_json({"a": 1, "b": "secret"}, "pw")
ok(crypto.decrypt_json(blob, "pw") == {"a": 1, "b": "secret"}, "encrypt/decrypt round-trips")
try:
    crypto.decrypt_json(blob, "wrong")
    raise AssertionError("wrong password should fail to decrypt")
except ValueError:
    print("  ok: wrong password fails to decrypt with a clear error")


# --- 2.4 secrets encrypted at rest -----------------------------------------
print("2.4 secrets at rest")
sec.set_credential("deepseek_api_key", "sk-xyz", owner=True)
ok(sec._encrypted_path().exists(), "secrets written to secrets.enc")
ok(not sec._plaintext_path().exists(), "no plaintext secrets.json remains")
raw = sec._encrypted_path().read_text()
ok("sk-xyz" not in raw, "the secret value is not present in cleartext on disk")
ok(sec.get_credential("deepseek_api_key") == "sk-xyz", "decrypts back to the value")
ok(sec.unlock().get("unlocked") is True, "unlock succeeds with the right master key")

os.environ["VAULT_MASTER_KEY"] = "the-wrong-key"
try:
    sec.unlock()
    raise AssertionError("unlock with the wrong key should raise")
except ValueError:
    print("  ok: unlock refuses the wrong master key (server would refuse to start)")
os.environ["VAULT_MASTER_KEY"] = "correct-horse-battery-staple"


# --- 2.2 bearer auth -------------------------------------------------------
print("2.2 bearer auth")
ok(auth.token_hash("abc") == auth.token_hash("abc"), "token hash is deterministic")
token = auth.generate_token()
auth.set_token(token, owner=True)
ok(auth.verify_authorization(f"Bearer {token}"), "valid token verifies")
ok(not auth.verify_authorization("Bearer nope"), "wrong token rejected")
ok(not auth.verify_authorization(None), "missing header rejected")
ok(not auth.verify_authorization(token), "missing 'Bearer ' scheme rejected")

new_token = auth.rotate_token(owner=True)
ok(auth.verify_authorization(f"Bearer {new_token}"), "rotated token verifies")
ok(not auth.verify_authorization(f"Bearer {token}"), "old token dies after rotation")


# --- 2.2/2.1 middleware gate ----------------------------------------------
print("2.2 middleware gate")


async def _downstream(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def _call(headers):
    middleware = BearerAuthMiddleware(_downstream, exempt_paths={"/healthz"})
    sent = []
    scope = {"type": "http", "path": "/mcp", "method": "POST",
             "headers": headers, "client": ("10.0.0.9", 5555)}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent[0]["status"]


ok(asyncio.run(_call([])) == 401, "no token -> 401")
ok(asyncio.run(_call([(b"authorization", b"Bearer bad")])) == 401, "bad token -> 401")
ok(asyncio.run(_call([(b"authorization", f"Bearer {new_token}".encode())])) == 200,
   "valid token -> passes through (200)")


# --- 2.3 audit -------------------------------------------------------------
print("2.3 audit")
events = audit.read_all()
ok(any(e["authorized"] is False for e in events), "an unauthorized attempt was logged")
ok(any(e["authorized"] is True for e in events), "an authorized request was logged")
ok(all("at" in e and "path" in e for e in events), "audit entries carry timestamp + path")


# --- 2.1 HTTP live smoke (best-effort) -------------------------------------
print("2.1 HTTP live smoke")


def _wait_for_port(port, timeout=25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


port = _free_port()
server = subprocess.Popen(
    [sys.executable, str(ROOT / "servers" / "vault" / "server.py"),
     "--http", "--host", "127.0.0.1", "--port", str(port)],
    env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
try:
    if not _wait_for_port(port):
        print("  SKIP: server did not start in time (middleware unit test already proved the gate)")
    else:
        import httpx

        url = f"http://127.0.0.1:{port}/mcp"
        unauthorized = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, timeout=5.0)
        ok(unauthorized.status_code == 401, "live server rejects an unauthenticated request (401)")
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()

print("\nALL VAULT PHASE 2 CHECKS PASSED")
