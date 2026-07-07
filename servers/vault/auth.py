"""Bearer-token authentication for the HTTP server (build steps 2.2 / 2.7).

The owner sets a bearer token (stored only as its SHA-256 hash, never in the
clear). Every HTTP request must send `Authorization: Bearer <token>`; the
middleware hashes the presented token and compares it to the stored hash in
constant time. Rotating the token invalidates the old one immediately.

This module has no Starlette dependency in its core (hash/verify/rotate are pure)
so it is fully unit-testable offline; the ASGI middleware at the bottom wires it
into the request path.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets as pysecrets
from typing import Iterable, Optional

from . import audit
from . import credentials as secrets_store

# Credential key under which the token's SHA-256 hash is stored.
TOKEN_HASH_CREDENTIAL = "api_bearer_token_sha256"

# Number of random bytes in a generated token (~43 url-safe chars).
_TOKEN_BYTES = 32


# Generate a new random bearer token (url-safe). Shown to the owner once; only its
# hash is persisted.
def generate_token() -> str:
    return pysecrets.token_urlsafe(_TOKEN_BYTES)


# SHA-256 hex digest of a token. Storing/comparing the hash means the raw token is
# never written to disk.
def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# Store the hash of a new token (owner-only). Returns the hash for confirmation.
def set_token(token: str, owner: bool = True) -> str:
    digest = token_hash(token)
    secrets_store.set_credential(TOKEN_HASH_CREDENTIAL, digest, owner=owner)
    return digest


# Generate, store, and RETURN a fresh token (owner-only). The old token stops
# working immediately because its hash is overwritten. Caller must show the
# returned token to the owner once -- it can't be recovered later.
def rotate_token(owner: bool = True) -> str:
    token = generate_token()
    set_token(token, owner=owner)
    return token


# The currently configured token hash, or None if no token has been set yet.
def configured_token_hash() -> Optional[str]:
    return secrets_store.get_credential(TOKEN_HASH_CREDENTIAL)


# True when the bearer token in an Authorization header value is valid. Denies by
# default: no header, wrong scheme, or no token configured all return False.
# Uses constant-time comparison to avoid timing leaks.
def verify_authorization(authorization_header: Optional[str]) -> bool:
    expected = configured_token_hash()
    if not expected or not authorization_header:
        return False

    parts = authorization_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False

    presented = token_hash(parts[1].strip())
    return hmac.compare_digest(presented, expected)


class BearerAuthMiddleware:
    """ASGI middleware that gates every HTTP request on a valid bearer token.

    Unauthorized requests get a 401 JSON response and an audit entry; authorized
    requests are audited and passed through to the wrapped app. Non-HTTP scopes
    (e.g. lifespan) pass straight through.
    """

    # Wrap a downstream ASGI app; `exempt_paths` skip auth (e.g. a health probe).
    def __init__(self, app, exempt_paths: Iterable[str] = ()):
        self.app = app
        self.exempt_paths = set(exempt_paths)

    # ASGI entrypoint: authenticate HTTP requests, then delegate or reject.
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in self.exempt_paths:
            await self.app(scope, receive, send)
            return

        authorized = verify_authorization(self._authorization_header(scope))
        audit.record({
            "client": self._client_address(scope),
            "method": scope.get("method", ""),
            "path": path,
            "authorized": authorized,
        })

        if not authorized:
            await self._reject(send)
            return
        await self.app(scope, receive, send)

    # Pull the Authorization header value out of the raw ASGI scope.
    def _authorization_header(self, scope) -> Optional[str]:
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                return value.decode("latin-1")
        return None

    # Best-effort client IP for the audit log.
    def _client_address(self, scope) -> str:
        client = scope.get("client")
        return client[0] if client else "unknown"

    # Send a 401 JSON response.
    async def _reject(self, send) -> None:
        body = json.dumps({
            "error": "unauthorized",
            "hint": "send 'Authorization: Bearer <token>' with a valid vault token.",
        }).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer")],
        })
        await send({"type": "http.response.body", "body": body})
