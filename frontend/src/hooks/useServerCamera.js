import { useEffect, useState } from 'react'
import { api, streamUrl } from '../lib/api'
import { useToast } from './useToast.jsx'
import { ZERO_COUNTS, normalizeCounts } from '../lib/detectionStyle'

/**
 * Live state for a camera the AI server opens itself (USB on the server host,
 * IP, RTSP or HTTP/MJPEG). The video is the server's annotated MJPEG stream,
 * so boxes are drawn server-side; telemetry arrives over the owner-scoped socket.
 */
export function useServerCamera(camera, subscribe) {
  const toast = useToast()
  const id = camera.id
  const [running, setRunning] = useState(camera.status === 'online')
  const [busy, setBusy] = useState(false)
  const [fps, setFps] = useState(null)
  const [inferenceMs, setInferenceMs] = useState(null)
  const [counts, setCounts] = useState(ZERO_COUNTS)

  // The list reload after a start/stop or test carries the authoritative status.
  useEffect(() => {
    setRunning(camera.status === 'online')
  }, [camera.status])

  useEffect(() => {
    setFps(null)
    setInferenceMs(null)
    setCounts(ZERO_COUNTS)
  }, [id])

  useEffect(() => {
    if (!running) {
      setCounts(ZERO_COUNTS)
      setFps(null)
      setInferenceMs(null)
    }
  }, [running])

  useEffect(() => {
    if (!subscribe) return undefined
    return subscribe((evt) => {
      if (evt.camera_id !== id) return
      if (evt.type === 'fps') {
        setFps(evt.fps)
        setInferenceMs(evt.inference_ms)
      } else if (evt.type === 'detection_count') {
        setCounts(normalizeCounts(evt.counts))
      } else if (evt.type === 'camera_status') {
        setRunning(evt.status === 'online')
      }
    })
  }, [subscribe, id])

  const start = async () => {
    setBusy(true)
    try {
      await api.post(`/api/cameras/${id}/start`)
      setRunning(true)
      toast.success(`${camera.name} starting…`)
    } catch (err) {
      toast.error(err.detail || err.message || 'Failed to start camera')
    } finally {
      setBusy(false)
    }
  }

  const stop = async () => {
    setBusy(true)
    try {
      await api.post(`/api/cameras/${id}/stop`)
      setRunning(false)
      toast.info(`${camera.name} stopped`)
    } catch (err) {
      toast.error(err.detail || err.message || 'Failed to stop camera')
    } finally {
      setBusy(false)
    }
  }

  return {
    kind: 'stream',
    status: busy ? 'starting' : running ? 'online' : camera.status === 'unknown' ? 'unknown' : 'offline',
    running,
    busy,
    error: running ? '' : camera.last_error || '',
    counts,
    fps,
    inferenceMs,
    streamSrc: running ? streamUrl(id) : null,
    start,
    stop,
  }
}
