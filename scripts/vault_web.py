"""Run the Life Vault web dashboard (build step 9.2).

    python scripts/vault_web.py                 # http://127.0.0.1:8760
    python scripts/vault_web.py --port 9000
    VAULT_DIR=~/life-vault python scripts/vault_web.py

Read-only dashboard over your vault (upcoming items, search, notes, finances, and
the build tracker). It is UNAUTHENTICATED — it binds to 127.0.0.1 by default and
should stay local; reach it from other devices over Tailscale, not by binding wide.
Needs the `serve` extra (Starlette/uvicorn): pip install -e ".[serve]".
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servers.vault.web import run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Life Vault web dashboard (read-only).")
    parser.add_argument("--host", default=os.environ.get("VAULT_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("VAULT_WEB_PORT", "8760")))
    args = parser.parse_args()
    run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
