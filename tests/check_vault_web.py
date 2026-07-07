"""Self-check for the web dashboard (build step 9.2). Run:
    python tests/check_vault_web.py

Offline (hash embedder + SQLite + temp VAULT_DIR). Boots the Starlette app with the
in-process TestClient (no network) against a seeded vault and checks each page
renders the expected content:

  /            home renders vault counts + a "Next up" section
  /upcoming    shows a synced calendar event (ALL-CAPS group title kept intact)
  /search      returns a hit linking to the note viewer
  /note?ref=   renders the full note body
  /finances    lists a finance-category note
  /build       renders the build-plan status dashboard

Needs the `serve` extra (starlette + httpx for TestClient). No test framework --
just asserts.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="vault_web_test_")
os.environ["VAULT_DIR"] = _TMP
os.environ["VAULT_DB"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"

from servers.vault.db import get_db  # noqa: E402
from servers.vault.config import ensure_layout  # noqa: E402
from servers.vault import capture as cap  # noqa: E402
from servers.vault.connectors import calendar as cal  # noqa: E402
from servers.vault.web import create_app  # noqa: E402

try:
    from starlette.testclient import TestClient
except Exception as error:  # noqa: BLE001
    print(f"SKIP: starlette/httpx not installed ({error}); install the serve extra.")
    sys.exit(0)


def ok(condition, message):
    assert condition, message
    print(f"  ok: {message}")


ensure_layout()
database = get_db()
database.migrate()

# Seed: a project note (search + note viewer), an ALL-CAPS calendar event
# (upcoming + group parsing), and a finance-category note (finances page).
project = cap.capture("The kitchen renovation budget is $25,000 for cabinets and counters.",
                      category="30-projects", title="Kitchen reno budget", database=database)
cap.capture("Rent payment of 1500 due monthly.", category="40-areas/finance/transactions",
            title="Rent payment", database=database)
cal.sync_events([{
    "id": "ev-midterm", "summary": "SCHOOL: Midterm exam", "status": "confirmed",
    "start": {"dateTime": "2026-07-10T09:00:00-04:00"},
    "end": {"dateTime": "2026-07-10T11:00:00-04:00"},
}], database=database)

client = TestClient(create_app())


print("home")
home = client.get("/")
ok(home.status_code == 200, "home returns 200")
ok("Life Vault" in home.text and "Next up" in home.text, "home shows the nav + next-up section")

print("upcoming")
up = client.get("/upcoming")
ok(up.status_code == 200, "upcoming returns 200")
ok("SCHOOL: Midterm exam" in up.text, "upcoming lists the calendar event with its ALL-CAPS title intact")

print("search + note")
res = client.get("/search", params={"q": "kitchen renovation budget cabinets"})
ok(res.status_code == 200, "search returns 200")
ok("Kitchen reno budget" in res.text, "search surfaces the matching note (linked)")
note = client.get("/note", params={"ref": project["id"]})
ok(note.status_code == 200 and "25,000" in note.text, "note viewer renders the full body by id")

print("finances")
fin = client.get("/finances")
ok(fin.status_code == 200 and "Rent payment" in fin.text, "finances lists a 40-areas/finance note")

print("build")
build = client.get("/build")
ok(build.status_code == 200 and "Self-pruning" in build.text,
   "build page renders the status dashboard from the plan")

# group parsing (supports the Google-Calendar ALL-CAPS convention)
print("calendar group parsing")
ok(cal.event_to_fields({"id": "x", "summary": "FINANCE: Rent due",
                        "start": {"date": "2026-07-01"}})["group"] == "FINANCE",
   "an ALL-CAPS prefix is parsed into a group")
ok(cal.event_to_fields({"id": "y", "summary": "lunch with sam",
                        "start": {"date": "2026-07-01"}})["group"] is None,
   "a normal title has no group")

database.close()
print("\nALL VAULT WEB DASHBOARD CHECKS PASSED")
