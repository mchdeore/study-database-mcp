"""Vault admin CLI: tokens, secrets unlock, audit (build steps 2.4 / 2.7).

Run locally as the owner.

    python scripts/vault_admin.py --rotate-token     # new token (printed ONCE)
    python scripts/vault_admin.py --set-token TOKEN   # store a specific token
    python scripts/vault_admin.py --check             # verify secrets unlock
    python scripts/vault_admin.py --status            # token/encryption status
    python scripts/vault_admin.py --audit 20          # last N audit lines

With VAULT_MASTER_KEY set, secrets (including the token hash) are encrypted at
rest; without it they are plaintext (a warning is shown).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servers.vault import audit  # noqa: E402
from servers.vault import auth  # noqa: E402
from servers.vault import credentials as secrets_store  # noqa: E402


# Parse the admin CLI flags.
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vault admin: tokens, secrets, audit.")
    parser.add_argument("--rotate-token", action="store_true", help="generate + store a new token")
    parser.add_argument("--set-token", metavar="TOKEN", help="store a specific bearer token")
    parser.add_argument("--check", action="store_true", help="verify secrets can be unlocked")
    parser.add_argument("--status", action="store_true", help="show token + encryption status")
    parser.add_argument("--audit", type=int, metavar="N", help="print the last N audit entries")
    return parser.parse_args()


# Generate and print a fresh token. It is shown once and cannot be recovered.
def _rotate_token() -> None:
    token = auth.rotate_token(owner=True)
    print("New bearer token (store it now -- it will NOT be shown again):\n")
    print(f"    {token}\n")
    print("Clients send it as:  Authorization: Bearer <token>")


# Store a specific token's hash (the raw token is never written).
def _set_token(token: str) -> None:
    auth.set_token(token, owner=True)
    print("Token stored (hash only). The old token, if any, no longer works.")


# Verify the secrets store opens with the current master key (or warn about
# plaintext mode). Exits non-zero on a failed unlock so scripts can detect it.
def _check() -> None:
    try:
        status = secrets_store.unlock()
    except ValueError as error:
        print(f"unlock FAILED: {error}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(status, indent=2))


# Print token + encryption status.
def _status() -> None:
    print(json.dumps({
        "token_configured": auth.configured_token_hash() is not None,
        "secrets": secrets_store.unlock(),
        "missing_credentials": [c["name"] for c in secrets_store.missing_credentials()],
    }, indent=2))


# Print the last N audit entries.
def _audit(count: int) -> None:
    for entry in audit.read_all()[-count:]:
        print(json.dumps(entry, sort_keys=True))


# Dispatch to the requested action.
def main() -> None:
    args = _parse_args()
    if args.rotate_token:
        _rotate_token()
    elif args.set_token:
        _set_token(args.set_token)
    elif args.check:
        _check()
    elif args.audit is not None:
        _audit(args.audit)
    else:
        _status()


if __name__ == "__main__":
    main()
