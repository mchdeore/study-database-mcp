"""Authenticated symmetric encryption for secrets at rest (build step 2.4).

Used to encrypt the secrets file so backups / a stolen disk don't leak tokens.
Key derivation is scrypt (stdlib hashlib) over a master password; the cipher is
Fernet (AES-128-CBC + HMAC) from the `cryptography` package. A random salt is
stored alongside the ciphertext so the same password yields a fresh key per file.

If `cryptography` isn't installed, importing the encrypt/decrypt functions raises
a clear, actionable error -- the rest of the system keeps working with the
plaintext secrets store (Phase 1 behavior) until you opt into encryption.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any, Dict

# scrypt cost parameters. These are deliberately strong for an interactive
# unlock; tune down only if boot time on weak hardware is a problem.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LENGTH_BYTES = 32
_SALT_LENGTH_BYTES = 16


# Import Fernet lazily so the module imports even without `cryptography`. Raises a
# clear install hint if the feature is actually used without the dependency.
def _fernet_class():
    try:
        from cryptography.fernet import Fernet

        return Fernet
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(
            "secrets encryption needs the 'cryptography' package. "
            "Install the crypto extra: pip install -e \".[crypto]\""
        ) from error


# Derive a 32-byte Fernet key from the master password and salt via scrypt, then
# url-safe base64 encode it (the form Fernet expects).
def _derive_key(master_password: str, salt: bytes) -> bytes:
    raw_key = hashlib.scrypt(
        master_password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_LENGTH_BYTES,
    )
    return base64.urlsafe_b64encode(raw_key)


# Encrypt a JSON-serializable object with the master password. Returns a dict
# (salt + ciphertext, both base64 text) suitable for writing to a file.
def encrypt_json(obj: Any, master_password: str) -> Dict[str, str]:
    fernet = _fernet_class()
    salt = os.urandom(_SALT_LENGTH_BYTES)
    key = _derive_key(master_password, salt)
    plaintext = json.dumps(obj, sort_keys=True).encode("utf-8")
    token = fernet(key).encrypt(plaintext)
    return {
        "salt": base64.b64encode(salt).decode("ascii"),
        "ciphertext": token.decode("ascii"),
    }


# Decrypt a blob produced by encrypt_json. Raises a clear error when the password
# is wrong or the file is tampered with (so a bad unlock is unambiguous).
def decrypt_json(blob: Dict[str, str], master_password: str) -> Any:
    fernet = _fernet_class()
    try:
        salt = base64.b64decode(blob["salt"])
        key = _derive_key(master_password, salt)
        plaintext = fernet(key).decrypt(blob["ciphertext"].encode("ascii"))
    except KeyError as error:
        raise ValueError("encrypted secrets file is malformed (missing salt/ciphertext).") from error
    except Exception as error:  # noqa: BLE001  -- InvalidToken etc.
        raise ValueError(
            "could not decrypt secrets: wrong master key or the file was modified. "
            "Set the correct VAULT_MASTER_KEY and retry."
        ) from error
    return json.loads(plaintext.decode("utf-8"))
