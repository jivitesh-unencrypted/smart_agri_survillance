"""
Authentication routes (Supabase-backed).

The frontend authenticates directly against Supabase Auth
(`signInWithPassword` / `signUp`), exactly like Supabase's own client
examples. This router therefore no longer issues or verifies passwords -
it exists so the existing frontend contract keeps working unchanged:

  GET  /api/auth/me            -> current user profile
  POST /api/auth/register      -> creates the app profile for a user that
                                  has just signed up via Supabase Auth
  POST /api/auth/login         -> 410, with a pointer to the new flow
  POST /api/auth/logout        -> acknowledged (real sign-out is client-side)

Keeping `/login` as an explicit 410 rather than silently removing it means
an old frontend build fails loudly with an explanation instead of a
mysterious 404.
"""
from fastapi import APIRouter, Depends, HTTPException

from app import supabase_client as sc
from app.api.deps import current_user_out, require_authenticated_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
def me(user: dict = Depends(require_authenticated_user)):
    return current_user_out(user)


@router.post("/register")
def register(user: dict = Depends(require_authenticated_user)):
    """
    Ensures an application profile exists for the authenticated Supabase
    user. Supabase Auth has already verified the email/password by the
    time this is reached, so there is nothing left to hash or store here.

    Kept for backwards compatibility with the previous frontend, which
    called this immediately after signing up.
    """
    return current_user_out(user)


@router.post("/login", status_code=410)
def login_removed():
    raise HTTPException(
        status_code=410,
        detail=(
            "Password login through the AI server has been removed - authentication is now "
            "handled by Supabase Auth. Sign in from the frontend with the Supabase client."
        ),
    )


@router.post("/logout")
def logout(user: dict = Depends(require_authenticated_user)):
    """
    Acknowledges a logout. The access token itself is a short-lived,
    client-held Supabase JWT; the frontend clears it via
    `supabase.auth.signOut()`. No server-side session store exists, which
    is exactly why no cloud session persistence is needed.
    """
    return {"status": "logged_out"}
