"""
Supabase Storage access for detection snapshots.

Rules this module enforces:
  * Only the small JPEG saved on a *detection event* is uploaded.
    Raw camera frames are never streamed to the cloud.
  * The local file is kept after a successful upload (it is the offline
    cache); it is only deleted when `delete_after_upload` is requested,
    which the caller uses for throwaway snapshots.
  * Upload failures raise `SupabaseUnavailable` so the caller can queue
    the whole event instead of losing it.
"""
import logging
from pathlib import Path
from typing import Optional

from app import supabase_client as sc
from app.core.config import settings

logger = logging.getLogger("ai_server.storage")


def snapshot_object_path(day: str, filename: str, user_id: Optional[str] = None) -> str:
    """
    Object key inside the `snapshots` bucket, namespaced by owner:

        <user_id>/YYYY-MM-DD/<file>.jpg

    The owner folder is not decoration. The bucket is private and its read
    policy is `(storage.foldername(name))[1] = auth.uid()::text`. Two facts
    together pin down the layout:

      * `name` in `storage.objects` is the key RELATIVE TO THE BUCKET ROOT -
        the bucket's own name is not part of it, so it must not be repeated
        here. Prefixing it produced `snapshots/<user_id>/...` inside a bucket
        already called `snapshots`.
      * `storage.foldername()` drops the final segment, so `[1]` is the FIRST
        directory in the key. That segment must be the user id.

    Which is why the key starts with the owner and not with `snapshots/`; the
    earlier double prefix made `foldername(name)[1]` the literal `'snapshots'`
    and the policy matched nothing for anybody.

    When `user_id` is missing (a job with no resolvable owner, which should not
    happen - the analysis run now aborts instead) the key falls back to the old
    un-namespaced layout; such an object is then not readable by any user
    through RLS, which is the safe failure direction.
    """
    if not user_id:
        return f"{day}/{filename}"
    return f"{user_id}/{day}/{filename}"


def upload_snapshot_object(local_path: Path, object_path: str) -> Optional[str]:
    """
    Uploads a local JPEG to the snapshots bucket.

    Returns the object path on success, or None when snapshot uploading
    is switched off (settings.upload_snapshots_to_cloud = false), which
    keeps the app fully local with zero cloud storage.
    """
    if not settings.upload_snapshots_to_cloud:
        logger.debug("Snapshot upload disabled by settings - keeping %s local only", local_path.name)
        return None

    client = sc.get_client()
    if client is None:
        raise sc.SupabaseUnavailable("Supabase is not configured; cannot upload snapshot")

    if not local_path.exists():
        logger.warning("Snapshot %s no longer exists locally; uploading metadata-only record", local_path)
        return object_path

    def _run(c):
        with open(local_path, "rb") as fh:
            data = fh.read()
        return c.storage.from_(settings.SUPABASE_STORAGE_BUCKET).upload(
            object_path,
            data,
            {"content-type": "image/jpeg", "cache-control": "31536000", "upsert": "true"},
        )

    sc.call(_run)
    logger.info("Uploaded snapshot to %s/%s", settings.SUPABASE_STORAGE_BUCKET, object_path)
    return object_path


def delete_snapshot(object_path: str) -> None:
    if not object_path:
        return
    try:
        sc.call(
            lambda c: c.storage.from_(settings.SUPABASE_STORAGE_BUCKET).remove([object_path])
        )
    except sc.SupabaseUnavailable as exc:
        logger.warning("Could not delete snapshot %s: %s", object_path, exc)
