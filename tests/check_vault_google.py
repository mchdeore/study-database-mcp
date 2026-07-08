"""Self-check for Google OAuth + live fetch, build step 4.1. Run:
    python tests/check_vault_google.py

Fully offline: the google libraries are never imported and no network is touched.
We inject a fake `http_get` (canned Google API responses) and a fake
`token_provider` into the fetch functions -- exactly the seam google_fetch.py
exposes -- so the paging / cursor / auth-chain logic is verified deterministically.

  scopes        - only read-only Calendar + Gmail scopes are requested
  client config - missing client id/secret -> actionable error; present -> usable
  auth status   - `ready` flips only once all three credential pieces are stored
  calendar      - internal pageToken paging; the nextSyncToken becomes the cursor
  cal 410       - an expired syncToken transparently falls back to a full resync
  gmail         - list ids -> metadata get; an `after:<cursor>` watermark is applied
  run_sync live - the injected fetch drives the real runner: notes are created,
                  the cursor is persisted, a re-sync is a clean no-op (dedup)

No test framework -- just asserts.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="vault_google_test_")
os.environ["VAULT_DIR"] = _TMP
os.environ["VAULT_DB"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"
# Pin the Gmail query so the assertion on the `after:` watermark is deterministic.
os.environ["GOOGLE_GMAIL_QUERY"] = "newer_than:30d"

from servers.vault.config import ensure_layout  # noqa: E402
from servers.vault.db import get_db  # noqa: E402
from servers.vault import credentials as secrets_store  # noqa: E402
from servers.vault.connectors import google_auth, google_fetch  # noqa: E402
from servers.vault.connectors.sync import CalendarConnector, GmailConnector, FetchResult, run_sync  # noqa: E402


def ok(condition, message):
    assert condition, message
    print(f"  ok: {message}")


def raises(func, needle, message):
    try:
        func()
    except Exception as error:  # noqa: BLE001
        assert needle.lower() in str(error).lower(), f"{message} (got: {error})"
        print(f"  ok: {message}")
        return
    raise AssertionError(f"{message} (no error raised)")


ensure_layout()
database = get_db()
database.migrate()

FAKE_TOKEN = "fake-access-token"


def fake_token():
    return FAKE_TOKEN


# --- scopes: read-only only -------------------------------------------------
print("scopes")
ok(all(scope.endswith(".readonly") for scope in google_auth.SCOPES),
   "every requested Google scope is read-only")
ok(any("calendar" in s for s in google_auth.SCOPES) and any("gmail" in s for s in google_auth.SCOPES),
   "both Calendar and Gmail scopes are present")


# --- client config + auth status -------------------------------------------
print("client config + status")
status = google_auth.status()
ok(status["ready"] is False and not status["refresh_token_set"], "a fresh vault is not authorized")
raises(google_auth._client_config, "not set", "missing client id/secret gives an actionable error")

secrets_store.set_credential("google_oauth_client_id", "cid-123.apps.googleusercontent.com")
secrets_store.set_credential("google_oauth_client_secret", "csecret-abc")
config = google_auth._client_config()["installed"]
ok(config["client_id"] == "cid-123.apps.googleusercontent.com", "stored client id flows into the config")
ok(config["token_uri"].startswith("https://"), "the token endpoint is HTTPS")
ok(google_auth.status()["ready"] is False, "still not ready without a refresh token")

secrets_store.set_credential("google_oauth_refresh_token", "refresh-xyz")
ok(google_auth.status()["ready"] is True, "ready once client id/secret + refresh token are all set")
ok(google_auth.has_consent() is True, "has_consent reflects the stored refresh token")


# --- fixture Google API responses ------------------------------------------
def event(event_id, summary, day):
    return {"id": event_id, "summary": summary, "status": "confirmed",
            "start": {"dateTime": f"2026-07-{day:02d}T09:00:00-04:00"},
            "end": {"dateTime": f"2026-07-{day:02d}T10:00:00-04:00"}}


EV_A, EV_B, EV_C = event("ev-a", "Alpha meeting", 10), event("ev-b", "Beta review", 11), \
                   event("ev-c", "Gamma sync", 12)

MESSAGES = {
    "m1": {"id": "m1", "labelIds": ["INBOX", "IMPORTANT"], "snippet": "Order shipped Tuesday.",
           "payload": {"headers": [{"name": "Subject", "value": "Your order shipped"},
                                   {"name": "From", "value": "Store <no-reply@store.com>"},
                                   {"name": "Date", "value": "Mon, 30 Jun 2026 12:00:00 -0400"}]}},
    "m2": {"id": "m2", "labelIds": ["INBOX"], "snippet": "Meeting moved to Friday.",
           "payload": {"headers": [{"name": "Subject", "value": "Re: schedule"}]}},
}

calls = []  # (url, params) log so we can assert what was requested


def fake_http_get(url, token, params):
    assert token == FAKE_TOKEN, "the fetch layer must send the provided access token"
    calls.append((url, dict(params)))

    if url.endswith("/events"):
        if params.get("syncToken"):  # incremental: caught up, echo the token back
            return {"items": [], "nextSyncToken": params["syncToken"]}
        if params.get("pageToken") == "page2":  # last page of the initial pull
            return {"items": [EV_C], "nextSyncToken": "SYNC-1"}
        return {"items": [EV_A, EV_B], "nextPageToken": "page2"}  # first page

    if url.endswith("/messages"):  # gmail list
        if "after:" in (params.get("q") or ""):
            return {"messages": []}
        return {"messages": [{"id": "m1"}, {"id": "m2"}]}

    if "/messages/" in url:  # gmail get
        return MESSAGES[url.rsplit("/", 1)[1]]

    raise AssertionError(f"unexpected URL: {url}")


# --- calendar fetch: paging + syncToken cursor -----------------------------
print("calendar fetch")
result = google_fetch.calendar_fetch_fn(None, token_provider=fake_token, http_get=fake_http_get)
ok(len(result.items) == 3, "internal pageToken paging pulled all 3 events in one fetch")
ok(result.next_cursor == "SYNC-1", "the nextSyncToken becomes the persisted cursor")
ok(any(p.get("timeMin") for _, p in calls), "the initial pull is bounded by timeMin")
ok(any(p.get("timeMax") for _, p in calls), "the initial pull is bounded by timeMax (future cap)")

caught_up = google_fetch.calendar_fetch_fn("SYNC-1", token_provider=fake_token, http_get=fake_http_get)
ok(caught_up.items == [] and caught_up.next_cursor == "SYNC-1",
   "an incremental fetch with the saved syncToken returns no changes")


# --- calendar 410: expired syncToken falls back to a full resync -----------
print("calendar 410 fallback")
def cal_get_410(url, token, params):
    if params.get("syncToken"):
        raise google_fetch.SyncTokenExpired("410 GONE")
    return {"items": [EV_A], "nextSyncToken": "SYNC-2"}

recovered = google_fetch.calendar_fetch_fn("STALE", token_provider=fake_token, http_get=cal_get_410)
ok(recovered.next_cursor == "SYNC-2" and len(recovered.items) == 1,
   "an expired syncToken transparently triggers a bounded full resync")


# --- gmail fetch: list + metadata get, after: watermark --------------------
print("gmail fetch")
calls.clear()
gmail_first = google_fetch.gmail_fetch_fn(None, token_provider=fake_token, http_get=fake_http_get,
                                          now=lambda: 1751850000)
ok(len(gmail_first.items) == 2, "listed ids are expanded to full message resources via get")
ok(gmail_first.next_cursor == "1751850000", "the cursor advances to the epoch watermark")
list_q = next(p.get("q") for u, p in calls if u.endswith("/messages"))
ok("after:" not in list_q, "the first sync has no after: watermark")

calls.clear()
google_fetch.gmail_fetch_fn("1751850000", token_provider=fake_token, http_get=fake_http_get,
                            now=lambda: 1751900000)
list_q2 = next(p.get("q") for u, p in calls if u.endswith("/messages"))
ok("after:1751850000" in list_q2, "a later sync applies the saved watermark as after:<cursor>")


# --- run_sync drives the real runner with the injected fetch ---------------
print("run_sync (calendar, live fetch injected)")
cal_connector = CalendarConnector(
    fetch_fn=lambda cursor: google_fetch.calendar_fetch_fn(
        cursor, token_provider=fake_token, http_get=fake_http_get))
report = run_sync(cal_connector, database=database)
ok(report["fetched"] == 3 and report.get("created") == 3, "the runner ingested all 3 events as notes")
ok(report["cursor"] == "SYNC-1", "the syncToken was persisted as the cursor")
docs = [d for d in database.list_documents() if d["path"].startswith("40-areas/calendar/")]
ok(len(docs) == 3, "three calendar notes exist in the vault")

again = run_sync(cal_connector, database=database)
ok(again["fetched"] == 0, "a re-sync from the saved syncToken fetches nothing new")

print("run_sync (gmail, live fetch injected)")
gmail_connector = GmailConnector(
    fetch_fn=lambda cursor: google_fetch.gmail_fetch_fn(
        cursor, token_provider=fake_token, http_get=fake_http_get, now=lambda: 1751850000),
    ttl_days=30)
mail_report = run_sync(gmail_connector, database=database)
ok(mail_report["fetched"] == 2 and mail_report.get("created") == 2, "both messages became mail notes")
mail_docs = [d for d in database.list_documents() if d["path"].startswith("50-resources/mail/")]
ok(len(mail_docs) == 2, "two ephemeral mail notes exist")
ok(all(database.get_document(d["id"])["expires"] for d in mail_docs), "each mail note carries a TTL expiry")


database.close()
print("\nALL VAULT PHASE 4.1 (GOOGLE OAUTH + LIVE FETCH) CHECKS PASSED")
