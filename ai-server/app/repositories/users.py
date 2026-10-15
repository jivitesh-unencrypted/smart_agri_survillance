"""User profile repository.

Passwords are owned exclusively by Supabase Auth. This repository only
touches the application profile table (username, display name, role,
active flag), so the AI server never stores or hashes a password.
"""
from typing import Any, Dict, List, Optional

from app import supabase_client as sc
from app.repositories._common import select_one

# `username` -> `email` resolution keeps the existing login screen (which
# asks for a Username) working unchanged on top of Supabase Auth.
USERNAME_PATTERN = r"^[a-zA-Z0-9_.-]+$"


def get_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    return select_one("users", id=user_id)


def get_by_username(username: str) -> Optional[Dict[str, Any]]:
    return select_one("users", username=username)


def get_by_email(email: str) -> Optional[Dict[str, Any]]:
    if not email:
        return None
    return select_one("users", email=email.lower())


def create_profile(
    user_id: str,
    email: Optional[str],
    username: str,
    name: Optional[str] = None,
    role: str = "user",
) -> Dict[str, Any]:
    payload = {
        "id": user_id,
        "email": (email or "").lower() or None,
        "username": username,
        "name": name or username,
        "role": role,
        "is_active": True,
    }
    client = sc.get_client()
    row = sc.one(client.table("users").upsert(payload, on_conflict="id").execute())
    if row is None:
        raise sc.SupabaseUnavailable("Insert into users returned no row")
    return row


def list_profiles(limit: int = 200) -> List[Dict[str, Any]]:
    def _run(client):
        return sc.rows(client.table("users").select("*").order("username").limit(limit).execute())

    return sc.call(_run)


def oldest_profile_id() -> Optional[str]:
    """
    The id of the earliest-created application profile.

    Used for exactly one thing: giving ownership to rows that predate the
    owner columns, which is the same deterministic rule
    `supabase/migrations/0005_per_user_isolation.sql` applied to the rows
    already in Postgres. It is NOT a substitute for an owner - nothing in
    the normal detection path calls it, because every new camera, detection,
    alert and job already carries the authenticated owner's uuid.
    """
    def _run(client):
        rows = sc.rows(
            client.table("users")
            .select("id")
            .order("created_at", desc=False)
            .order("id", desc=False)
            .limit(1)
            .execute()
        )
        return rows[0]["id"] if rows else None

    return sc.call(_run)
