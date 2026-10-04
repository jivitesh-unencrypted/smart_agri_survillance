"""Shared helpers for the Supabase repositories."""
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import PurePath
from typing import Any, Dict, List, Optional
from uuid import UUID

from app import supabase_client as sc

logger = logging.getLogger("ai_server.repo")


def utc_now_iso() -> str:
    """Timestamp string Postgres `timestamptz` parses unambiguously."""
    return datetime.now(timezone.utc).isoformat()


def jsonable(value: Any) -> Any:
    """
    Converts a payload value into something PostgREST can serialise.

    This is required because the detection service hands us real `datetime`
    objects (it computes start/end times from `datetime.now(timezone.utc)`),
    and `httpx` serialises the request body with plain `json.dumps`, which
    has no idea what a datetime is. Without this every live detection insert
    would fail with "Object of type datetime is not JSON serializable" and
    silently fall back to the offline outbox - which looks like it works,
    but only ever syncs after a reconnect.

    Naive datetimes are assumed to be UTC, matching the original app's
    convention (its SQLite columns were all naive UTC, see the removed
    `UTCDateTime` TypeDecorator).
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (UUID, PurePath)):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    return value


def jsonable_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """`jsonable` applied across a whole row, dropping None values."""
    return {k: jsonable(v) for k, v in payload.items() if v is not None}


def is_unique_violation(exc: Exception) -> bool:
    """PostgREST surfaces a 23505 unique violation for duplicate keys."""
    text = str(exc)
    return "23505" in text or "duplicate key" in text.lower()


def idempotent_insert(
    table: str,
    payload: Dict[str, Any],
    key_col: str,
) -> Optional[Dict[str, Any]]:
    """
    INSERT with "do nothing on conflict" semantics.

    `payload[key_col]` must carry a client-generated UUID. If the row is
    already in Postgres (an outbox replay after a reconnect) the existing
    row is returned instead of raising, which is what makes retrying a
    buffered event safe.
    """
    client = sc.get_client()
    key_value = payload.get(key_col)
    payload = jsonable_payload(payload)

    if client is None or not key_value:
        return sc.insert_returning(client, table, payload)

    def _fetch_existing():
        return sc.one(client.table(table).select("*").eq(key_col, key_value).limit(1).execute())

    try:
        row = sc.one(client.table(table).insert(payload).execute())
        if row is not None:
            return row
    except Exception as exc:  # noqa: BLE001
        if is_unique_violation(exc):
            logger.debug("Idempotent replay hit on %s.%s - reusing existing row", table, key_col)
            return sc.call(_fetch_existing)
        sc._mark_failed(exc)
        raise sc.SupabaseUnavailable(str(exc)) from exc

    # A conflict normally returns no rows; look the row up instead.
    return sc.call(_fetch_existing)


def select_one(table: str, **filters) -> Optional[Dict[str, Any]]:
    def _run(client):
        q = client.table(table).select("*")
        for col, value in filters.items():
            q = q.eq(col, value)
        return sc.one(q.limit(1).execute())

    return sc.call(_run)


def select_many(
    table: str,
    order_column: Optional[str] = None,
    descending: bool = True,
    limit: Optional[int] = None,
    **filters,
) -> List[Dict[str, Any]]:
    def _run(client):
        q = client.table(table).select("*")
        for col, value in filters.items():
            if value is None:
                continue
            q = q.eq(col, value)
        if order_column:
            q = q.order(order_column, desc=descending)
        if limit is not None:
            q = q.limit(limit)
        return sc.rows(q.execute())

    return sc.call(_run)


def update_row(table: str, row_id: Any, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    def _run(client):
        # Nones are kept here (unlike inserts) because clearing a column -
        # e.g. `last_error = None`, or detaching a snapshot path - is a
        # meaningful update.
        return sc.one(
            client.table(table).update(jsonable(updates)).eq("id", row_id).execute()
        )

    return sc.call(_run)


def upsert_row(table: str, payload: Dict[str, Any], on_conflict: str) -> Optional[Dict[str, Any]]:
    return sc.upsert_returning(sc.get_client(), table, jsonable_payload(payload), on_conflict)
