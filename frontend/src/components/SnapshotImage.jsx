import React, { useEffect, useState } from 'react'
import { mediaUrl, localMediaUrl } from '../lib/api'

/**
 * Snapshot image with an offline fallback.
 *
 * The preferred source is the public Supabase Storage URL, which is what a
 * synced detection uses. When the AI server is offline - or the event is
 * still sitting in the local outbox and therefore only exists on the
 * machine that produced it - the cloud object does not exist yet, so this
 * transparently retries against the local `/storage` route.
 *
 * If neither source has the file, the parent's empty-state is shown.
 */
export default function SnapshotImage({ snapshotPath, alt = '', className = '', onClick, onMissing }) {
  const [src, setSrc] = useState(() => mediaUrl(snapshotPath))
  const [failed, setFailed] = useState(false)

  // A new detection row must restart the cloud-then-local attempt.
  useEffect(() => {
    setSrc(mediaUrl(snapshotPath))
    setFailed(false)
  }, [snapshotPath])

  if (!snapshotPath || failed) {
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
