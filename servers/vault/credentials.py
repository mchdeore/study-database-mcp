"""Credential store + owner-guarded credential tools (build step 1.6).

NOTE: this module is intentionally named `credentials`, NOT `secrets`, so it can
never shadow Python's stdlib `secrets` module when the server is run as a script
(running a file puts its own directory on sys.path, and a local `secrets.py`
would then break any library doing `import secrets`).

Phase 1 stored credentials as plain JSON under `.vault/`. Phase 2 encrypts them
at rest (secrets.enc) when VAULT_MASTER_KEY is set; the function surface is the
same so nothing downstream changes.

"Owner-guarded": write/read is only allowed for the authenticated owner. Callers
pass an `owner` flag; the HTTP layer sets it from the verified token. Local CLI
use is the owner by default.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from . import crypto
from .config import paths

# Environment variable holding the master password used to encrypt the secrets
# file at rest. When set, secrets live in secrets.enc; when unset, the Phase 1
# plaintext secrets.json is used (with a warning) so nothing breaks for local dev.
MASTER_KEY_ENV = "VAULT_MASTER_KEY"

# The credentials the system will eventually want, with human descriptions so a
# first-run wizard / the assistant can ask for exactly what's missing. Expand as
# connectors land (Phase 4/5).
CREDENTIAL_REGISTRY: Dict[str, str] = {
    "api_bearer_token": "Bearer token clients must send to reach the vault over HTTP (Phase 2).",
    "google_oauth_client_id": "Google OAuth client id (Calendar + Gmail ingestion).",
    "google_oauth_client_secret": "Google OAuth client secret.",
    "google_oauth_refresh_token": "Google OAuth refresh token (obtained by the setup wizard).",
    "deepseek_api_key": "DeepSeek API key: the librarian's EXTRACTION model (bulk per-chunk entity/relation extraction). Phase 5.",
    "moonshot_api_key": "Moonshot (Kimi) API key: the librarian's AGENTIC model (tool orchestration, dedup/regroup, context-packing). Phase 5.",
}


# Raise if the caller is not the owner. Centralizes the guard so every secret
# operation enforces it the same way, with an actionable message.
def require_owner(owner: bool) -> None:
    if not owner:
        raise PermissionError(
            "credential operations are owner-only. "
            "Authenticate as the vault owner (Phase 2 bearer token) and retry."
        )


# The master password if encryption is enabled, else None (plaintext mode).
def _master_key() -> Optional[str]:
    key = os.environ.get(MASTER_KEY_ENV)
    return key if key else None


# Path to the plaintext store (Phase 1 / no master key).
def _plaintext_path():
    return paths()["system"] / "secrets.json"


# Path to the encrypted store (used when a master key is configured).
def _encrypted_path():
    return paths()["system"] / "secrets.enc"


# Read the secrets dict, decrypting when a master key is set. Empty dict if no
# store exists yet. Raises a clear error on a corrupt file or wrong master key.
def _load() -> Dict[str, str]:
    master_key = _master_key()
    if master_key:
        return _load_encrypted(master_key)
    return _load_plaintext()


# Decrypt the encrypted store with the master key.
def _load_encrypted(master_key: str) -> Dict[str, str]:
    path = _encrypted_path()
    if not path.exists():
        return {}
    blob = json.loads(path.read_text(encoding="utf-8"))
    return crypto.decrypt_json(blob, master_key)


# Read the plaintext store.
def _load_plaintext() -> Dict[str, str]:
    path = _plaintext_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(
            f"secrets file is corrupt ({path}): {error}. "
            "Fix or delete the file, then re-enter credentials."
        ) from error


# Write the secrets dict back (encrypted when a master key is set), with
# owner-only file permissions so other local users can't read tokens.
def _save(stored: Dict[str, str]) -> None:
    master_key = _master_key()
    path = _encrypted_path() if master_key else _plaintext_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if master_key:
        blob = crypto.encrypt_json(stored, master_key)
        path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
        _remove_plaintext_after_migration()
    else:
        path.write_text(json.dumps(stored, indent=2, sort_keys=True), encoding="utf-8")

    _restrict_permissions(path)


# Once secrets are encrypted, delete any leftover plaintext file so the cleartext
# copy doesn't linger after migrating to encryption.
def _remove_plaintext_after_migration() -> None:
    plaintext = _plaintext_path()
    if plaintext.exists():
        plaintext.unlink()


# Best-effort owner-only permissions (POSIX). No-op where unsupported.
def _restrict_permissions(path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# Verify the secrets store can be opened on boot. With a master key set, this
# attempts a decrypt and RAISES on the wrong key (so the server refuses to start
# rather than run without its secrets). Returns a small status dict.
def unlock() -> Dict[str, Any]:
    master_key = _master_key()
    if not master_key:
        return {"encrypted": False,
                "warning": f"{MASTER_KEY_ENV} not set; secrets are stored in plaintext. "
                           "Set a master key before exposing the server."}
    _load()  # raises ValueError on wrong key / tampering
    return {"encrypted": True, "unlocked": True}


# Store one credential. Owner-only. Empty names/values are rejected with a clear
# message rather than silently stored.
def set_credential(name: str, value: str, owner: bool = True) -> Dict[str, Any]:
    require_owner(owner)
    if not name or not name.strip():
        raise ValueError("credential name is empty. Pass a non-empty name, e.g. 'deepseek_api_key'.")
    if value is None or value == "":
        raise ValueError(f"value for '{name}' is empty. Pass the actual secret value.")

    stored = _load()
    stored[name.strip()] = value
    _save(stored)
    return {"ok": True, "name": name.strip(), "known": name.strip() in CREDENTIAL_REGISTRY}


# Fetch one credential value, preferring the stored file and falling back to an
# environment variable (NAME upper-cased) for ops who pass secrets via env.
def get_credential(name: str) -> Optional[str]:
    stored = _load()
    if name in stored and stored[name]:
        return stored[name]
    return os.environ.get(name.upper()) or None


# True when a credential has a usable value (file or env).
def has_credential(name: str) -> bool:
    return bool(get_credential(name))


# List the registry credentials that are not yet set, with their descriptions, so
# the assistant can ask the owner for exactly what's missing.
def missing_credentials() -> List[Dict[str, str]]:
    return [
        {"name": name, "description": description}
        for name, description in CREDENTIAL_REGISTRY.items()
        if not has_credential(name)
    ]
