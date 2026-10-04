"""
FastAPI dependencies.

Authentication now runs through Supabase Auth instead of the local
JWT/password tables:

  * The browser signs in with `@supabase/supabase-js` and sends the
    resulting Supabase access token as `Authorization: Bearer <token>`.
  * This server verifies that token against Supabase Auth and loads the
    matching application profile row (role, is_active).
  * A disabled or unknown account is rejected, exactly as the old
    `get_current_user` did for a missing/disabled row.

`require_authenticated_user` additionally requires a user, which is what
protects camera management (including credentials), start/stop, test and
video analysis. These stay server-side on purpose - the frontend only
ever holds the anon key.
"""
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import supabase_client as sc
from app.repositories import users as users_repo

bearer_scheme = HTTPBearer(auto_error=False)

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_access_token(request: Request) -> str | None:
    """
    Reads the bearer token.

    `Authorization` is the normal path. A `?token=` query parameter is
    also accepted because browsers cannot attach headers to an `<img src>`
    request - that is how the live MJPEG stream stays authenticated.
    """
    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip()
    return request.query_params.get("token")


async def require_authenticated_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    request: Request = None,
) -> dict:
    """Returns the Supabase Auth user + app profile, or raises 401/403."""
    token = None
    if credentials is not None:
        token = credentials.credentials
    elif request is not None:
        token = get_access_token(request)

    if not token:
        raise _CREDENTIALS_ERROR

    auth_user = sc.verify_access_token(token)
    if auth_user is None:
        raise _CREDENTIALS_ERROR

    # A profile may legitimately not exist yet (e.g. an account created
    # directly in the Supabase dashboard), so create it on first sight.
    profile = sc.ensure_user_profile(auth_user["id"], auth_user.get("email"), auth_user.get("username"))

    if not profile.get("is_active", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    sc.touch_last_seen(auth_user["id"])

    return {
        "id": auth_user["id"],
        "email": auth_user.get("email"),
        "username": profile.get("username") or auth_user.get("username"),
        "name": profile.get("name"),
        "role": profile.get("role", "user"),
        "is_active": True,
    }


def current_user_out(user: dict) -> dict:
    """Shape returned by GET /api/auth/me (unchanged from the original)."""
    return {
        "id": user["id"],
        "username": user.get("username"),
        "name": user.get("name"),
        "email": user.get("email"),
        "role": user.get("role", "user"),
        "is_active": user.get("is_active", True),
    }
