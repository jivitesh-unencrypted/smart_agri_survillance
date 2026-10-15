import React, { useEffect, useState } from 'react'
import { mediaUrl, localMediaUrl } from '../lib/api'
import { useUserScope } from '../lib/userScope'

/**
 * Snapshot image with an offline fallback.
 *
 * The preferred source is a short-lived SIGNED Supabase Storage URL, because
 * the `snapshots` bucket is private and objects are namespaced by owner
 * (`<user_id>/...` - the user id is the first key segment, which is what the
 * bucket's `storage.foldername(name)[1] = auth.uid()` policy reads). Signing is
 * asynchronous, so the cloud source is resolved in an effect rather than during
 * render.
 *
 * When the AI server is offline - or the event is still sitting in the local
 * outbox and therefore only exists on the machine that produced it - the cloud
 * object does not exist yet, so this transparently retries against the local
 * `/storage` route.
 *
 * If neither source has the file, the parent's empty-state is shown.
 */
export default function SnapshotImage({ snapshotPath, alt = '', className = '', onClick, onMissing }) {
  const [src, setSrc] = useState(null)
  const [failed, setFailed] = useState(false)
  // `useUserScope()` returns the epoch value itself (a number), not an object.
  const epoch = useUserScope()

  // A new detection row - or a different signed-in user - must restart the
  // cloud-then-local attempt. On an identity change the previous signed URL is
  // void anyway, so re-signing is required rather than merely tidy.
  useEffect(() => {
    let cancelled = false
    setSrc(null)
    setFailed(false)

    if (!snapshotPath) return undefined

    mediaUrl(snapshotPath).then((signed) => {
      if (cancelled) return
      setSrc(signed || localMediaUrl(snapshotPath))
    })

    return () => {
      cancelled = true
    }
  }, [snapshotPath, epoch])

  if (!snapshotPath || failed || !src) {
    return onMissing ?? null
  }

  const handleError = () => {
    const local = localMediaUrl(snapshotPath)
    if (local && src !== local) setSrc(local)
    else setFailed(true)
  }

  if (onClick) {
    return (
      <button type="button" onClick={onClick} className="block" title="View snapshot">
        <img src={src} alt={alt} className={className} onError={handleError} loading="lazy" />
      </button>
    )
  }

  return <img src={src} alt={alt} className={className} onError={handleError} loading="lazy" />
}
