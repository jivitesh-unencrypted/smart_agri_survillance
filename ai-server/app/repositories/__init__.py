"""
Supabase repositories.

These modules are the ONLY place that touches Postgres. They replace the
original SQLAlchemy `db.query(...)` layer one-for-one: same entities,
same columns, same filters, so the API routes above them and the
frontend above that did not have to change.

Offline behaviour: every function goes through
`supabase_client.call`, so any connectivity problem raises
`SupabaseUnavailable`. Detection writes additionally support an
idempotency key (`client_event_id` / `client_alert_id`), which is what
lets the offline outbox replay buffered events after the internet comes
back without ever creating duplicates.
"""
from app.repositories import (  # noqa: F401
    alerts,
    analytics,
    cameras,
    detections,
    jobs,
    settings,
    users,
)

__all__ = ["alerts", "analytics", "cameras", "detections", "jobs", "settings", "users"]
