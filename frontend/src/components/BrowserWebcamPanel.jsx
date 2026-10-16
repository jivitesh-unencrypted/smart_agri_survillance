import React, { useEffect, useRef, useState } from 'react'
import { Camera, Play, Square, Wifi, WifiOff } from 'lucide-react'
import { AI_SERVER_URL, getToken } from '../lib/api'

const EMPTY_COUNTS = { Human: 0, Animals: 0, Vehicles: 0, Others: 0 }

/** Uses the visitor's browser camera, not a webcam attached to the cloud host. */
export default function BrowserWebcamPanel() {
  const videoRef = useRef(null)
  const canvasRef = useRef(null)
  const streamRef = useRef(null)
  const activeRef = useRef(false)
  const [active, setActive] = useState(false)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('Camera is stopped')
  const [error, setError] = useState('')
  const [counts, setCounts] = useState(EMPTY_COUNTS)
  const [inferenceMs, setInferenceMs] = useState(null)
  const [detections, setDetections] = useState([])

  const stop = () => {
    activeRef.current = false
    setActive(false)
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop())
      streamRef.current = null
    }
    if (videoRef.current) videoRef.current.srcObject = null
    const canvas = canvasRef.current
    if (canvas) canvas.getContext('2d')?.clearRect(0, 0, canvas.width, canvas.height)
    setCounts(EMPTY_COUNTS)
    setDetections([])
    setInferenceMs(null)
    setStatus('Camera is stopped')
  }

  useEffect(() => () => {
    activeRef.current = false
    streamRef.current?.getTracks().forEach((track) => track.stop())
  }, [])

  const start = async () => {
    setError('')
    setBusy(true)
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error('Browser camera access is unavailable. Open this page over HTTPS or localhost.')
      }
      if (!getToken()) throw new Error('Your session is missing. Sign out and sign in again.')
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: 'environment', width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 8, max: 12 } },
      })
      streamRef.current = stream
      if (!videoRef.current) throw new Error('Video preview is not ready. Try again.')
      videoRef.current.srcObject = stream
      await videoRef.current.play()
      activeRef.current = true
      setActive(true)
      setStatus('Connected — sending frames for detection')
      setBusy(false)

      // Sequential capture keeps requests from piling up on a CPU-only cloud worker.
      const processNext = async () => {
        if (!activeRef.current || !videoRef.current || !canvasRef.current) return
        const video = videoRef.current
        const canvas = canvasRef.current
        if (video.readyState >= 2 && video.videoWidth && video.videoHeight) {
          const scale = Math.min(1, 640 / video.videoWidth, 480 / video.videoHeight)
          canvas.width = Math.max(1, Math.round(video.videoWidth * scale))
          canvas.height = Math.max(1, Math.round(video.videoHeight * scale))
          const ctx = canvas.getContext('2d')
          ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
          canvas.toBlob(async (blob) => {
            if (!blob || !activeRef.current) return
            try {
              const form = new FormData()
              form.append('frame', blob, 'webcam-frame.jpg')
              const token = getToken()
              const response = await fetch(`${AI_SERVER_URL}/api/live-detection/frame`, {
                method: 'POST',
                headers: token ? { Authorization: `Bearer ${token}` } : {},
                body: form,
              })
              const result = await response.json().catch(() => ({}))
              if (!response.ok) throw new Error(result.detail || `Detection request failed (${response.status})`)
              if (!activeRef.current) return
              const nextDetections = result.detections || []
              setDetections(nextDetections)
              // Paint the latest frame and its YOLO boxes over the video preview.
              if (videoRef.current?.videoWidth && canvasRef.current) {
                const overlay = canvasRef.current
                const overlayCtx = overlay.getContext('2d')
                overlayCtx.drawImage(videoRef.current, 0, 0, overlay.width, overlay.height)
                const sourceWidth = result.width || overlay.width
                const sourceHeight = result.height || overlay.height
                const sx = overlay.width / sourceWidth
                const sy = overlay.height / sourceHeight
                nextDetections.forEach((item) => {
                  const [x1, y1, x2, y2] = item.bbox || []
                  if (![x1, y1, x2, y2].every(Number.isFinite)) return
                  const palette = { Human: '#22c55e', Animals: '#f97316', Vehicles: '#3b82f6', Others: '#a1a1aa' }
                  const color = palette[item.category] || palette.Others
                  overlayCtx.strokeStyle = color
                  overlayCtx.lineWidth = Math.max(2, overlay.width / 320)
                  overlayCtx.strokeRect(x1 * sx, y1 * sy, (x2 - x1) * sx, (y2 - y1) * sy)
                  const label = `${item.name} ${Math.round((item.confidence || 0) * 100)}%`
                  overlayCtx.font = '13px sans-serif'
                  const textWidth = overlayCtx.measureText(label).width
                  overlayCtx.fillStyle = color
                  overlayCtx.fillRect(x1 * sx, Math.max(0, y1 * sy - 19), textWidth + 8, 19)
                  overlayCtx.fillStyle = '#ffffff'
                  overlayCtx.fillText(label, x1 * sx + 4, Math.max(13, y1 * sy - 5))
                })
              }
              setCounts(result.counts || EMPTY_COUNTS)
              setInferenceMs(result.inference_ms ?? null)
              setStatus('Connected — live detection running')
              setError('')
            } catch (err) {
              if (activeRef.current) {
                setStatus('Detection connection problem')
                setError(err.message || 'Could not reach the AI server')
              }
            } finally {
              if (activeRef.current) window.setTimeout(processNext, 650)
            }
          }, 'image/jpeg', 0.72)
        } else if (activeRef.current) {
          window.setTimeout(processNext, 250)
        }
      }
      processNext()
    } catch (err) {
      stop()
      setError(err.message || 'Could not start the browser webcam')
      setStatus('Unable to start camera')
    } finally {
      setBusy(false)
    }
  }

  const total = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0)

  return (
    <section className="card space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="card-title flex items-center gap-2"><Camera size={15} /> Browser Webcam · Cloud YOLO</div>
          <p className="text-xs text-slate-500">Uses your device camera and sends compressed frames to the authenticated AI server.</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-400">
          {active ? <Wifi size={15} className="text-emerald-400" /> : <WifiOff size={15} />}
          {status}
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_220px] gap-4">
        <div className="relative aspect-video overflow-hidden rounded-lg bg-slate-950 flex items-center justify-center">
          <video ref={videoRef} muted playsInline className={`h-full w-full object-contain ${active ? '' : 'hidden'}`} />
          <canvas ref={canvasRef} className="pointer-events-none absolute inset-0 h-full w-full object-contain" />
          {!active && <div className="px-4 text-center text-sm text-slate-500"><Camera size={26} className="mx-auto mb-2 opacity-60" />Press Start to allow camera access</div>}
        </div>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <Metric label="Objects" value={total} />
            <Metric label="Inference" value={inferenceMs == null ? '—' : `${Math.round(inferenceMs)} ms`} />
            <Metric label="Humans" value={counts.Human || 0} />
            <Metric label="Animals" value={counts.Animals || 0} />
            <Metric label="Vehicles" value={counts.Vehicles || 0} />
            <Metric label="Others" value={counts.Others || 0} />
          </div>
          <div className="flex gap-2">
            {!active ? (
              <button className="btn btn-primary flex-1" disabled={busy} onClick={start}><Play size={15} /> {busy ? 'Starting…' : 'Start webcam'}</button>
            ) : (
              <button className="btn btn-danger flex-1" onClick={stop}><Square size={15} /> Stop webcam</button>
            )}
          </div>
          {error && <p role="alert" className="text-xs text-red-400 break-words">{error}</p>}
          {active && <p className="text-xs text-slate-500">Frames are sampled at a limited rate to reduce cloud CPU load. Stop webcam to end capture.</p>}
          {detections.length > 0 && <div className="space-y-1 max-h-28 overflow-auto">{detections.slice(0, 6).map((item, index) => <div key={`${item.class_id}-${index}`} className="flex justify-between gap-2 text-xs"><span className="text-slate-300">{item.name}</span><span className="text-slate-500">{Math.round((item.confidence || 0) * 100)}%</span></div>)}</div>}
        </div>
      </div>
    </section>
  )
}

function Metric({ label, value }) {
  return <div className="rounded-md bg-slate-800/60 p-2"><div className="text-[11px] text-slate-500">{label}</div><div className="text-sm font-semibold text-slate-100">{value}</div></div>
}
