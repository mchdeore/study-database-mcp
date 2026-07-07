"""Google OAuth for the Calendar + Gmail connectors (build step 4.1 — auth half).

This is the one *network seam* Phase 4 was waiting on. Everything around it —
the credential store, both adapters, the incremental runner + cursors — is built
and offline-tested. This module does exactly two jobs:

  1. `run_consent()` — the one-time interactive OAuth "installed app" flow. Opens
     the Google consent screen in a browser, catches the redirect on a localhost
     port, exchanges the code, and stores the resulting **refresh token**
     (encrypted, via the credential store). Owner-only.
  2. `get_access_token()` — mints a short-lived access token from the stored
     refresh token, refreshing as needed. This is what the live fetch layer
     (`google_fetch.py`) calls before each API request.

Design choices (ponytail):
  - **Least privilege:** read-only Calendar + Gmail scopes only. We never request
    write access, so a leaked token can't modify your Google data.
  - **Don't hand-roll the security-critical dance.** The consent flow (local
    redirect server, PKCE, code exchange) and skew-aware token refresh come from
    Google's own `google-auth-oauthlib` / `google-auth`. We only add ONE dependency
    name; it pulls `requests`, which the thin REST fetch layer reuses.
  - **Lazy imports.** The google libraries are imported *inside* the functions that
    need them, so this module (and the offline test suite) imports fine without the
    `connectors-google` extra installed. You only need the extra to actually run
    consent / refresh.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .. import credentials as secrets_store

# --- Least-privilege, read-only scopes -------------------------------------
# calendar.readonly  -> list events (never create/modify/delete)
# gmail.readonly      -> read messages/labels (never send/modify/delete)
# If you change these, existing consent must be re-run (run_consent) because the
# granted scopes are baked into the refresh token.
SCOPES: List[str] = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]

# Google's standard OAuth endpoints for an installed/desktop app.
_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
_TOKEN_URI = "https://oauth2.googleapis.com/token"

# Credential-store keys (already registered in credentials.CREDENTIAL_REGISTRY).
_CLIENT_ID = "google_oauth_client_id"
_CLIENT_SECRET = "google_oauth_client_secret"
_REFRESH_TOKEN = "google_oauth_refresh_token"


# Assemble the "installed app" client config from the stored client id/secret, in
# the shape google-auth-oauthlib expects (so we never need a client_secret.json on
# disk). Raises an actionable error naming exactly which credential is missing.
def _client_config() -> Dict[str, Any]:
    client_id = secrets_store.get_credential(_CLIENT_ID)
    client_secret = secrets_store.get_credential(_CLIENT_SECRET)
    missing = [name for name, value in ((_CLIENT_ID, client_id), (_CLIENT_SECRET, client_secret))
               if not value]
    if missing:
        raise RuntimeError(
            "Google OAuth client credentials are not set: " + ", ".join(missing) + ". "
            "Create an OAuth 'Desktop app' client in Google Cloud Console "
            "(APIs & Services -> Credentials), then store them with the set_credential "
            "tool (or scripts/vault_admin.py). See docs for the exact click-path."
        )
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": _AUTH_URI,
            "token_uri": _TOKEN_URI,
            "redirect_uris": ["http://localhost"],
        }
    }


# True once a refresh token has been stored (i.e. consent has been completed).
def has_consent() -> bool:
    return secrets_store.has_credential(_REFRESH_TOKEN)


# Run the one-time interactive consent flow and persist the refresh token.
# Owner-only. Opens a browser to Google's consent screen and catches the redirect
# on an ephemeral localhost port. Requires the `connectors-google` extra.
#
# `access_type=offline` + `prompt=consent` are what guarantee Google returns a
# refresh token (without them a re-consent can come back with none). Returns a
# small status dict; the raw tokens are never returned, only stored.
def run_consent(*, owner: bool = True, open_browser: bool = True, port: int = 0) -> Dict[str, Any]:
    secrets_store.require_owner(owner)
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as error:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "Google consent needs the 'connectors-google' extra. "
            "Install it: pip install -e \".[connectors-google]\""
        ) from error

    flow = InstalledAppFlow.from_client_config(_client_config(), scopes=SCOPES)
    creds = flow.run_local_server(
        port=port,
        open_browser=open_browser,
        access_type="offline",
        prompt="consent",
        authorization_prompt_message="Opening Google consent in your browser… "
                                     "if it doesn't open, visit:\n{url}",
        success_message="Google authorization complete — you can close this tab.",
    )
    if not getattr(creds, "refresh_token", None):
        raise RuntimeError(
            "Google returned no refresh token. Re-run consent; this flow requests "
            "access_type=offline + prompt=consent to force one."
        )

    secrets_store.set_credential(_REFRESH_TOKEN, creds.refresh_token, owner=owner)
    return {"ok": True, "scopes": list(SCOPES), "has_refresh_token": True}


# Build a google.oauth2 Credentials object from the stored client + refresh token.
# Kept separate so tests can assert the wiring without the network.
def _stored_credentials():
    from google.oauth2.credentials import Credentials

    refresh_token = secrets_store.get_credential(_REFRESH_TOKEN)
    if not refresh_token:
        raise RuntimeError(
            "No Google refresh token stored. Run the consent flow first "
            "(setup_google tool, or scripts/vault_sync.py --setup)."
        )
    config = _client_config()["installed"]
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=_TOKEN_URI,
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        scopes=list(SCOPES),
    )


# Mint a fresh, valid access token from the stored refresh token. This is the
# function the live fetch layer injects as its `token_provider`. Refresh uses
# google-auth's transport, which handles clock skew and expiry correctly.
def get_access_token() -> str:
    try:
        from google.auth.transport.requests import Request
    except ImportError as error:  # pragma: no cover
        raise RuntimeError(
            "Google token refresh needs the 'connectors-google' extra. "
            "Install it: pip install -e \".[connectors-google]\""
        ) from error

    creds = _stored_credentials()
    creds.refresh(Request())
    return creds.token


# A quick, side-effect-light status for a `google_auth_status` / setup tool: which
# pieces of the Google credential chain are present, without exposing any value.
def status() -> Dict[str, Any]:
    return {
        "client_id_set": secrets_store.has_credential(_CLIENT_ID),
        "client_secret_set": secrets_store.has_credential(_CLIENT_SECRET),
        "refresh_token_set": secrets_store.has_credential(_REFRESH_TOKEN),
        "scopes": list(SCOPES),
        "ready": all(secrets_store.has_credential(name)
                     for name in (_CLIENT_ID, _CLIENT_SECRET, _REFRESH_TOKEN)),
    }
