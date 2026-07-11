"""Vault connector sync CLI (build steps 4.6 + 4.1).

    python scripts/vault_sync.py --status              # per-source cursors (offline)
    python scripts/vault_sync.py --setup               # one-time Google OAuth consent
    python scripts/vault_sync.py --preview             # Gmail triage preview (writes nothing)
    python scripts/vault_sync.py --calendar            # sync Google Calendar (incremental)
    python scripts/vault_sync.py --gmail --full        # full resync Gmail
    python scripts/vault_sync.py --calendar --gmail    # sync both

The incremental runner + cursors + adapters are built and tested offline. Step 4.1
adds the live Google FETCH layer (OAuth + REST) this CLI now drives. `--status`
stays fully offline; `--calendar/--gmail` need Google credentials (and the
`connectors-google` extra) — if they're missing, the CLI says exactly what to do
instead of pretending to sync.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servers.vault.connectors import cursors  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync connectors into the vault (incremental).")
    parser.add_argument("--status", action="store_true", help="show per-source sync cursors")
    parser.add_argument("--setup", action="store_true", help="run the one-time Google OAuth consent")
    parser.add_argument("--preview", action="store_true",
                        help="read-only Gmail triage preview (classify, write nothing)")
    parser.add_argument("--calendar", action="store_true", help="sync Google Calendar")
    parser.add_argument("--gmail", action="store_true", help="sync Gmail")
    parser.add_argument("--full", action="store_true", help="full resync (ignore the saved cursor)")
    return parser.parse_args()


def _emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2, default=str))


# Run one connector sync and return its report (or an error dict).
def _sync(connector, *, full: bool) -> dict:
    from servers.vault.connectors.sync import run_sync

    try:
        return run_sync(connector, full=full)
    except Exception as error:  # noqa: BLE001 - surface any live-fetch failure as JSON
        return {"source": getattr(connector, "name", "?"), "error": f"{type(error).__name__}: {error}"}


def main() -> None:
    args = _parse_args()

    if args.setup:
        from servers.vault.connectors import google_auth

        try:
            _emit(google_auth.run_consent())
        except Exception as error:  # noqa: BLE001
            _emit({"error": f"{type(error).__name__}: {error}"})
        return

    if args.preview:
        from servers.vault.connectors import google_auth

        if not google_auth.status()["ready"]:
            _emit({"error": "Google is not fully authorized yet.", "status": google_auth.status()})
            return
        from servers.vault.connectors import google_fetch

        try:
            _emit({"gmail_preview": google_fetch.live_gmail_preview()})
        except Exception as error:  # noqa: BLE001
            _emit({"error": f"{type(error).__name__}: {error}"})
        return

    if args.calendar or args.gmail:
        from servers.vault.connectors import google_auth

        if not google_auth.status()["ready"]:
            from servers.vault import credentials as secrets_store

            _emit({
                "error": "Google is not fully authorized yet.",
                "status": google_auth.status(),
                "missing": secrets_store.missing_credentials(),
                "hint": "Store google_oauth_client_id + google_oauth_client_secret "
                        "(set_credential), then run: python scripts/vault_sync.py --setup",
            })
            return

        from servers.vault.connectors import google_fetch

        reports = []
        if args.calendar:
            reports.append(_sync(google_fetch.live_calendar_connector(), full=args.full))
        if args.gmail:
            reports.append(_sync(google_fetch.live_gmail_connector(), full=args.full))
        _emit({"synced": reports})
        return

    # Default action: report the sync checkpoints (offline, always available).
    _emit({"cursors": cursors.load_cursors()})


if __name__ == "__main__":
    main()
