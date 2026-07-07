"""Self-check for backup + scheduler, Phase 3 batch 4. Run:
    python tests/check_vault_scheduler.py

Offline (hash embedder + SQLite + temp VAULT_DIR + temp VAULT_BACKUP_DIR).
Requires `git` on PATH (the backup git-commits the vault). Verifies:

  3.10 backup      - a run produces a dated DB snapshot file AND a git commit
  3.10 security    - secrets are NEVER committed (.vault/secrets.* gitignored),
                     and the rebuildable index (.vault/index.db) isn't tracked
  3.10 no-op       - a second run with nothing changed makes no empty commit
  3.9  tick (dry)  - one tick reindexes, previews prune (changes nothing),
                     backs up, and writes a summary to today's journal
  3.9  tick (apply)- with apply=True an expired note is archived + tombstoned and
                     the journal records an "apply" summary

No test framework -- just asserts.
"""

import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="vault_sched_test_")
os.environ["VAULT_DIR"] = str(Path(_TMP) / "vault")
os.environ["VAULT_BACKUP_DIR"] = str(Path(_TMP) / "backups")
os.environ["VAULT_DB"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"

from servers.vault.db import get_db  # noqa: E402
from servers.vault.config import ensure_layout, paths, backup_dir  # noqa: E402
from servers.vault import backup as backup_mod  # noqa: E402
from servers.vault import scheduler  # noqa: E402
from servers.vault.index import index  # noqa: E402
from servers.vault.note import Note  # noqa: E402


def ok(condition, message):
    assert condition, message
    print(f"  ok: {message}")


def write_note(relpath, body, **extra):
    destination = paths()["vault"] / relpath
    note = Note(body=body)
    note.ensure_defaults(category=relpath.split("/", 1)[0])
    for key, value in extra.items():
        note.frontmatter[key] = value
    note.save(destination)
    return destination


def doc_id_for(database, relpath):
    for document in database.list_documents():
        if document["path"] == relpath:
            return document["id"]
    return None


def git_tracked(vault):
    result = subprocess.run(["git", "-C", str(vault), "ls-files"],
                            capture_output=True, text=True)
    return set(result.stdout.split())


def git_commit_count(vault):
    result = subprocess.run(["git", "-C", str(vault), "log", "--oneline"],
                            capture_output=True, text=True)
    return len([line for line in result.stdout.splitlines() if line.strip()])


# Fail early with a clear message if git isn't available (the feature needs it).
if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
    print("SKIP: git is not installed; backup requires it.")
    sys.exit(0)

PAST = "2020-01-01T00:00:00+00:00"

ensure_layout()
database = get_db()
database.migrate()
vault = paths()["vault"]

write_note("00-inbox/first.md", "# First\n\nThe very first captured note.\n", title="First")
index(incremental=True, database=database)


# --- 3.10 backup: dated DB snapshot + a git commit -------------------------
print("3.10 backup")
report = backup_mod.run_backup()
snapshot = report["db_snapshot"]
ok(snapshot.get("path") and Path(snapshot["path"]).is_file(), "a dated DB snapshot file was written")
ok(Path(snapshot["path"]).parent == backup_dir(), "the snapshot lives in the backup folder (outside the vault)")
ok(report["git"].get("committed") is True and report["git"].get("commit"), "the vault was git-committed")
ok((vault / ".git").exists(), "the vault is now a git repo")
ok(git_commit_count(vault) == 1, "exactly one commit so far")


# --- 3.10 security: secrets are never committed ----------------------------
print("3.10 security (secrets excluded)")
(paths()["system"] / "secrets.json").write_text('{"api_bearer_token": "SECRET"}', encoding="utf-8")
report2 = backup_mod.run_backup()
tracked = git_tracked(vault)
ok(".vault/secrets.json" not in tracked, "the secrets store is NOT tracked by git")
ok(".vault/index.db" not in tracked, "the rebuildable index DB is NOT tracked by git")
ok(".gitignore" in tracked, "a .gitignore is committed to enforce the exclusions")
# Nothing tracked changed (secrets are ignored) -> no empty commit.
ok(report2["git"].get("committed") is False, "a run with no tracked changes makes no commit")
ok(git_commit_count(vault) == 1, "still exactly one commit (no empty commit created)")


# --- 3.9 scheduler tick (dry-run) ------------------------------------------
print("3.9 tick (dry-run)")
write_note("50-resources/note-b.md", "# Note B\n\nA second resource note.\n", title="Note B")
tick = scheduler.run_once(database=database)  # apply defaults to config (dry-run)
ok(tick["applied"] is False, "prune is dry-run by default")
ok(tick["indexed"] >= 1, "the tick reindexed the new note")
ok(tick["ttl"]["dry_run"] is True and tick["decay"]["dry_run"] is True, "both prune policies previewed only")
ok(tick["backup"]["git"].get("committed") is True, "the tick committed the new note")
ok(Path(tick["backup"]["db_snapshot"]["path"]).is_file(), "the tick wrote a DB snapshot")

today = datetime.now().astimezone()
journal_path = paths()["10-journal"] / today.strftime("%Y") / f"{today.strftime('%Y-%m-%d')}.md"
ok(journal_path.exists(), "today's journal note exists")
journal_text = journal_path.read_text(encoding="utf-8")
ok("Scheduler tick (dry-run)" in journal_text and "reindex" in journal_text,
   "the journal recorded the dry-run tick summary")
# Dry-run must not have moved anything into the archive.
ok(not list((vault / "90-archive").rglob("*.md")), "dry-run archived nothing on disk")


# --- 3.9 scheduler tick (apply): an expired note is actually archived ------
print("3.9 tick (apply)")
write_note("50-resources/expired.md", "# Expired\n\nA note whose expiry has passed.\n",
           title="Expired", expires=PAST)
apply_tick = scheduler.run_once(apply=True, database=database)
ok(apply_tick["applied"] is True, "apply mode is reported")
ok(apply_tick["ttl"]["count"] >= 1, "the TTL policy archived at least one note")

expired_id = doc_id_for(database, "90-archive/50-resources/expired.md")
ok(expired_id is not None, "the expired note now lives under 90-archive/")
ok(database.get_document(expired_id)["status"] == "archived", "its status flipped to archived")
ok(any(t["document_id"] == expired_id for t in database.list_tombstones()),
   "an archival tombstone was recorded (reversible)")

journal_text = journal_path.read_text(encoding="utf-8")
ok("Scheduler tick (apply)" in journal_text, "the journal recorded the apply tick summary")


database.close()
print("\nALL VAULT PHASE 3 (BATCH 4) CHECKS PASSED")
