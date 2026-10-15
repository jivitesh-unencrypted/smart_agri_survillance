"""
Offline-first event outbox.

The local AI server must keep detecting when the internet is down. Every
event that would otherwise be written to Supabase is instead appended to
a small local SQLite queue and flushed by a background thread once
connectivity returns.

Why this is durable
-------------------
The queue lives on disk (ai-server/data/outbox.db), not in memory, so a
crash or restart while offline does not lose recorded events. Snapshot
JPEG bytes are left in the normal local snapshots/ folder (referenced by
path) until they are successfully uploaded, so nothing is uploaded twice
and nothing is uploaded that was never saved locally.

Exactly-once semantics
----------------------
Each queued row carries a client-generated UUID (`client_event_id` /
`client_alert_id`) with a UNIQUE constraint in Postgres. Replay is
therefore idempotent: a row that already made it to the cloud (e.g. the
response was lost mid-flight) is reused instead of duplicated.

Ownership
---------
Every queued row records the owning user, both inside the JSON payload
(which is what actually gets written to Postgres) and in its own `user_id`
column, which exists so ownership can be inspected and reported without
having to parse every payload.

Offline is precisely when this matters most: a camera keeps running with no
internet for hours, and every event it produces is buffered here. The owner
is captured when the event is queued - from the camera row, long before any
network call - so it cannot be lost, guessed, or reassigned by whoever
happens to be signed in when the queue finally drains. Rows that predate the
owner column are given the same deterministic legacy owner the SQL migration
used (the earliest-created profile) and are logged loudly when that happens.
"""
import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app import supabase_client as sc
from app.repositories import alerts as alerts_repo
from app.repositories import detections as detections_repo
from app.repositories import users as users_repo

logger = logging.getLogger("ai_server.outbox")

_lock = threading.Lock()

KIND_DETECTION = "detection"
KIND_ALERT = "alert"
# Closing values of an event that ended while the cloud was unreachable.
# Replayed as an upsert, not an insert: the event may or may not already
# exist in the cloud depending on whether it opened before or during the
# outage, and both cases have to end up as one correct row.
KIND_DETECTION_FINAL = "detection_final"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT    NOT NULL,
    payload       TEXT    NOT NULL,
    snapshot_file TEXT,
    created_at    TEXT    NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT
);
CREATE INDEX IF NOT EXISTS ix_outbox_created ON outbox (created_at);
"""

# Added after the queue was already in use, so `CREATE TABLE IF NOT EXISTS`
# above will not add it to an existing outbox.db. SQLite has no
# "ADD COLUMN IF NOT EXISTS", so the column list is probed instead.
_legacy_owner_cache: Optional[str] = None
_legacy_owner_looked_up = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(settings.outbox_db_path), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _lock, _connect() as conn:
        conn.executescript(_SCHEMA)
        # Add the owner column to a pre-existing queue. Rows already in it keep
        # a NULL owner and are resolved at flush time (see `_ensure_owner`).
        if not any(r["name"] == "user_id" for r in conn.execute("PRAGMA table_info(outbox)")):
            conn.execute("ALTER TABLE outbox ADD COLUMN user_id TEXT")
            logger.info("Added user_id column to the existing offline outbox")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_outbox_user ON outbox (user_id)")
    logger.info("Offline outbox ready at %s", settings.outbox_db_path)


# ---------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------
def enqueue_detection(payload: Dict[str, Any], snapshot_file: Optional[str] = None) -> Optional[str]:
    """
    Queues a detection event and RETURNS its idempotency key.

    The caller needs the key because an alert queued for the same event
    references it via `pending_detection_client_event_id`; returning it here
    means the key can never drift between the two rows.
    """
    payload = dict(payload)
    key = payload.setdefault("client_event_id", str(uuid.uuid4()))
    _insert(KIND_DETECTION, payload, snapshot_file)
    return key


def enqueue_alert(payload: Dict[str, Any]) -> Optional[str]:
    payload = dict(payload)
    key = payload.setdefault("client_alert_id", str(uuid.uuid4()))
    _insert(KIND_ALERT, payload, None)
    return key


def enqueue_detection_final(payload: Dict[str, Any]) -> Optional[str]:
    """
    Queues the closing state (end_time, duration, frame_count, confidences,
    last box) of an event that finished while Supabase was unreachable.

    The payload is the complete row, not a delta, so the replay can be a
    single upsert whether or not the event itself ever made it to the cloud.
    """
    payload = dict(payload)
    key = payload.setdefault("client_event_id", str(uuid.uuid4()))
    _insert(KIND_DETECTION_FINAL, payload, None)
    return key


def _insert(kind: str, payload: Dict[str, Any], snapshot_file: Optional[str]) -> None:
    from datetime import datetime, timezone

    try:
        with _lock, _connect() as conn:
            conn.execute(
                "INSERT INTO outbox (kind, payload, snapshot_file, created_at, user_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (kind, json.dumps(payload, default=str), snapshot_file,
                 datetime.now(timezone.utc).isoformat(),
                 # Mirrored out of the payload so ownership is inspectable
                 # without parsing JSON. The payload remains the source of
                 # truth for what actually gets synced.
                 payload.get("user_id")),
            )
    except Exception as exc:  # noqa: BLE001 - never let queueing break detection
        logger.error("Could not queue %s event for later sync: %s", kind, exc)


def pending_count() -> int:
    try:
        with _lock, _connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS c FROM outbox").fetchone()["c"])
    except Exception:
        return 0


def pending_by_owner() -> Dict[str, int]:
    """Queued rows per owner, for diagnostics and the sync status endpoint."""
    try:
        with _lock, _connect() as conn:
            return {
                (r["user_id"] or "unowned"): int(r["c"])
                for r in conn.execute(
                    "SELECT user_id, COUNT(*) AS c FROM outbox GROUP BY user_id"
                )
            }
    except Exception:
        return {}


def _legacy_owner() -> Optional[str]:
    """
    Resolves the fallback owner for rows queued before owner tracking existed.

    Uses the same rule as `0005_per_user_isolation.sql`: the earliest-created
    application profile. Resolved once and cached, because it only applies to
    pre-upgrade rows and the sync loop would otherwise retry it per row while
    the network is flaky.
    """
    global _legacy_owner_cache, _legacy_owner_looked_up
    if not _legacy_owner_looked_up:
        _legacy_owner_looked_up = True
        try:
            _legacy_owner_cache = users_repo.oldest_profile_id()
        except sc.SupabaseUnavailable:
            _legacy_owner_cache = None
        if _legacy_owner_cache:
            logger.warning(
                "Queued outbox rows predate owner tracking; assigning them to the "
                "earliest profile %s (see migration 0005)",
                _legacy_owner_cache[:8],
            )
    return _legacy_owner_cache


# ---------------------------------------------------------------------
# Flushing
# ---------------------------------------------------------------------
def _fetch_batch(limit: int) -> List[sqlite3.Row]:
    with _lock, _connect() as conn:
        return list(conn.execute(
            "SELECT * FROM outbox ORDER BY id ASC LIMIT ?", (limit,)
        ))


def _drop(row_id: int) -> None:
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM outbox WHERE id = ?", (row_id,))


def _record_failure(row_id: int, error: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?",
            (error[:500], row_id),
        )


def flush(limit: int = 100) -> Dict[str, int]:
    """
    Attempts to drain the queue. Never raises: a sync failure must not
    interrupt the camera threads.

    Returns {"flushed": n, "failed": n, "remaining": n}.
    """
    stats = {"flushed": 0, "failed": 0, "remaining": pending_count()}
    if not settings.sync_enabled:
        return stats
    if not sc.is_configured():
        return stats

    batch = _fetch_batch(limit)
    if not batch:
        return stats

    for row in batch:
        try:
            payload = json.loads(row["payload"])
            # A queued row always carries its owner in the payload. Rows that
            # were queued before owner tracking existed have none, and Postgres
            # now rejects an unowned detection, so they are stamped with the
            # legacy owner here - once, loudly, and before the write.
            _ensure_owner(row, payload)
            if row["kind"] == KIND_DETECTION:
                _flush_detection(payload, row["snapshot_file"])
            elif row["kind"] == KIND_DETECTION_FINAL:
                # No snapshot_file: the snapshot that belongs to this event was
                # queued alongside its opening row, and the queue drains
                # oldest-first, so it has already been uploaded by now.
                detections_repo.upsert_detection(payload)
            elif row["kind"] == KIND_ALERT:
                _flush_alert(payload)
            else:
                logger.warning("Dropping unknown outbox kind %s", row["kind"])

            _drop(row["id"])
            stats["flushed"] += 1
            logger.info("Synced queued %s event to Supabase", row["kind"])
        except sc.SupabaseUnavailable as exc:
            _record_failure(row["id"], str(exc))
            stats["failed"] += 1
            # Still offline - stop early instead of hammering a dead link.
            break
        except Exception as exc:  # noqa: BLE001
            _record_failure(row["id"], str(exc))
            stats["failed"] += 1
            logger.error("Unexpected error flushing outbox row %s: %s", row["id"], exc)

    stats["remaining"] = pending_count()
    return stats


def _ensure_owner(row: sqlite3.Row, payload: Dict[str, Any]) -> None:
    """
    Guarantees the payload carries a `user_id` before it is synced.

    A detection's owner is captured from its camera at detection time, so the
    normal path already has one. This only touches rows queued by an older
    build. If no owner can be resolved the row is left alone and the sync will
    fail loudly on the NOT NULL constraint, which is the correct outcome:
    silently dropping a queued detection, or inventing an owner, would both be
    worse than a visible error.
    """
    if payload.get("user_id"):
        if payload["user_id"] != row["user_id"]:
            _set_row_owner(row["id"], payload["user_id"])
        return

    legacy = _legacy_owner()
    if not legacy:
        return
    payload["user_id"] = legacy
    _set_row_owner(row["id"], legacy)


def _set_row_owner(row_id: int, user_id: str) -> None:
    with _lock, _connect() as conn:
        conn.execute("UPDATE outbox SET user_id = ? WHERE id = ?", (user_id, row_id))


def _flush_detection(payload: Dict[str, Any], snapshot_file: Optional[str]) -> None:
    # Upload the snapshot FIRST so the detection row never points at a
    # Storage object that does not exist yet.
    if snapshot_file and payload.get("snapshot_path"):
        _upload_snapshot(Path(snapshot_file), payload["snapshot_path"])
    detections_repo.insert_detection(payload)


def _flush_alert(payload: Dict[str, Any]) -> None:
    # Rows are drained oldest-first and a detection is always enqueued
    # before its alert, so by the time an alert is flushed the referenced
    # detection already exists and can be resolved by its idempotency key.
    # This is what lets an alert survive being created while offline.
    client_event_id = payload.pop("pending_detection_client_event_id", None)
    if payload.get("detection_id") is None and client_event_id:
        # Scoped to the payload's own owner: `client_event_id` is unique per
        # user, not globally, so an unscoped lookup could bind this alert to
        # another account's detection.
        row = detections_repo.get_detection_by_client_event_id(
            client_event_id, user_id=payload.get("user_id")
        )
        if row:
            payload["detection_id"] = row.get("id")
    alerts_repo.insert_alert(payload)


def _upload_snapshot(local_path: Path, object_path: str) -> None:
    """Uploads one JPEG to the Supabase Storage `snapshots` bucket."""
    from app.storage import upload_snapshot_object

    upload_snapshot_object(local_path, object_path)


# ---------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------
class SyncWorker(threading.Thread):
    """
    Drains the outbox on a fixed interval. Started once from the app's
    startup hook and stopped on shutdown.
    """

    daemon = True

    def __init__(self, interval_seconds: Optional[int] = None):
        super().__init__(name="supabase-sync")
        self._stop = threading.Event()
        self._interval = interval_seconds or settings.SYNC_INTERVAL_SECONDS
        self.last_error: Optional[str] = None
        self.last_flush_mono: Optional[float] = None

    def stop(self):
        self._stop.set()

    def run(self):
        # Give startup logging a moment before the first attempt.
        self._stop.wait(3.0)
        while not self._stop.is_set():
            try:
                if sc.ping():
                    stats = flush()
                    self.last_flush_mono = time.monotonic()
                    self.last_error = None
                    if stats["flushed"]:
                        logger.info("Outbox sync drained %s event(s), %s remaining",
                                    stats["flushed"], stats["remaining"])
                else:
                    self.last_error = "Supabase unreachable"
                    pending = pending_count()
                    if pending:
                        logger.warning("Supabase unreachable - %s detection event(s) queued locally", pending)
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)[:200]
                logger.error("Outbox sync loop error: %s", exc)

            # Wake often enough that queued events reach the cloud
            # promptly after a reconnect, but not so often that an
            # offline machine spins.
            self._stop.wait(min(self._interval, 15))

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": settings.sync_enabled,
            "pending": pending_count(),
            # Keyed by owner so it is obvious whether a stuck queue belongs to
            # one user or is holding rows that predate owner tracking.
            "pending_by_owner": pending_by_owner(),
            "interval_seconds": self._interval,
            "last_flush_seconds_ago": (
                round(time.monotonic() - self.last_flush_mono, 1) if self.last_flush_mono else None
            ),
            "last_error": self.last_error,
        }
