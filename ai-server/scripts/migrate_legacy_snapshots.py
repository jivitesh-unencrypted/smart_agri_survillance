#!/usr/bin/env python3
"""
Move legacy detection snapshots into their owner's Storage folder.

WHY THIS EXISTS
---------------
Detection snapshots used to be written to a FLAT path with the bucket name
mistakenly repeated as the first key segment:

    snapshots/2026-10-04/cam1_person_145147_042583.jpg

Migration 0005 makes the `snapshots` bucket private and namespaces every object
by owner:

    <user_id>/2026-10-04/cam1_person_145147_042583.jpg

because the Storage read policy is

    (storage.foldername(name))[1] = auth.uid()::text

and `storage.foldername()` is relative to the BUCKET ROOT: `name` is the key
inside the bucket, so the bucket's own name is not part of it. The first
segment of the key must therefore be the user id. Any object still sitting
under a leading `snapshots/` segment fails that check and simply stops
displaying in the Detections page - the row is intact, only the image is
unreachable. This script relocates those objects and rewrites
`detections.snapshot_path` to match, so no historical preview is lost.

The destination is derived per row as `"<user_id>/" + key-with-the-wrong-
prefix-stripped`, so it repairs all three shapes seen in practice:

    snapshots/<date>/<file>        -> <uid>/<date>/<file>
    snapshots/<uid>/<date>/<file>  -> <user_id>/<date>/<file>
    <something-else>/<date>/<file> -> <user_id>/<date>/<file>

It is safe to run more than once: an object already at `<user_id>/...` is
skipped.

USAGE
-----
 
   cd ai-server
    .\\venv\\Scripts\\python.exe scripts\\migrate_legacy_snapshots.py

    # dry run first - changes nothing, just reports what would move
    .\\venv\\Scripts\\python.exe scripts\\migrate_legacy_snapshots.py --dry-run

Requires the service-role key in ai-server/.env. Run it AFTER migration 0005,
because it reads `detections.user_id` to decide where each object belongs.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as a plain script from the ai-server directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app import supabase_client as sc  # noqa: E402


def _bucket_name() -> str:
    import os
    return os.environ.get("SUPABASE_STORAGE_BUCKET", "snapshots")


def target_key(snapshot_path: str, user_id: str) -> str:
    """
    The correct object key for `snapshot_path` once it belongs to `user_id`.

    Strips a leading `snapshots` segment (the old, wrong bucket-name prefix)
    and then a leading segment that already equals the owner, so re-running
    the script cannot double the namespace. Whatever is left is kept verbatim
    and re-rooted at the owner.
    """
    parts = [p for p in snapshot_path.split("/") if p]
    if parts and parts[0] == "snapshots":
        parts = parts[1:]
    if parts and parts[0] == user_id:
        parts = parts[1:]
    return "/".join([user_id, *parts])


def legacy_objects(client) -> tuple[list[dict], bool]:
    """
    Detections whose snapshot key does not start with its owner's uuid.

    The owner is the first segment of a correct key, so any row whose
    `snapshot_path` starts with something else still needs moving. That test is
    layout-independent: it catches the bucket-name prefix, the flat
    `snapshots/<date>/<file>` form and a bare `<date>/<file>` alike, without
    hardcoding a uuid.

    Returns (rows, has_owner_column). `has_owner_column` is False when
    migration 0005 has not been applied yet, in which case the rows come back
    without `user_id` and only the inventory half of the job is possible.
    """
    try:
        rows = sc.rows(
            client.table("detections")
            .select("id, snapshot_path, user_id")
            .execute()
        )
        has_owner = True
    except Exception as exc:  # noqa: BLE001
        if "user_id" not in str(exc):
            raise
        # Pre-0005 schema: report the inventory anyway so the migration can be
        # sized before it is applied.
        rows = sc.rows(
            client.table("detections")
            .select("id, snapshot_path")
            .execute()
        )
        has_owner = False

    legacy = []
    for row in rows:
        path = row.get("snapshot_path")
        if not path:
            continue
        if path.split("/", 1)[0] != row.get("user_id"):
            legacy.append(row)
    return legacy, has_owner


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report only; move nothing")
    args = parser.parse_args()

    bucket = _bucket_name()
    client = sc.get_client()
    if client is None:
        print("Supabase is not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing from .env)")
        return 1

    candidates, has_owner_column = legacy_objects(client)
    if not candidates:
        print("No legacy snapshot paths found - nothing to do.")
        return 0

    if not has_owner_column:
        print(f"Migration 0005 is NOT applied yet: detections.user_id does not exist.")
        print(f"Legacy snapshots found: {len(candidates)}")
        print()
        print("Apply migration 0005 first, then re-run this script - the")
        print("destination folder for each object depends on which user owns")
        print("the detection, so there is nothing useful this script can do")
        print("before the owner columns exist.")
        print()
        print("For reference, the objects that would move:")
        for row in candidates[:10]:
            print(f"  {row['snapshot_path']}  (detection id={row['id']})")
        if len(candidates) > 10:
            print(f"  ... and {len(candidates) - 10} more")
        return 2

    unowned = [r for r in candidates if not r.get("user_id")]
    if unowned:
        print(f"ABORT: {len(unowned)} legacy row(s) have no user_id.")
        print("Every detection must be owned before its snapshot can be placed.")
        return 1

    print(f"bucket={bucket}  legacy snapshots found={len(candidates)}")
    if args.dry_run:
        for row in candidates[:10]:
            print(f"  [dry-run] {row['snapshot_path']} -> "
                  f"{target_key(row['snapshot_path'], row['user_id'])}")
        if len(candidates) > 10:
            print(f"  ... and {len(candidates) - 10} more")
        print("\nDry run complete - nothing was changed.")
        return 0

    moved = skipped = failed = 0
    for row in candidates:
        new_path = target_key(row["snapshot_path"], row["user_id"])
        if new_path == row["snapshot_path"]:
            skipped += 1
            continue

        try:
            _move_object(client, bucket, row["snapshot_path"], new_path)
        except Exception as exc:  # noqa: BLE001 - keep going, report at the end
            print(f"  FAILED move {row['snapshot_path']}: {exc}")
            failed += 1
            continue

        try:
            client.table("detections").update({"snapshot_path": new_path}).eq("id", row["id"]).execute()
        except Exception as exc:  # noqa: BLE001
            # The object moved but the row still points at the old key. Report
            # it loudly rather than leaving a silently broken preview.
            print(f"  MOVED but path not updated (id={row['id']}): {exc}")
            print(f"    object now at {new_path}")
            failed += 1
            continue

        moved += 1

    print(f"\nmoved={moved} skipped={skipped} failed={failed}")
    return 1 if failed else 0


def _move_object(client, bucket: str, old_key: str, new_key: str) -> None:
    """
    Renames an object inside the bucket.

    `storage3` exposes a server-side `move()` on the bucket proxy, so this is
    one round-trip and cannot half-succeed. (The bucket proxy is reached as
    `from_`, with the trailing underscore, because `from` is a Python
    keyword.) Download/re-upload is kept as a fallback for older storage3
    builds; the object is a small JPEG so it costs nothing.
    """
    bucket_api = client.storage.from_(bucket)
    move = getattr(bucket_api, "move", None)
    if callable(move):
        move(old_key, new_key)
        return

    data = bucket_api.download(old_key)
    if isinstance(data, str):        # supabase-py returns the error as a str
        raise RuntimeError(data)
    bucket_api.upload(new_key, data, {"cache-control": "3600", "upsert": "false"})
    bucket_api.remove([old_key])


if __name__ == "__main__":
    raise SystemExit(main())
