"""Prune scoring + access signals (build steps 3.1-3.2).

This module computes each document's `prune_score` (lower = more prunable) and
tracks the usage signals that feed it. It does NOT move or delete anything yet --
archival/tombstones come in a later batch. It only ranks.

Two design choices worth knowing:

  - Signals live in a sidecar `.vault/signals.json` (keyed by document id), NOT in
    note frontmatter. Writing last_access into a note's frontmatter on every search
    would change the file's content hash and force a needless re-embed; the sidecar
    avoids that churn while still surviving a DB rebuild (the DB is derived; the
    sidecar is the durable home for usage telemetry). See build-plan deferral note.

  - Weights live in `.vault/prune.config` (a flat `key: value` file) so the policy
    is tunable in one auditable place. Missing file -> built-in defaults.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .config import paths
from .db import VaultDB, get_db

# Built-in scoring weights. Any of these may be overridden in .vault/prune.config.
# Pinned notes get a huge bonus so they effectively never sink (the hard "never
# prune pinned" rule lands with archival in the next batch; this keeps them safe
# in ranking meanwhile).
PRUNE_DEFAULTS: Dict[str, float] = {
    "w_recency": 2.0,       # freshness of the most recent touch (0..1 decayed)
    "w_usage": 1.0,         # how often search surfaces it (log scale)
    "w_importance": 1.0,    # author-set importance 0..5
    "w_links": 0.5,         # incoming links (a hub note is worth keeping)
    "w_pin": 1000.0,        # pinned -> effectively unprunable
    "w_age": 1.0,           # staleness penalty (subtracted)
    "half_life_days": 30.0, # recency half-life
    "staleness_days": 30.0, # divisor that turns "days since touch" into a penalty
    # Decay-archival policy (step 3.5): a note becomes a decay candidate when its
    # prune_score is at/below `decay_score_threshold` AND it has gone untouched for
    # at least `decay_min_idle_days`. Conservative defaults keep decay near-inert
    # until the owner tunes it in .vault/prune.config.
    "decay_score_threshold": 0.0,
    "decay_min_idle_days": 90.0,
    # Scheduler policy (step 3.9): whether an unattended tick APPLIES pruning or
    # only previews it. 0 = dry-run (safe default: report, don't move); >0 = apply.
    "prune_apply": 0.0,
}


# Parse the current UTC time once per call site so a whole recompute uses one
# consistent "now".
def _now() -> datetime:
    return datetime.now(timezone.utc)


# Parse an ISO-8601 timestamp into an aware datetime, or None if unparseable.
# Tolerates a trailing 'Z' and naive timestamps (assumed UTC).
def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# Load the prune weights: built-in defaults overlaid with any values found in
# .vault/prune.config. The file is flat `key: value` (floats); blanks and
# `#` comments are ignored. Unknown keys are ignored so the file can carry notes.
def load_config() -> Dict[str, float]:
    config = dict(PRUNE_DEFAULTS)
    config_path = paths()["prune_config"]
    if not config_path.exists():
        return config

    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key in config:
            config[key] = _parse_weight(key, value.strip())
    return config


# Convert one config value to float, with a clear error naming the offending key.
def _parse_weight(key: str, value: str) -> float:
    try:
        return float(value)
    except ValueError:
        raise ValueError(
            f"prune.config: weight {key!r} is not a number: {value!r}. "
            "Use a plain number like '2.0'."
        )


# Write a commented default prune.config so the user has something to tune. Does
# not overwrite an existing file. Returns the path written (or the existing one).
def write_default_config() -> str:
    config_path = paths()["prune_config"]
    if config_path.exists():
        return str(config_path)

    lines = ["# Prune scoring weights (higher score = more likely to KEEP).", ""]
    for key, value in PRUNE_DEFAULTS.items():
        lines.append(f"{key}: {value}")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(config_path)


# -- Signals sidecar --------------------------------------------------------

# Load the signals sidecar: {document_id: {"last_access": iso, "access_count": n}}.
# A missing or corrupt file is treated as "no signals yet" (empty dict).
def load_signals() -> Dict[str, Dict[str, Any]]:
    signals_path = paths()["signals_json"]
    if not signals_path.exists():
        return {}
    try:
        return json.loads(signals_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


# Persist the signals sidecar (pretty-printed so it's auditable by eye).
def save_signals(signals: Dict[str, Dict[str, Any]]) -> None:
    signals_path = paths()["signals_json"]
    signals_path.parent.mkdir(parents=True, exist_ok=True)
    signals_path.write_text(json.dumps(signals, indent=2, sort_keys=True), encoding="utf-8")


# Record that the given documents were just accessed (surfaced by search): bump
# each one's access_count and set last_access=now. Updates both the durable sidecar
# and the DB cache columns, then rescores the touched documents so usage shows
# up immediately. Returns the updated signal entries.
def record_access(document_ids: List[str], database: Optional[VaultDB] = None) -> Dict[str, Dict[str, Any]]:
    unique_ids = [doc_id for doc_id in dict.fromkeys(document_ids) if doc_id]
    if not unique_ids:
        return {}

    database = database or get_db()
    signals = load_signals()
    stamp = _now().isoformat(timespec="seconds")

    for doc_id in unique_ids:
        entry = signals.get(doc_id, {"last_access": None, "access_count": 0})
        entry["access_count"] = int(entry.get("access_count", 0)) + 1
        entry["last_access"] = stamp
        signals[doc_id] = entry
        database.set_signals(doc_id, stamp, entry["access_count"])

    save_signals(signals)
    _rescore_ids(database, unique_ids, signals, load_config())
    return {doc_id: signals[doc_id] for doc_id in unique_ids}


# -- Scoring ----------------------------------------------------------------

# Days since a document was last "touched": the later of its `updated` timestamp
# and its last_access signal. Shared by scoring (the staleness/recency terms) and
# the decay policy (its idle gate) so the two never disagree on what "touched"
# means. Returns 0.0 when no timestamp is parseable (treat as just-touched).
def days_since_touch(
    document: Dict[str, Any],
    signal: Optional[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> float:
    now = now or _now()
    updated = parse_iso(document.get("updated"))
    last_access = parse_iso((signal or {}).get("last_access"))
    reference = max([dt for dt in (updated, last_access) if dt], default=now)
    return max(0.0, (now - reference).total_seconds() / 86400.0)


# Compute a document's prune_score AND a per-term breakdown (the breakdown powers
# `explain-prune` later). `signal` is that document's sidecar entry (or None).
def compute_prune_score(
    document: Dict[str, Any],
    signal: Optional[Dict[str, Any]],
    incoming_links: int,
    config: Dict[str, float],
    now: Optional[datetime] = None,
) -> Tuple[float, Dict[str, float]]:
    now = now or _now()

    # Read typed document columns (present on both backends) rather than the
    # serialized frontmatter blob, so scoring never depends on JSON parsing.
    # The most recent "touch" is the later of last_access and the note's updated.
    days_since = days_since_touch(document, signal, now)

    access_count = int((signal or {}).get("access_count", 0))
    importance = int(document.get("importance") or 0)
    pinned = bool(document.get("pinned"))

    # Each term, then the weighted sum (age is the only subtracted term).
    recency = 0.5 ** (days_since / max(config["half_life_days"], 1e-9))
    usage = math.log1p(access_count)
    staleness = days_since / max(config["staleness_days"], 1e-9)

    breakdown = {
        "recency": config["w_recency"] * recency,
        "usage": config["w_usage"] * usage,
        "importance": config["w_importance"] * importance,
        "links": config["w_links"] * incoming_links,
        "pin": config["w_pin"] * (1.0 if pinned else 0.0),
        "age": -config["w_age"] * staleness,
    }
    score = sum(breakdown.values())
    return score, breakdown


# Recompute and store prune_score for a specific set of documents, given preloaded
# signals + config. Used after an access bump (cheap, only touched docs).
def _rescore_ids(
    database: VaultDB,
    document_ids: List[str],
    signals: Dict[str, Dict[str, Any]],
    config: Dict[str, float],
) -> None:
    now = _now()
    for doc_id in document_ids:
        document = database.get_document(doc_id)
        if document is None:
            continue
        incoming = len(database.backlinks(doc_id))
        score, _ = compute_prune_score(document, signals.get(doc_id), incoming, config, now)
        database.set_prune_score(doc_id, score)


# Reapply the durable signals to the DB cache and recompute every document's
# prune_score. Called at the end of indexing (and rebuild) so scores are always
# current and survive a derived-table rebuild. Returns a small summary.
def refresh(database: Optional[VaultDB] = None) -> Dict[str, Any]:
    database = database or get_db()
    signals = load_signals()
    config = load_config()
    now = _now()

    scored = 0
    for document in database.list_documents():
        doc_id = document["id"]
        signal = signals.get(doc_id)
        if signal:
            database.set_signals(
                doc_id, signal.get("last_access") or "", int(signal.get("access_count", 0))
            )
        incoming = len(database.backlinks(doc_id))
        score, _ = compute_prune_score(document, signal, incoming, config, now)
        database.set_prune_score(doc_id, score)
        scored += 1

    return {"scored": scored}


# Full score breakdown for one document, for inspection/audit (basis of the future
# `explain-prune` tool). Returns an error dict for an unknown id.
def explain(note_id: str, database: Optional[VaultDB] = None) -> Dict[str, Any]:
    database = database or get_db()
    document = database.get_document(note_id)
    if document is None:
        return {"error": f"no document with id {note_id!r}.",
                "hint": "pass an id from search results or vault_status."}

    signals = load_signals()
    config = load_config()
    incoming = len(database.backlinks(note_id))
    score, breakdown = compute_prune_score(document, signals.get(note_id), incoming, config)
    return {
        "id": note_id,
        "title": (document.get("title") or "").strip() or document.get("path"),
        "prune_score": round(score, 4),
        "breakdown": {term: round(value, 4) for term, value in breakdown.items()},
        "incoming_links": incoming,
        "access_count": int((signals.get(note_id) or {}).get("access_count", 0)),
    }
