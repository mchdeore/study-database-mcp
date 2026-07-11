"""Extensive self-check for the Gmail triage refinement. Run:
    python tests/check_vault_mail_triage.py

Fully offline (hash embedder + SQLite + temp VAULT_DIR), fixture Gmail message
dicts — no live OAuth or network. Covers the data-reduction brain end to end:

  classify        - header-only classification + precedence (starred > promo >
                    social > list-mail > important > personal; drafts/spam excluded)
  clean_snippet   - zero-width padding (incl. U+034F) + boilerplate stripped,
                    whitespace collapsed, long text truncated
  datetime        - internalDate (epoch ms) / Date header / fallback resolution
  digest          - key/title/line format, parse↔render roundtrip, id-dedup merge
  sync policy     - skip noise (counted, not written); keep keepers as clean,
                    importance-scored notes; roll bulk into ONE weekly digest note
  retention       - starred keepers get no expiry; personal/important get a TTL;
                    an expired keeper is archived by the TTL policy, a starred one is not
  dedup           - re-syncing is a no-op; a later sync merges into the same digest
  plan_messages   - classify-only preview writes nothing and caps the keepers list
  index           - a kept note is findable and its body is free of zero-width junk

No test framework -- just asserts.
"""

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="vault_triage_test_")
os.environ["VAULT_DIR"] = _TMP
os.environ["VAULT_DB"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"

from servers.vault.config import ensure_layout, paths  # noqa: E402
from servers.vault.db import get_db  # noqa: E402
from servers.vault.note import Note  # noqa: E402
from servers.vault.search import search  # noqa: E402
from servers.vault import archive  # noqa: E402
from servers.vault.connectors import gmail, mail_triage as mt  # noqa: E402


def ok(condition, message):
    assert condition, message
    print(f"  ok: {message}")


# --- fixture builder -------------------------------------------------------
def msg(mid, labels, *, subject="", sender="", snippet="", unsubscribe=None,
        list_id=None, date=None, internal=None):
    headers = [{"name": "Subject", "value": subject}, {"name": "From", "value": sender}]
    if date:
        headers.append({"name": "Date", "value": date})
    if unsubscribe:
        headers.append({"name": "List-Unsubscribe", "value": unsubscribe})
    if list_id:
        headers.append({"name": "List-Id", "value": list_id})
    message = {"id": mid, "labelIds": list(labels), "snippet": snippet or subject,
               "payload": {"headers": headers}}
    if internal is not None:
        message["internalDate"] = str(internal)
    return message


def epoch_ms(dt):
    return int(dt.timestamp() * 1000)


# ===========================================================================
# 1. classify — precedence + every class
# ===========================================================================
print("classify")
ok(mt.classify(msg("s", ["INBOX", "STARRED"])).klass == "starred", "STARRED -> starred")
ok(mt.classify(msg("s", ["INBOX", "STARRED"])).action == "keep", "starred is kept")
ok(mt.classify(msg("s", ["INBOX", "STARRED"])).ttl_days is None, "starred keeper never expires")
ok(mt.classify(msg("s", ["INBOX", "STARRED"])).importance == 4, "starred importance is 4")

ok(mt.classify(msg("p", ["INBOX", "CATEGORY_PROMOTIONS"])).action == "skip", "promotions -> skip")
ok(mt.classify(msg("o", ["INBOX", "CATEGORY_SOCIAL"])).action == "skip", "social -> skip")

ok(mt.classify(msg("u", ["INBOX", "CATEGORY_UPDATES"], unsubscribe="<mailto:x>")).action == "digest",
   "updates + List-Unsubscribe -> digest (bulk)")
ok(mt.classify(msg("l", ["INBOX"], list_id="<news.example.com>")).klass == "bulk",
   "a List-Id header alone marks list mail as bulk")
ok(mt.classify(msg("f", ["INBOX", "CATEGORY_FORUMS"])).klass == "bulk", "forums -> bulk")
ok(mt.classify(msg("u", ["INBOX", "CATEGORY_UPDATES"], unsubscribe="<mailto:x>")).importance == 1,
   "bulk importance is 1 (ranks below real mail)")

ok(mt.classify(msg("i", ["INBOX", "IMPORTANT"])).klass == "important", "IMPORTANT -> important")
ok(mt.classify(msg("i", ["INBOX", "IMPORTANT"])).ttl_days == mt.IMPORTANT_TTL_DAYS,
   "important keeps for the important TTL")
ok(mt.classify(msg("m", ["INBOX"])).klass == "personal", "plain inbox mail -> personal")
ok(mt.classify(msg("m", ["INBOX"])).ttl_days == mt.PERSONAL_TTL_DAYS, "personal keeps for the personal TTL")

# precedence / exclusions
ok(mt.classify(msg("d", ["DRAFT"])).klass == "excluded", "DRAFT is excluded (never ingested)")
ok(mt.classify(msg("d", ["DRAFT", "IMPORTANT"])).action == "skip",
   "an excluded label wins even over IMPORTANT")
ok(mt.classify(msg("sp", ["SPAM"])).action == "skip", "SPAM is skipped")
ok(mt.classify(msg("st", ["INBOX", "STARRED", "CATEGORY_PROMOTIONS"])).klass == "starred",
   "an explicit star beats the Promotions category")
ok(mt.classify(msg("pu", ["INBOX", "CATEGORY_PROMOTIONS"], unsubscribe="<mailto:x>")).action == "skip",
   "a promo with List-Unsubscribe is still skipped (promotions precede bulk)")


# ===========================================================================
# 2. clean_snippet — the zero-width / boilerplate scrubber
# ===========================================================================
print("clean_snippet")
padded = "Clio Data Scientist" + "\u034f \u200d \u200b \u2060 \ufeff" + " role"
cleaned = mt.clean_snippet(padded)
for bad in ("\u034f", "\u200d", "\u200b", "\u2060", "\ufeff"):
    ok(bad not in cleaned, f"zero-width U+{ord(bad):04X} stripped from snippet")
ok(cleaned == "Clio Data Scientist role", f"padding collapses to clean text (got {cleaned!r})")
ok(mt.clean_snippet("Deal! view in browser now") == "Deal! now", "'view in browser' boilerplate removed")
ok("unsubscribe" not in mt.clean_snippet("Read more Unsubscribe here").lower(),
   "'unsubscribe' boilerplate removed")
ok(mt.clean_snippet("a\n\n b\t\tc   d") == "a b c d", "runs of whitespace collapse to single spaces")
ok(mt.clean_snippet("") == "", "empty snippet stays empty")
long_text = "x" * 800
ok(len(mt.clean_snippet(long_text)) <= 501 and mt.clean_snippet(long_text).endswith("…"),
   "an over-long snippet is truncated with an ellipsis")


# ===========================================================================
# 3. message_datetime — internalDate / Date header / fallback
# ===========================================================================
print("message_datetime")
when = datetime(2026, 7, 8, 15, 30, tzinfo=timezone.utc)
ok(mt.message_datetime(msg("a", ["INBOX"], internal=epoch_ms(when))).date() == when.date(),
   "internalDate (epoch ms) resolves to the right day")
ok(mt.message_datetime(msg("b", ["INBOX"], date="Mon, 30 Jun 2026 12:00:00 -0400")).date()
   == datetime(2026, 6, 30).date(), "the Date header is parsed when internalDate is absent")
fallback = datetime(2000, 1, 1, tzinfo=timezone.utc)
ok(mt.message_datetime(msg("c", ["INBOX"]), fallback=fallback) == fallback,
   "with neither signal, the fallback is used")
ok(mt.message_datetime(msg("d", ["INBOX"], date="not a date"), fallback=fallback) == fallback,
   "an unparseable Date header falls back cleanly")


# ===========================================================================
# 4. digest helpers — key/title/line + parse↔render + id-dedup merge
# ===========================================================================
print("digest helpers")
w = datetime(2026, 7, 8, tzinfo=timezone.utc)  # ISO week 28 of 2026
ok(mt.digest_key(w) == "gmail://digest/2026-W28", f"ISO-week digest key (got {mt.digest_key(w)})")
line = mt.digest_line(msg("z1", ["INBOX"], subject="Weekly digest", sender="News <n@x.com>"), w)
ok(line.startswith("- 2026-07-08 · News <n@x.com> · Weekly digest") and "<!-- id:z1 -->" in line,
   "digest line carries date · sender · subject + an id marker")
parsed = mt.parse_digest_items(mt.render_digest("T", {"z1": line}, mt.digest_key(w)))
ok(parsed == {"z1": line}, "render -> parse roundtrips the item back by id")
# merge: same id doesn't duplicate; new id is added
line2 = mt.digest_line(msg("z2", ["INBOX"], subject="Later", sender="A <a@x>"),
                       datetime(2026, 7, 9, tzinfo=timezone.utc))  # later day, same ISO week
merged = {**parsed, **{"z1": line, "z2": line2}}
ok(set(merged) == {"z1", "z2"}, "merging by id de-duplicates and unions")
rendered = mt.render_digest(mt.digest_title(w), merged, mt.digest_key(w))
ok(rendered.index("z2") < rendered.index("z1"),
   "digest rows are newest-first (z2 dated Jul 9 sorts above z1 dated Jul 8)")


# ===========================================================================
# 5. sync_messages — the integrated triage policy
# ===========================================================================
print("sync policy")
ensure_layout()
database = get_db()
database.migrate()

NOW = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
WEEK_INTERNAL = epoch_ms(datetime(2026, 7, 7, tzinfo=timezone.utc))  # same ISO week (W28)

STARRED = msg("star-1", ["INBOX", "STARRED"], subject="Signed lease", sender="Landlord <l@x.com>",
              date="Tue, 07 Jul 2026 09:00:00 -0400")
PERSONAL = msg("pers-1", ["INBOX"], subject="Re: dinner Friday", sender="Sam <sam@x.com>",
               snippet="Are we still on for" + "\u034f\u200d" + " Friday? unsubscribe",
               date="Tue, 07 Jul 2026 10:00:00 -0400")
PROMO = msg("promo-1", ["INBOX", "CATEGORY_PROMOTIONS"], subject="25% off flights",
            sender="VIA <v@x.com>")
SOCIAL = msg("soc-1", ["INBOX", "CATEGORY_SOCIAL"], subject="You have 3 new connections",
             sender="LinkedIn <n@linkedin.com>")
DRAFT = msg("draft-1", ["DRAFT"], subject="unsent reply", sender="me")
ALERT1 = msg("alert-1", ["INBOX", "CATEGORY_UPDATES"], subject="Data Scientist at Clio",
             sender="LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
             unsubscribe="<https://linkedin.com/unsub>", internal=WEEK_INTERNAL)
ALERT2 = msg("alert-2", ["INBOX"], subject="Software Engineer at Scotiabank",
             sender="LinkedIn <jobalerts@linkedin.com>", list_id="<jobs.linkedin.com>",
             internal=WEEK_INTERNAL)

batch = [STARRED, PERSONAL, PROMO, SOCIAL, DRAFT, ALERT1, ALERT2]
summary = gmail.sync_messages(batch, now=NOW, database=database)

ok(summary["classes"].get("promotion") == 1 and summary["classes"].get("social") == 1,
   "promotions and social are classified (and counted) ...")
ok(summary["classes"].get("excluded") == 1, "... the draft is classified excluded ...")
ok(summary["skipped"] == 3, "... and all three (promo+social+draft) are skipped, never written")
ok(summary["kept"] == 2, "two keepers (starred + personal) become individual notes")
ok(summary["digested"] == 2, "two job alerts are routed to the digest")
ok(len(summary["digests"]) == 1 and summary["digests"][0]["items"] == 2,
   "the two alerts roll into exactly ONE weekly digest note")

mail_docs = [d for d in database.list_documents() if d["path"].startswith("50-resources/mail/")]
ok(len(mail_docs) == 3, "vault has 3 mail notes total: 2 keepers + 1 digest (not 7)")

star_id = database.find_document_by_source_ref("gmail://msg/star-1")
pers_id = database.find_document_by_source_ref("gmail://msg/pers-1")
digest_id = database.find_document_by_source_ref("gmail://digest/2026-W28")
ok(all((star_id, pers_id, digest_id)), "keepers and the weekly digest each resolve by source_ref")
ok(database.find_document_by_source_ref("gmail://msg/promo-1") is None,
   "the promo was never written to the vault")

star_note = Note.load(paths()["vault"] / database.get_document(star_id)["path"])
pers_note = Note.load(paths()["vault"] / database.get_document(pers_id)["path"])
ok(star_note.frontmatter["importance"] == 4 and star_note.frontmatter.get("expires") in (None,),
   "the starred keeper is importance 4 with NO expiry")
ok("starred" in star_note.frontmatter["tags"], "the keeper is tagged with its class")
ok(pers_note.frontmatter["importance"] == 3 and pers_note.frontmatter["expires"],
   "the personal keeper is importance 3 WITH a TTL expiry")
for bad in ("\u034f", "\u200d"):
    ok(bad not in pers_note.body, f"the kept note body is scrubbed of U+{ord(bad):04X}")
ok("unsubscribe" not in pers_note.body.lower(), "kept note body has boilerplate removed")

digest_note = Note.load(paths()["vault"] / database.get_document(digest_id)["path"])
ok("Clio" in digest_note.body and "Scotiabank" in digest_note.body,
   "the digest note lists both alerts' subjects")
ok(digest_note.frontmatter["importance"] == 1, "the digest ranks low (importance 1)")


# ===========================================================================
# 6. dedup — re-sync is a no-op; a later sync merges into the same digest
# ===========================================================================
print("dedup + digest merge")
again = gmail.sync_messages(batch, now=NOW, database=database)
ok(again["kept"] == 2 and again["created"] == 0 and again["unchanged"] == 2,
   "re-syncing the keepers changes nothing (unchanged)")
ok(again["digests"][0]["action"] == "unchanged", "the digest is unchanged when no new alerts arrive")
ok(len([d for d in database.list_documents() if d["path"].startswith("50-resources/mail/")]) == 3,
   "no duplicate notes created on re-sync")

ALERT3 = msg("alert-3", ["INBOX", "CATEGORY_UPDATES"], subject="ML Engineer at TD",
             sender="LinkedIn <jobalerts@linkedin.com>", unsubscribe="<x>", internal=WEEK_INTERNAL)
merged_run = gmail.sync_messages([ALERT3], now=NOW, database=database)
ok(merged_run["digests"][0]["action"] == "updated" and merged_run["digests"][0]["items"] == 3,
   "a new alert in the same week MERGES into the existing digest (now 3 items)")
ok(database.find_document_by_source_ref("gmail://digest/2026-W28") == digest_id,
   "the digest kept its stable id across the merge (no duplicate)")
digest_note = Note.load(paths()["vault"] / database.get_document(digest_id)["path"])
ok(digest_note.body.count("<!-- id:") == 3, "the merged digest body carries all three alert ids")


# ===========================================================================
# 7. index — a kept note is findable
# ===========================================================================
print("search")
results = search("dinner Friday Sam", k=5, database=database)["results"]
ok(any(database.get_document(pers_id)["path"] == hit["citation"]["source"] for hit in results),
   "the personal keeper is findable via search")


# ===========================================================================
# 8. retention — TTL archives an expired keeper; a starred keeper survives
# ===========================================================================
print("retention / TTL")
# Push the personal keeper's expiry into the past by re-syncing with an old clock.
gmail.sync_messages([PERSONAL], now=datetime(2020, 1, 1, tzinfo=timezone.utc), database=database)
gmail.sync_messages([STARRED], now=datetime(2020, 1, 1, tzinfo=timezone.utc), database=database)
ok(database.get_document(pers_id)["status"] == "active", "personal keeper active before pruning")
archive.run_ttl(dry_run=False, database=database)
ok(database.get_document(pers_id)["status"] == "archived",
   "the expired personal keeper is archived by the TTL policy")
ok(database.get_document(star_id)["status"] == "active",
   "the starred keeper has no expiry, so TTL never touches it")


# ===========================================================================
# 9. plan_messages — classify-only preview writes nothing
# ===========================================================================
print("plan_messages (preview)")
before = len(database.list_documents())
plan = gmail.plan_messages(batch, now=NOW)
ok(len(database.list_documents()) == before, "plan_messages writes NOTHING to the vault")
ok(plan["actions"] == {"keep": 2, "digest": 2, "skip": 3}, "plan reports keep/digest/skip counts")
ok(plan["classes"].get("promotion") == 1 and plan["classes"].get("starred") == 1,
   "plan reports the per-class breakdown")
ok(len(plan["keepers"]) == 2 and all("subject" in k and "importance" in k for k in plan["keepers"]),
   "plan lists the keepers with their computed importance")
ok(plan["digest_weeks"].get("2026-W28") == 2, "plan groups bulk mail by the ISO week it would digest into")

# keepers list is capped for token budget
many = [msg(f"k{i}", ["INBOX", "IMPORTANT"], subject=f"thread {i}") for i in range(gmail._PLAN_KEEPER_CAP + 10)]
capped = gmail.plan_messages(many)
ok(len(capped["keepers"]) == gmail._PLAN_KEEPER_CAP and capped["keepers_truncated"],
   "the preview caps the keepers list and flags truncation")


database.close()
print("\nALL VAULT MAIL-TRIAGE CHECKS PASSED")
