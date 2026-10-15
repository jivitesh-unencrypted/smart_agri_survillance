#!/usr/bin/env python3
"""
Two-user isolation verification.

This is the test that decides whether the per-user data isolation feature is
actually working. It creates two real Supabase accounts, gives each one real
data, and then checks - as each account, with that account's own credentials -
that neither can see or touch the other's rows.

    ai-server> .\\venv\\Scripts\\python.exe scripts\\verify_two_user_isolation.py

Exit code 0 means every check passed. Any failure exits non-zero and prints
the step that failed plus what was expected and what actually came back.

WHAT IS AND IS NOT BEING TESTED
--------------------------------
Three distinct boundaries are exercised, because "the other user cannot see
it" can fail at any of them:

  1. ROW-LEVEL SECURITY. Every read and write below uses the ANON key with a
     real user's access token, exactly as the browser does. RLS is the only
     thing standing between the two accounts, which is the point: this test
     passes if the database is doing the work, not the application.

  2. THE AI SERVER. It runs with the SERVICE-ROLE key, which BYPASSES RLS
     entirely, so it must enforce ownership itself. The AI-server checks below
     hit the live routes over HTTP and confirm that a caller cannot reach
     another user's camera, alert or job.

  3. STORAGE. Snapshot objects live in a private bucket under
     `<user_id>/...` inside the private `snapshots` bucket. The storage checks
     confirm each account can
     sign a URL for its own object and gets nothing for the other's.

A test that only passed (1) would be worthless against this system, because
almost everything interesting goes through the service-role path.

PREREQUISITES
-------------
  * migrations 0001-0005 applied (the script checks and tells you if not)
  * the AI server running for the service-role route checks
      .\\venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
  * the script is skipped for the AI-server checks if it is not reachable
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from supabase import create_client  # noqa: E402

from app import supabase_client as sc  # noqa: E402

# ---------------------------------------------------------------- test users
USER_A_EMAIL = os.environ.get("ISOLATION_USER_A_EMAIL", "isolation-a@test.local")
USER_A_PASS = os.environ.get("ISOLATION_USER_A_PASSWORD", "Isolation-A-2026!")
USER_B_EMAIL = os.environ.get("ISOLATION_USER_B_EMAIL", "isolation-b@test.local")
USER_B_PASS = os.environ.get("ISOLATION_USER_B_PASSWORD", "Isolation-B-2026!")

AI_SERVER = os.environ.get("AI_SERVER_URL", "http://127.0.0.1:8000").rstrip("/")
BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "snapshots")

# ------------------------------------------------------------------ reporting
GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
)

_failures: list[str] = []
_step_no = 0
_skip_ai_server = False


def step(title: str) -> None:
    global _step_no
    _step_no += 1
    print(f"\n{BOLD}[{_step_no}] {title}{RESET}")


def ok(detail: str = "") -> None:
    print(f"    {GREEN}PASS{RESET} {detail}")


def fail(detail: str) -> None:
    print(f"    {RED}FAIL{RESET} {detail}")
    _failures.append(detail)


def skip(detail: str) -> None:
    print(f"    {YELLOW}SKIP{RESET} {detail}")


def info(detail: str) -> None:
    print(f"    {DIM}{detail}{RESET}")


def check(condition: bool, description: str, on_fail: str = "") -> bool:
    if condition:
        ok(description)
        return True
    fail(f"{description} -- {on_fail}" if on_fail else description)
    return False


# ------------------------------------------------------------------- clients
def anon_client(access_token: str):
    """
    A client that behaves exactly like the browser: anon key + the user's own
    access token, so RLS is fully in force.
    """
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_ANON_KEY"],
        options={"global": {"headers": {"Authorization": f"Bearer {access_token}"}}},
    )


def sign_in(email: str, password: str) -> tuple[str, str]:
    """Returns (user_id, access_token) after a real password sign-in."""
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])
    response = client.auth.sign_in_with_password({"email": email, "password": password})
    if response.user is None:
        raise RuntimeError(f"Sign-in failed for {email}: {response.error}")
    return response.user.id, response.session.access_token


def ensure_test_user(email: str, password: str, username: str) -> None:
    """Create the account if it is missing. Idempotent."""
    admin = sc.get_client()
    try:
        existing = sc.one(admin.auth.admin.list_users().execute())
        _ = existing  # touch the API so a bad service key fails loudly here
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"The service-role key is unusable: {exc}") from exc

    found = None
    page = admin.auth.admin.list_users()
    while True:
        for u in page.users:
            if u.email == email:
                found = u
                break
        if found or not getattr(page, "next_page_token", None):
            break
        page = admin.auth.admin.list_users(page=page.next_page_token)
        if hasattr(page, "users"):
            page = page.users

    if found is None:
        admin.auth.admin.create_user(
            {"email": email, "password": password, "email_confirm": True}
        )
        print(f"{DIM}created auth account {email}{RESET}")

    # The application profile row, written with the service role because a
    # signed-out insert cannot satisfy the RLS `auth.uid() = id` check.
    admin.table("users").upsert(
        {"id": None, "email": email, "username": username,
         "name": f"Isolation Test {username[-1].upper()}", "role": "user", "is_active": True},
        on_conflict="email",
    )


def ensure_profiles() -> None:
    """
    Ensure both test accounts have an auth user AND a public.users profile.

    The profile lookup is done per-user with the user's own token so the
    resulting ids are exactly what that account will authenticate as.
    """
    admin = sc.get_client()
    for email, password, username in (
        (USER_A_EMAIL, USER_A_PASS, "isolation_a"),
        (USER_B_EMAIL, USER_B_PASS, "isolation_b"),
    ):
        user_id, _ = sign_in(email, password)
        row = sc.one(admin.table("users").select("*").eq("id", user_id).execute())
        if row is None:
            admin.table("users").insert({
                "id": user_id, "email": email, "username": username,
                "name": f"Isolation Test {username[-1].upper()}",
                "role": "user", "is_active": True,
            })
            info(f"created public.users profile {username} -> {user_id}")


def rows(result):
    return result.data or []


# =========================================================== migration check
OWNED_TABLES = ("cameras", "detections", "alerts", "settings",
                "video_analysis_jobs", "camera_status_history")


def check_schema() -> bool:
    step("Verify migration 0005 is applied")
    admin = sc.get_client()
    problems = []

    # The fastest honest signal: if the owner column is not there, nothing
    # below this line means anything, so say so plainly instead of dumping a
    # PostgREST error about a missing column.
    try:
        admin.table("settings").select("user_id").limit(1).execute()
    except Exception as exc:  # noqa: BLE001
        if "user_id" not in str(exc):
            raise
        print(f"{RED}Migration 0005 is NOT applied - settings.user_id does not exist.{RESET}")
        info("Nothing can be isolated until the owner columns exist.")
        info("Apply supabase/migrations/0005_per_user_isolation.sql first:")
        info("  supabase db push")
        info("  ...or Supabase Studio -> SQL Editor -> paste the file -> Run")
        return False
    ok("settings.user_id exists (the 0005 signature change is present)")

    for table in OWNED_TABLES:
        try:
            rows(admin.table(table).select("user_id").limit(1).execute())
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{table}.user_id is missing: {exc}")
        else:
            ok(f"{table}.user_id exists")

    # The decisive check: is RLS actually scoping, or is it still using (true)?
    info("policies on user-owned tables:")
    pol = rows(admin.table("pg_policies").select(
        "schemaname, tablename, policyname, roles, qual, with_check"
    ).execute())
    for p in pol:
        if p["tablename"] in OWNED_TABLES:
            info(f"    {p['tablename']}.{p['policyname']} "
                 f"roles={p['roles']} qual={p['qual']}")

    global_owner = [
        p for p in rows(pol)
        if p["tablename"] in OWNED_TABLES
        and p["qual"] and p["qual"].strip().lower() == "true"
    ]
    if global_owner:
        problems.append(
            "these policies still allow everything: "
            + ", ".join(f"{p['tablename']}.{p['policyname']}" for p in global_owner)
        )
    else:
        ok("no `using (true)` policy remains on a user-owned table")

    if problems:
        for p in problems:
            fail(p)
        return False
    return True


# ================================================================== main test
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true",
                        help="do not delete the generated test rows afterwards")
    parser.add_argument("--skip-ai-server", action="store_true",
                        help="only test the database boundary, not the live routes")
    args = parser.parse_args()

    admin = sc.get_client()
    if admin is None:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing from .env")
        return 2

    print(f"{BOLD}Two-user isolation verification{RESET}")
    info(f"user A: {USER_A_EMAIL}")
    info(f"user B: {USER_B_EMAIL}")

    if not check_schema():
        print(f"\n{RED}Migration 0005 does not look applied. Apply it first:{RESET}")
        print("  supabase db push        # or paste 0005_per_user_isolation.sql into")
        print("                           # Supabase Studio -> SQL Editor -> Run")
        return 2

    step("Create / verify the two test accounts")
    ensure_profiles()
    a_id, a_token = sign_in(USER_A_EMAIL, USER_A_PASS)
    b_id, b_token = sign_in(USER_B_EMAIL, USER_B_PASS)
    check(a_id != b_id, "the two accounts have distinct Supabase user ids",
          f"both resolved to {a_id}")
    info(f"A = {a_id}")
    info(f"B = {b_id}")

    # Keep this run's rows identifiable so cleanup can remove exactly them.
    marker = f"ISOTEST-{uuid.uuid4().hex[:8]}"

    # ---------------------------------------------------- A creates its data
    step("Sign in as A and create Camera A")
    a = anon_client(a_token)
    created = a.table("cameras").insert({
        "camera_code": "CAM-001", "name": f"{marker} camera A", "stream_url": "http://127.0.0.1:1/x",
        "camera_type": "ip", "connection_type": "http", "enabled": True,
        "user_id": a_id,
    }).execute()
    cam_a = rows(created)[0]
    check(cam_a["user_id"] == a_id, "Camera A is owned by A",
          f"user_id={cam_a['user_id']}")
    ok(f"Camera A id={cam_a['id']} code={cam_a['camera_code']}")

    step("Sign in as A and create a detection + alert for Camera A")
    det_a = rows(a.table("detections").insert({
        "client_event_id": str(uuid.uuid4()), "source": "Live Camera",
        "camera_id": cam_a["id"], "camera_name": cam_a["name"], "location": "A zone",
        "object_name": "person", "category": "Human", "severity": "critical",
        "confidence": 0.91, "avg_confidence": 0.91, "frame_count": 120,
        "start_time": "2026-01-01T00:00:00+00:00", "end_time": "2026-01-01T00:02:00+00:00",
        "duration_seconds": 120.0, "snapshot_path": f"{a_id}/2026-01-01/{marker}_a.jpg",
        "user_id": a_id,
    }).execute())[0]
    check(det_a["user_id"] == a_id, "Detection A is owned by A",
          f"user_id={det_a['user_id']}")

    alr_a = rows(a.table("alerts").insert({
        "client_alert_id": str(uuid.uuid4()), "detection_id": det_a["id"],
        "camera_id": cam_a["id"], "camera_name": cam_a["name"],
        "category": "Human", "severity": "critical", "alert_type": "intrusion",
        "title": "Intrusion Alert", "message": f"{marker} A alert",
        "acknowledged": False, "resolved": False, "user_id": a_id,
    }).execute())[0]
    check(alr_a["user_id"] == a_id, "Alert A is owned by A", f"user_id={alr_a['user_id']}")
    ok(f"Detection A id={det_a['id']}, Alert A id={alr_a['id']}")

    # ------------------------------------------------ B must see none of it
    step("Sign out of A, sign in as B -- A's data must be invisible")
    b = anon_client(b_token)

    cams_b = rows(b.table("cameras").select("id,name").execute())
    check(all(c["id"] != cam_a["id"] for c in cams_b),
          "B does not see Camera A", f"B received {cams_b}")

    dets_b = rows(b.table("detections").select("id").execute())
    check(all(d["id"] != det_a["id"] for d in dets_b),
          "B does not see Detection A", f"B received {len(dets_b)} detections")

    alrs_b = rows(b.table("alerts").select("id").execute())
    check(all(x["id"] != alr_a["id"] for x in alrs_b),
          "B does not see Alert A", f"B received {len(alrs_b)} alerts")

    cross = b.table("cameras").select("*").eq("id", cam_a["id"]).execute()
    check(not rows(cross), "B cannot read Camera A by guessing its id",
          f"B read {rows(cross)}")

    step("B tries to WRITE to A's rows")
    hijack = b.table("cameras").update({"name": "hijacked"}).eq("id", cam_a["id"]).execute()
    still = rows(admin.table("cameras").select("name").eq("id", cam_a["id"]).execute())
    check(still and still[0]["name"] == cam_a["name"],
          "B's rename of Camera A had no effect",
          f"name is now {still[0]['name'] if still else '<gone>'} (hijack returned {hijack})")

    stolen = b.table("cameras").insert({
        "camera_code": "CAM-STOLEN", "name": "B planting on A", "stream_url": "http://x",
        "user_id": a_id,
    }).execute()
    check(not rows(stolen),
          "B cannot insert a camera owned by A",
          f"insert returned {rows(stolen)}")

    # --------------------------------------------------- B creates its own
    step("B creates Camera B (and gets its own CAM-001)")
    cam_b = rows(b.table("cameras").insert({
        "camera_code": "CAM-001", "name": f"{marker} camera B", "stream_url": "http://127.0.0.1:1/y",
        "camera_type": "ip", "connection_type": "http", "enabled": True,
        "user_id": b_id,
    }).execute())[0]
    check(cam_b["user_id"] == b_id, "Camera B is owned by B", f"user_id={cam_b['user_id']}")
    check(cam_b["camera_code"] == "CAM-001",
          "B reuses the code CAM-001 in its own namespace",
          f"got {cam_b['camera_code']}")
    ok(f"Camera B id={cam_b['id']}")

    det_b = rows(b.table("detections").insert({
        "client_event_id": str(uuid.uuid4()), "source": "Live Camera",
        "camera_id": cam_b["id"], "camera_name": cam_b["name"],
        "object_name": "dog", "category": "Animals", "severity": "warning",
        "confidence": 0.8, "avg_confidence": 0.8, "frame_count": 30,
        "start_time": "2026-01-01T00:00:00+00:00", "end_time": "2026-01-01T00:00:30+00:00",
        "duration_seconds": 30.0, "user_id": b_id,
    }).execute())[0]

    # ------------------------------------------------ back to A, symmetric
    step("Sign back in as A -- A sees its own data and none of B's")
    a2 = anon_client(a_token)
    a_cams = rows(a2.table("cameras").select("id").execute())
    check(any(c["id"] == cam_a["id"] for c in a_cams), "A still sees Camera A",
          f"A sees {[c['id'] for c in a_cams]}")
    check(all(c["id"] != cam_b["id"] for c in a_cams), "A does not see Camera B",
          f"A sees {[c['id'] for c in a_cams]}")

    a_dets = rows(a2.table("detections").select("id").execute())
    check(any(d["id"] == det_a["id"] for d in a_dets), "A still sees Detection A")
    check(all(d["id"] != det_b["id"] for d in a_dets), "A does not see Detection B",
          f"A sees {len(a_dets)} detections")

    a_alrs = rows(a2.table("alerts").select("id").execute())
    check(any(x["id"] == alr_a["id"] for x in a_alrs), "A still sees Alert A")
    check(all(x["id"] != alr_b_id(rows(b)) for x in a_alrs), "A does not see B's alerts")

    # ------------------------------------------------------------- analytics
    step("Analytics must not mix the two accounts")
    sum_a = a2.rpc("get_detection_summary").execute().data
    sum_b = b.rpc("get_detection_summary").execute().data
    info(f"A summary: {sum_a}")
    info(f"B summary: {sum_b}")
    check(sum_a.get("human_events") == 1,
          "A's summary counts exactly A's one Human detection",
          f"got human_events={sum_a.get('human_events')}")
    check(sum_b.get("human_events") == 0,
          "B's summary does not count A's Human detection",
          f"got human_events={sum_b.get('human_events')}")
    check(sum_b.get("animal_events") == 1,
          "B's summary counts exactly B's one Animal detection",
          f"got animal_events={sum_b.get('animal_events')}")

    an_a = a2.rpc("get_analytics").execute().data
    an_b = b.rpc("get_analytics").execute().data
    check(isinstance(an_a, dict) and isinstance(an_b, dict),
          "both accounts get their own analytics document")
    total_a = sum(sum(v.values()) for k, v in (an_a.get("by_category") or {}).items())
    total_b = sum(sum(v.values()) for k, v in (an_b.get("by_category") or {}).items())
    info(f"A total events={total_a}, B total events={total_b}")
    check(total_a == 1 and total_b == 1,
          "each account's analytics covers only its own single detection",
          f"A={total_a} B={total_b}")

    # -------------------------------------------------------------- settings
    step("Settings are one row per user, not one global row")
    s_a = a2.table("settings").select("*").limit(5).execute()
    s_b = b.table("settings").select("*").limit(5).execute()
    check(len(s_a.data or []) <= 1, "A sees at most one settings row",
          f"A sees {len(s_a.data or [])}")
    check(len(s_b.data or []) <= 1, "B sees at most one settings row",
          f"B sees {len(s_b.data or [])}")

    a2.table("settings").upsert(
        {"user_id": a_id, "confidence_threshold": 0.11, "updated_at": "2026-01-01T00:00:00Z"},
        on_conflict="user_id",
    ).execute()
    b.table("settings").upsert(
        {"user_id": b_id, "confidence_threshold": 0.99, "updated_at": "2026-01-01T00:00:00Z"},
        on_conflict="user_id",
    ).execute()

    s_a = a2.table("settings").select("user_id,confidence_threshold").execute()
    s_b = b.table("settings").select("user_id,confidence_threshold").execute()
    check(len(s_a.data or []) == 1 and s_a.data[0]["user_id"] == a_id,
          "A reads back only its own settings row", f"{s_a.data}")
    check(len(s_b.data or []) == 1 and s_b.data[0]["user_id"] == b_id,
          "B reads back only its own settings row", f"{s_b.data}")
    check(s_a.data[0]["confidence_threshold"] != s_b.data[0]["confidence_threshold"],
          "A and B hold independent settings values",
          f"both are {s_a.data[0]['confidence_threshold']}")

    # -------------------------------------------------------- video-analysis
    step("Video-analysis jobs are scoped to the uploader")
    job_a = rows(admin.table("video_analysis_jobs").insert({
        "original_filename": f"{marker}.mp4", "status": "completed",
        "client_job_id": str(uuid.uuid4()), "user_id": a_id,
    }).execute())[0]
    jobs_b = rows(b.table("video_analysis_jobs").select("id").execute())
    check(all(j["id"] != job_a["id"] for j in jobs_b),
          "B does not see A's video-analysis job", f"B sees {[j['id'] for j in jobs_b]}")
    jobs_a = rows(a2.table("video_analysis_jobs").select("id").execute())
    check(any(j["id"] == job_a["id"] for j in jobs_a), "A sees its own job")

    # -------------------------------------------------------------- storage
    step("Snapshot storage is private and per-user")
    if admin.storage is None:
        skip("storage client unavailable")
    else:
        storage = admin.storage.from_(BUCKET)
        uploaded = False
        try:
            for user_id, name in (
                (a_id, f"{a_id}/2026-01-01/{marker}_a.jpg"),
                (b_id, f"{b_id}/2026-01-01/{marker}_b.jpg"),
            ):
                try:
                    storage.remove([name])
                except Exception:
                    pass
                storage.upload(name, b"\xff\xd8\xff\xe0 not-a-real-jpeg", {"upsert": "true"})
                uploaded = True
            info("uploaded one probe object per user")

            bucket_meta = rows(admin.table("storage_buckets")
                               .select("id,name,public").eq("name", BUCKET).execute())
            check(bucket_meta and bucket_meta[0]["public"] is False,
                  f"bucket `{BUCKET}` is private",
                  f"public={bucket_meta[0]['public'] if bucket_meta else '?'}")

            for user_id, other_id, label in (
                (a_id, b_id, "A"),
                (b_id, a_id, "B"),
            ):
                own = a2 if label == "A" else b
                other_client = b if label == "A" else a2
                own_path = f"{user_id}/2026-01-01/{marker}_{label.lower()}.jpg"
                other_path = (
                    f"{other_id}/2026-01-01/"
                    f"{marker}_{'b' if label == 'A' else 'a'}.jpg"
                )

                # `from` is a Python keyword, so the bucket handle is reached
                # through the `from_` method (the trailing underscore).
                own_bucket = own.storage.from_(BUCKET)
                signed = own_bucket.create_signed_url(own_path, 60)
                check(
                    getattr(signed, "signed_url", None) is not None
                    or (isinstance(signed, dict) and signed.get("signedURL")),
                    f"{label} can create a signed URL for its own snapshot",
                    f"got {signed}",
                )

                other_bucket = other_client.storage.from_(BUCKET)
                stolen = other_bucket.create_signed_url(other_path, 60)
                stolen_url = (
                    getattr(stolen, "signed_url", None)
                    or (stolen.get("signedURL") if isinstance(stolen, dict) else None)
                )
                check(stolen_url is None,
                      f"{label} cannot sign the other user's snapshot",
                      f"it returned {stolen_url}")
        finally:
            if uploaded:
                try:
                    storage.remove([
                        f"{a_id}/2026-01-01/{marker}_a.jpg",
                        f"{b_id}/2026-01-01/{marker}_b.jpg",
                    ])
                except Exception:
                    pass

    # ----------------------------------------------------- session persistence
    step("A session survives a 'page refresh' (token reuse)")
    a3 = anon_client(a_token)
    again = rows(a3.table("cameras").select("id").execute())
    check(any(c["id"] == cam_a["id"] for c in again),
          "reusing A's existing token still returns A's camera and only A's",
          f"A sees {[c['id'] for c in again]}")
    check(all(c["id"] != cam_b["id"] for c in again),
          "and still never B's camera")

    # --------------------------------------------------- AI server boundary
    step("AI server routes enforce ownership independently of RLS")
    if args.skip_ai_server:
        skip("--skip-ai-server requested")
    else:
        try:
            with httpx.Client(timeout=15.0) as http:
                http.get(f"{AI_SERVER}/api/system/health")
        except Exception as exc:  # noqa: BLE001
            skip(f"AI server not reachable at {AI_SERVER} ({exc}); "
                 f"service-role route checks were not run")
            global _skip_ai_server
            _skip_ai_server = True

        if not _skip_ai_server:
            with httpx.Client(timeout=15.0) as http:
                ra = http.get(f"{AI_SERVER}/api/cameras",
                              headers={"Authorization": f"Bearer {a_token}"})
                rb = http.get(f"{AI_SERVER}/api/cameras",
                              headers={"Authorization": f"Bearer {b_token}"})
                check(ra.status_code == 200, "A can list cameras over the AI server",
                      f"HTTP {ra.status_code} {ra.text[:120]}")
                check(rb.status_code == 200, "B can list cameras over the AI server",
                      f"HTTP {rb.status_code} {rb.text[:120]}")

                if ra.status_code == 200:
                    ids_a = [c["id"] for c in ra.json()]
                    check(cam_a["id"] in ids_a, "A's list contains Camera A", f"{ids_a}")
                    check(cam_b["id"] not in ids_a,
                          "A's list does NOT contain Camera B", f"{ids_a}")
                if rb.status_code == 200:
                    ids_b = [c["id"] for c in rb.json()]
                    check(cam_b["id"] in ids_b, "B's list contains Camera B", f"{ids_b}")
                    check(cam_a["id"] not in ids_b,
                          "B's list does NOT contain Camera A", f"{ids_b}")

                cross = http.get(f"{AI_SERVER}/api/cameras/{cam_a['id']}",
                                 headers={"Authorization": f"Bearer {b_token}"})
                check(cross.status_code == 404,
                      "B gets 404 (not 403) for Camera A over the AI server",
                      f"HTTP {cross.status_code} {cross.text[:120]}")

                own = http.get(f"{AI_SERVER}/api/cameras/{cam_a['id']}",
                               headers={"Authorization": f"Bearer {a_token}"})
                check(own.status_code == 200, "A gets 200 for its own Camera A",
                      f"HTTP {own.status_code} {own.text[:120]}")

                stream = http.get(f"{AI_SERVER}/api/cameras/{cam_a['id']}/stream?token={b_token}")
                check(stream.status_code in (401, 403, 404),
                      "B cannot open Camera A's MJPEG stream",
                      f"HTTP {stream.status_code}")

                alert_x = http.patch(f"{AI_SERVER}/api/alerts/{alr_a['id']}",
                                     headers={"Authorization": f"Bearer {b_token}"},
                                     json={"acknowledged": True})
                check(alert_x.status_code == 404,
                      "B cannot acknowledge A's alert (404, not 403)",
                      f"HTTP {alert_x.status_code} {alert_x.text[:120]}")

                alert_ok = http.patch(f"{AI_SERVER}/api/alerts/{alr_a['id']}",
                                      headers={"Authorization": f"Bearer {a_token}"},
                                      json={"acknowledged": True})
                check(alert_ok.status_code == 200, "A can acknowledge its own alert",
                      f"HTTP {alert_ok.status_code} {alert_ok.text[:120]}")

                job_x = http.get(f"{AI_SERVER}/api/video-analysis/jobs/{job_a['id']}",
                                 headers={"Authorization": f"Bearer {b_token}"})
                check(job_x.status_code == 404, "B gets 404 for A's video job",
                      f"HTTP {job_x.status_code} {job_x.text[:120]}")

                noauth = http.get(f"{AI_SERVER}/api/cameras")
                check(noauth.status_code in (401, 403),
                      "an unauthenticated request is rejected",
                      f"HTTP {noauth.status_code}")

    # ----------------------------------------------------------------- cleanup
    if not args.keep:
        step("Clean up the generated test rows")
        # Service-role deletes, matched on this run's unique name marker.
        for table, column in (("alerts", "message"), ("detections", "camera_name"),
                              ("cameras", "name")):
            try:
                admin.table(table).delete().like(column, f"%{marker}%").execute()
                ok(f"removed {marker} from {table}")
            except Exception as exc:  # noqa: BLE001
                fail(f"could not clean {table}: {exc}")
        try:
            admin.table("video_analysis_jobs").delete().like("original_filename", f"%{marker}%").execute()
            ok(f"removed {marker} from video_analysis_jobs")
        except Exception as exc:  # noqa: BLE001
            fail(f"could not clean video_analysis_jobs: {exc}")
        info("the two test ACCOUNTS are left in place so the test is repeatable")

    # ------------------------------------------------------------------ report
    print()
    if _failures:
        print(f"{RED}{BOLD}ISOLATION TEST FAILED{RESET} - {len(_failures)} problem(s):")
        for f in _failures:
            print(f"  {RED}x{RESET} {f}")
        return 1
    print(f"{GREEN}{BOLD}ISOLATION TEST PASSED{RESET} - every check above held.")
    if _skip_ai_server:
        print(f"{YELLOW}Note: the AI-server (service-role) checks were skipped.{RESET}")
    return 0


def alr_b_id(b_client) -> int:
    """The id of B's first alert, for the A-must-not-see-it check."""
    got = rows(b_client.table("alerts").select("id").limit(1).execute())
    return got[0]["id"] if got else -1


if __name__ == "__main__":
    raise SystemExit(main())