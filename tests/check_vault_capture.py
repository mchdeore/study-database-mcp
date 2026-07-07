"""Self-check for the vault write path, Phase 1. Run:
    python tests/check_vault_capture.py

Offline (hash embedder + SQLite + temp VAULT_DIR). Verifies:

  1.1 capture            - free text -> note in the inbox, indexed + searchable
  1.2 quick_note         - titled note (title becomes the H1)
  1.3 append_to_journal  - timestamped entries land in one dated daily note
  1.4 auto-frontmatter   - captured notes carry a complete, valid frontmatter
  1.5 list_inbox         - inbox triage lists unfiled notes only
  1.6 credentials        - missing/set/get + owner guard + clear errors

No test framework -- just asserts.
"""

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="vault_capture_test_")
os.environ["VAULT_DIR"] = _TMP
os.environ["VAULT_DB"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"

from servers.vault.db import get_db  # noqa: E402
from servers.vault.note import Note  # noqa: E402
from servers.vault import capture as cap  # noqa: E402
from servers.vault import credentials as sec  # noqa: E402
from servers.vault.search import search  # noqa: E402
from servers.vault.config import paths  # noqa: E402


def ok(condition, message):
    assert condition, message
    print(f"  ok: {message}")


database = get_db()
database.migrate()


# --- 1.1 capture -----------------------------------------------------------
print("1.1 capture")
result = cap.capture("Buy new faucet for the kitchen reno before the contractor arrives.",
                     tags=["home", "todo"], database=database)
ok("error" not in result, f"capture succeeded: {result}")
ok(result["path"].startswith("00-inbox/"), f"captured into the inbox: {result['path']}")
ok(result["indexed"] == [result["path"]], "exactly the new note was indexed")

hits = search("kitchen faucet contractor", k=5, database=database)["results"]
ok(any(h["citation"]["source"] == result["path"] for h in hits), "captured note is searchable")

ok(cap.capture("   ", database=database).get("error"), "empty capture text returns a clear error")


# --- 1.4 auto-frontmatter --------------------------------------------------
print("1.4 auto-frontmatter")
captured = Note.load(paths()["vault"] / result["path"])
for key in ("id", "title", "category", "created", "updated", "source", "status"):
    ok(key in captured.frontmatter, f"frontmatter has '{key}'")
ok(captured.frontmatter["source"] == "capture", "source is 'capture'")
ok(captured.frontmatter["status"] == "active", "status defaults to 'active'")
ok(captured.frontmatter["tags"] == ["home", "todo"], "tags were stored")


# --- 1.2 quick_note --------------------------------------------------------
print("1.2 quick_note")
qn = cap.quick_note("Kitchen reno budget", "Cap total spend at 12k.",
                    category="30-projects", database=database)
ok(qn["path"].startswith("30-projects/"), f"filed into the given category: {qn['path']}")
note = Note.load(paths()["vault"] / qn["path"])
ok(note.body.lstrip().startswith("# Kitchen reno budget"), "title became the H1 heading")
ok(cap.quick_note("", "body", database=database).get("error"), "empty title returns a clear error")


# --- 1.3 append_to_journal -------------------------------------------------
print("1.3 append_to_journal")
morning = datetime(2026, 6, 29, 9, 0).astimezone()
evening = datetime(2026, 6, 29, 21, 30).astimezone()
first = cap.append_to_journal("Picked the tile.", when=morning, database=database)
second = cap.append_to_journal("Signed the contractor.", when=evening, database=database)
ok(first["path"] == second["path"], "both entries land in the same dated journal note")
ok(first["path"].startswith("10-journal/2026/"), f"journal path is year-nested: {first['path']}")
journal = Note.load(paths()["vault"] / first["path"]).body
ok("Picked the tile." in journal and "Signed the contractor." in journal,
   "both entries are present in the daily note")
ok("## 09:00" in journal and "## 21:30" in journal, "entries are timestamped")


# --- 1.5 list_inbox --------------------------------------------------------
print("1.5 list_inbox")
inbox = cap.list_inbox()
inbox_paths = {entry["path"] for entry in inbox}
ok(result["path"] in inbox_paths, "the captured inbox note is listed")
ok(qn["path"] not in inbox_paths, "the filed (non-inbox) note is NOT listed")
ok(all(entry["summary"] for entry in inbox), "each inbox entry has a summary")


# --- 1.6 credentials -------------------------------------------------------
print("1.6 credentials")
missing_before = {item["name"] for item in sec.missing_credentials()}
ok("deepseek_api_key" in missing_before, "a known credential shows as missing initially")

sec.set_credential("deepseek_api_key", "sk-test-123", owner=True)
ok(sec.get_credential("deepseek_api_key") == "sk-test-123", "set then get returns the value")
ok("deepseek_api_key" not in {i["name"] for i in sec.missing_credentials()},
   "the set credential drops out of 'missing'")

try:
    sec.set_credential("x", "y", owner=False)
    raise AssertionError("non-owner set should be refused")
except PermissionError:
    print("  ok: non-owner set_credential is refused")

for bad in [("", "v"), ("name", "")]:
    try:
        sec.set_credential(*bad, owner=True)
        raise AssertionError(f"empty input should error: {bad}")
    except ValueError:
        pass
print("  ok: empty name/value return clear errors")

database.close()
print("\nALL VAULT PHASE 1 CHECKS PASSED")
