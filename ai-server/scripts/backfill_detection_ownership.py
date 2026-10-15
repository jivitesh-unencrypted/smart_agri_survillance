#!/usr/bin/env python3
"""
Re-attribute detections and alerts that the owner-resolution bug left
unowned (or attributed to the wrong account).

WHY THIS EXISTS
---------------
`_common.select_one()` used to send `user_id=eq.null` whenever an optional
owner filter was omitted. PostgREST rejects that on a `uuid` column, so
`jobs_repo.get_job(job_id)` always raised inside the video-analysis worker, the
`except SupabaseUnavailable` branch swallowed it, and every detection and alert
that run produced was written with `user_id IS NULL`.

An unowned row is invisible to EVERY signed-in user, because the RLS policy is

    auth.uid() = user_id

which is NULL - and therefore not true - for a NULL owner. The symptom is the
one this repairs: the video-analysis job reports `event_count > 0` while the
Detections page shows zero.

A second, worse failure came out of the offline outbox: a queued row with no
owner was stamped with the *legacy* owner (the earliest-created profile) when it
was flushed, so some rows ended up attributed to an account that never produced
them. Those are re-attributed here too.

WHAT IT DOES
------------
Ownership is derivable, so nothing is guessed:

  * a `Video Analysis` detection belongs to its `video_analysis_jobs.user_id`
  * a `Live Camera` detection belongs to its `cameras.user_id`
  * an alert belongs to its detection's owner

For each row whose owner is NULL, or whose owner disagrees with the parent
row, the parent's owner is written. Rows whose parent cannot be resolved are
reported and left alone - they are not fabricated an owner.

USAGE
-----
    cd ai-server
    .\\venv\\Scripts\\python.exe scripts\\backfill_detection_ownership.py            # dry run
    .\\venv\\Scripts\\python.exe scripts\\backfill_detection_ownership.py --apply    # write

Run this BEFORE `migrate_legacy_snapshots.py`: relocating a snapshot needs to
know which account owns it.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Allow running as a plain script from the ai-server directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app import supabase_client as sc  # noqa: E402

PAGE = 1000

# Uploaded analysis videos are stored as `job<job_id>_<hex>.<ext>` and the
# detection/alert rows copy that filename into `camera_name`, so the job id is
# recoverable from an alert that lost its `detection_id` on the way through the
# offline outbox.
_UPLOAD_NAME = re.compile(r"^job(\d+)_")


def _fetch_all(client, table: str, columns: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    start = 0
    while True:
        page = sc.rows(
            client.table(table).select(columns).range(start, start + PAGE - 1).execute()
        )
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        start += PAGE


def detection_owner(det: Dict[str, Any], job_owner: Dict[Any, Optional[str]],
                    cam_owner: Dict[Any, Optional[str]]) -> tuple[Optional[str], str]:
    """(owner, explanation) for one detection, derived from its parent row."""
    source = det.get("source")
    if source == "Video Analysis" or det.get("video_analysis_job_id") is not None:
        job_id = det.get("video_analysis_job_id")
        return job_owner.get(job_id), f"video_analysis_jobs#{job_id}"
    cam_id = det.get("camera_id")
    return cam_owner.get(cam_id), f"cameras#{cam_id}"


def build_plan(client) -> tuple[List[Dict[str, Any]], List[str]]:
    """Returns (planned changes, unresolved notes)."""
    jobs = _fetch_all(client, "video_analysis_jobs", "id,user_id")
    cameras = _fetch_all(client, "cameras", "id,user_id")
    detections = _fetch_all(
        client, "detections", "id,source,camera_id,video_analysis_job_id,user_id"
    )
    alerts = _fetch_all(client, "alerts", "id,detection_id,user_id,camera_name")

    job_owner = {j["id"]: j.get("user_id") for j in jobs}
    cam_owner = {c["id"]: c.get("user_id") for c in cameras}

    plan: List[Dict[str, Any]] = []
    unresolved: List[str] = []

    det_owner_by_id: Dict[Any, Optional[str]] = {}
    for det in detections:
        owner, parent = detection_owner(det, job_owner, cam_owner)
        det_owner_by_id[det["id"]] = owner
        if owner is None:
            unresolved.append(
                f"detection id={det['id']} ({det.get('source')}): parent {parent} "
                f"has no owner"
            )
            continue
        if det.get("user_id") != owner:
            plan.append({
                "table": "detections", "id": det["id"],
                "from": det.get("user_id"), "to": owner, "why": parent,
            })

    for alert in alerts:
        det_id = alert.get("detection_id")
        if det_id is not None:
            owner = det_owner_by_id.get(det_id)
            why = f"detections#{det_id}"
            if owner is None:
                unresolved.append(
                    f"alert id={alert['id']}: detection {det_id} has no resolvable owner"
                )
                continue
        else:
            # No detection link: the alert was queued while offline and the
            # detection it belongs to was never resolved on replay. An uploaded
            # video's filename does identify the job unambiguously, so that is
            # used when available - otherwise the alert is reported, never
            # guessed at from a camera name that several accounts may share.
            match = _UPLOAD_NAME.match(alert.get("camera_name") or "")
            owner = job_owner.get(int(match.group(1))) if match else None
            why = f"camera_name {alert.get('camera_name')!r} -> video_analysis_job"
            if owner is None:
                unresolved.append(
                    f"alert id={alert['id']}: no detection_id and no job id in "
                    f"camera_name {alert.get('camera_name')!r}"
                )
                continue
        if alert.get("user_id") != owner:
            plan.append({
                "table": "alerts", "id": alert["id"],
                "from": alert.get("user_id"), "to": owner, "why": why,
            })

    return plan, unresolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args()

    client = sc.get_client()
    if client is None:
        print("Supabase is not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing from .env)")
        return 1

    plan, unresolved = build_plan(client)

    if not plan and not unresolved:
        print("Nothing to repair - every detection and alert already has the right owner.")
        return 0

    by_table: Dict[str, int] = {}
    for item in plan:
        by_table[item["table"]] = by_table.get(item["table"], 0) + 1

    print(f"rows to re-attribute: {len(plan)}  ({by_table})")
    for item in plan[:20]:
        print(f"  {item['table']} id={item['id']}: "
              f"{(item['from'] or 'NULL')[:8]} -> {item['to'][:8]}  ({item['why']})")
    if len(plan) > 20:
        print(f"  ... and {len(plan) - 20} more")

    if unresolved:
        print(f"\nunresolved (left untouched): {len(unresolved)}")
        for note in unresolved[:10]:
            print(f"  {note}")
        if len(unresolved) > 10:
            print(f"  ... and {len(unresolved) - 10} more")

    if not args.apply:
        print("\nDry run complete - nothing was written. Re-run with --apply to write.")
        return 0

    updated = failed = 0
    for item in plan:
        try:
            client.table(item["table"]).update({"user_id": item["to"]}).eq("id", item["id"]).execute()
            updated += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED {item['table']} id={item['id']}: {exc}")
            failed += 1

    print(f"\nupdated={updated} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
