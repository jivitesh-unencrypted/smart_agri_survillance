import { useCallback, useEffect, useRef, useState } from 'react'
import { AI_SERVER_URL, api, getToken } from '../lib/api'
import { useToast } from './useToast.jsx'
import { ZERO_COUNTS, drawDetections, normalizeCounts } from '../lib/detectionStyle'
import { browserDeviceStorageKey } from '../lib/cameras'

const FRAME_WIDTH = 640
const FRAME_HEIGHT = 480
const MAX_FPS = 8
const NEXT_FRAME_MS = 650
const RETRY_MS = 2000

/** Turns getUserMedia / API failures into messages a non-technical user can act on. */
export function describeCameraError(err) {
  const name = err?.name || ''
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'Camera permission was denied. Allow camera access for this site in your browser settings, then press Start again.'
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
    return 'No camera was found on this device. Connect a webcam, or choose another device.'
  }
  if (name === 'NotReadableError' || name === 'TrackStartError') {
    return 'The camera is being used by another application. Close that application and try again.'
  }
  if (name === 'OverconstrainedError') {
    return 'The selected camera cannot provide the requested settings. Choose another camera.'
  }
  if (name === 'NotSupportedError') {
    return 'This browser cannot open a camera here. Use a current browser over HTTPS, or check the camera permission.'
  }
  if (name === 'AbortError') {
    return 'Starting the camera was interrupted. Press Start again.'
  }
  if (typeof err?.detail === 'string' && err.detail) return err.detail
  return err?.message || 'Could not start the browser camera.'
}

function readStoredDevice(key) {
  try {
    return localStorage.getItem(key) || ''
  } catch {
    return ''
  }
}

/** Ends a server-side browser session without awaiting it; keepalive lets it outlive the page. */
function sendSessionStop(cameraId) {
  const token = getToken()
  return fetch(`${AI_SERVER_URL}/api/live-detection/sessions/${cameraId}/stop`, {
    method: 'POST',
    keepalive: true,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  }).catch(() => {})
}

/**
 * Live state for a browser webcam (built-in or USB camera on the device running
 * the dashboard). The browser captures with getUserMedia, sends one JPEG at a time
 * to the AI server tied to this camera, and paints the returned YOLO boxes onto the
 * same frame it sent, so boxes always line up with the picture.
 *
 * The component must be keyed by camera id: this hook does not handle id changes.
 */
export function useBrowserCamera(camera) {
  const toast = useToast()
  const id = camera.id
  const storageKey = browserDeviceStorageKey(id)

  const [deviceId, setDeviceIdState] = useState(() => readStoredDevice(storageKey))
  const [devices, setDevices] = useState([])
  const [running, setRunning] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [counts, setCounts] = useState(ZERO_COUNTS)
  const [fps, setFps] = useState(null)
  const [inferenceMs, setInferenceMs] = useState(null)

  // The tile shows the live camera picture (videoRef) with transparent detection
  // boxes on top (canvasRef). The picture therefore appears as soon as the camera
  // starts, and does not wait for the AI server to answer.
  const canvasRef = useRef(null)
  const videoRef = useRef(null)
  const frameRef = useRef(null) // offscreen copy of the frame currently in flight
  const streamRef = useRef(null)
  const activeRef = useRef(false)
  const sessionRef = useRef(false)
  const fpsRef = useRef({ last: 0, value: 0 })
  const stopAllRef = useRef(() => {})

  if (!frameRef.current && typeof document !== 'undefined') {
    frameRef.current = document.createElement('canvas')
  }

  const setDevice = (value) => {
    setDeviceIdState(value)
    try {
      if (value) localStorage.setItem(storageKey, value)
      else localStorage.removeItem(storageKey)
    } catch {
      /* private browsing: the choice lasts for this page only */
    }
  }

  const refreshDevices = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) return
    try {
      const list = await navigator.mediaDevices.enumerateDevices()
      setDevices(list.filter((d) => d.kind === 'videoinput'))
    } catch {
      /* the list is a convenience; capture still works without it */
    }
  }, [])

  useEffect(() => {
    refreshDevices()
    const md = navigator.mediaDevices
    md?.addEventListener?.('devicechange', refreshDevices)
    return () => md?.removeEventListener?.('devicechange', refreshDevices)
  }, [refreshDevices])

  // Stops the camera tracks and clears the picture. No network call.
  const releaseCapture = useCallback(() => {
    activeRef.current = false
    streamRef.current?.getTracks().forEach((track) => {
      track.onended = null
      track.stop()
    })
    streamRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
    const canvas = canvasRef.current
    canvas?.getContext('2d')?.clearRect(0, 0, canvas.width, canvas.height)
    setRunning(false)
    setCounts(ZERO_COUNTS)
    setFps(null)
    setInferenceMs(null)
    fpsRef.current = { last: 0, value: 0 }
  }, [])

  const endSession = useCallback(() => {
    if (!sessionRef.current) return
    sessionRef.current = false
    sendSessionStop(id)
  }, [id])

  const stopAll = useCallback(() => {
    releaseCapture()
    endSession()
  }, [releaseCapture, endSession])

  useEffect(() => {
    stopAllRef.current = stopAll
  }, [stopAll])

  // Leaving the page, reloading or unmounting must not leave the camera marked live.
  useEffect(() => {
    const onHide = () => stopAllRef.current()
    window.addEventListener('pagehide', onHide)
    return () => {
      window.removeEventListener('pagehide', onHide)
      activeRef.current = false
      streamRef.current?.getTracks().forEach((track) => track.stop())
      streamRef.current = null
      if (sessionRef.current) {
        sessionRef.current = false
        sendSessionStop(id)
      }
    }
  }, [id])

  // Draws only the boxes, on a transparent overlay the size of the analysed frame.
  const paint = (result) => {
    const overlay = canvasRef.current
    const frame = frameRef.current
    if (!overlay || !frame) return
    const width = result.width || frame.width
    const height = result.height || frame.height
    if (overlay.width !== width) overlay.width = width
    if (overlay.height !== height) overlay.height = height
    const ctx = overlay.getContext('2d')
    ctx.clearRect(0, 0, width, height)
    drawDetections(ctx, result.detections || [], width, height)
  }

  const runLoop = () => {
    const tick = () => {
      if (!activeRef.current) return
      const video = videoRef.current
      const frame = frameRef.current
      if (!video || !frame || video.readyState < 2 || !video.videoWidth) {
        window.setTimeout(tick, 250)
        return
      }
      const scale = Math.min(1, FRAME_WIDTH / video.videoWidth, FRAME_HEIGHT / video.videoHeight)
      frame.width = Math.max(1, Math.round(video.videoWidth * scale))
      frame.height = Math.max(1, Math.round(video.videoHeight * scale))
      frame.getContext('2d').drawImage(video, 0, 0, frame.width, frame.height)

      // Frames are sequential: the next one is captured only after this result is painted.
      frame.toBlob(async (blob) => {
        let delay = NEXT_FRAME_MS
        try {
          if (!blob || !activeRef.current) return
          const form = new FormData()
          form.append('frame', blob, 'frame.jpg')
          form.append('camera_id', String(id))
          const token = getToken()
          const response = await fetch(`${AI_SERVER_URL}/api/live-detection/frame`, {
            method: 'POST',
            headers: token ? { Authorization: `Bearer ${token}` } : {},
            body: form,
          })
          const result = await response.json().catch(() => ({}))
          if (!response.ok) {
            const detail = typeof result.detail === 'string' ? result.detail : null
            if (response.status === 409) {
              // The server no longer has this session (for example after a restart).
              sessionRef.current = false
              releaseCapture()
              setError(detail || 'The server stopped this camera. Press Start to resume.')
              return
            }
            throw new Error(detail || `Detection request failed (${response.status})`)
          }
          if (!activeRef.current) return

          paint(result)
          setCounts(normalizeCounts(result.counts))
          setInferenceMs(result.inference_ms ?? null)
          const now = performance.now()
          const state = fpsRef.current
          if (state.last) {
            const instant = 1000 / Math.max(1, now - state.last)
            state.value = state.value ? state.value * 0.8 + instant * 0.2 : instant
          }
          state.last = now
          setFps(Number(state.value.toFixed(1)))
          setError('')
        } catch (err) {
          if (activeRef.current) {
            setError(err.message || 'Could not reach the AI server')
            delay = RETRY_MS
          }
        } finally {
          if (activeRef.current) window.setTimeout(tick, delay)
        }
      }, 'image/jpeg', 0.72)
    }
    tick()
  }

  const start = async () => {
    if (busy || activeRef.current) return
    setError('')
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      setError('Browser camera access needs HTTPS (or localhost). Open the dashboard over HTTPS and try again.')
      return
    }
    setBusy(true)
    let stream = null
    try {
      const constraints = {
        width: { ideal: FRAME_WIDTH },
        height: { ideal: FRAME_HEIGHT },
        frameRate: { ideal: MAX_FPS, max: 12 },
      }
      if (deviceId) constraints.deviceId = { exact: deviceId }
      stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: constraints })

      // Labels and the full device list only become available after permission.
      await refreshDevices()
      const track = stream.getVideoTracks()[0]
      const usedDevice = track?.getSettings?.().deviceId
      if (usedDevice && usedDevice !== deviceId) setDevice(usedDevice)

      await api.post(`/api/live-detection/sessions/${id}/start`)
      sessionRef.current = true

      streamRef.current = stream
      if (track) {
        track.onended = () => {
          if (!activeRef.current) return
          stopAllRef.current()
          setError('The camera was disconnected or permission was revoked. Reconnect it and press Start.')
        }
      }
      const video = videoRef.current
      if (!video) throw new Error('The video preview is not ready. Press Start again.')
      video.muted = true
      video.playsInline = true
      video.srcObject = stream
      await video.play()

      activeRef.current = true
      setRunning(true)
      setBusy(false)
      runLoop()
    } catch (err) {
      stream?.getTracks().forEach((track) => track.stop())
      if (sessionRef.current) endSession()
      releaseCapture()
      setError(describeCameraError(err))
      setBusy(false)
    }
  }

  const stop = () => {
    stopAll()
    setError('')
    toast.info(`${camera.name} stopped`)
  }

  const status = busy
    ? 'starting'
    : running
      ? 'online'
      : camera.status === 'unknown'
        ? 'unknown'
        : 'offline'

  return {
    kind: 'canvas',
    status,
    running,
    busy,
    error,
    counts,
    fps,
    inferenceMs,
    canvasRef,
    videoRef,
    devices,
    deviceId,
    setDevice,
    start,
    stop,
  }
}
