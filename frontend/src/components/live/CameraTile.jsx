import React, { useState } from 'react'
import { Play, Square, PlugZap, Pencil, Trash2, Maximize2, Minimize2, MapPin, Camera as CameraIcon } from 'lucide-react'
import StatusBadge from '../StatusBadge.jsx'
import { api } from '../../lib/api'
import { useToast } from '../../hooks/useToast.jsx'
import { useServerCamera } from '../../hooks/useServerCamera'
import { useBrowserCamera } from '../../hooks/useBrowserCamera'
import { isBrowserCamera, CONNECTION_LABELS } from '../../lib/cameras'
import { CATEGORY_RGB, totalCount } from '../../lib/detectionStyle'

/**
 * One camera on the Live Monitoring screen. Every connection type renders the
 * same tile: the same frame box, status badge, detection counts and controls.
 * Only the source of the picture and the start/stop mechanics differ.
 */
export default function CameraTile(props) {
  return isBrowserCamera(props.camera)
    ? <BrowserCameraTile {...props} />
    : <ServerCameraTile {...props} />
}

function ServerCameraTile({ camera, socket, expanded, onToggleExpand, onEdit, onDelete, onChanged }) {
  const feed = useServerCamera(camera, socket?.subscribe)
  const [testing, setTesting] = useState(false)
  const toast = useToast()

  const test = async () => {
    setTesting(true)
    try {
      const result = await api.post(`/api/cameras/${camera.id}/test`)
      if (result.success) toast.success(`${camera.name}: stream is available (${result.latency_ms?.toFixed(0)} ms)`)
      else toast.error(`${camera.name}: ${result.message}`)
      onChanged?.()
    } catch (err) {
      toast.error(err.detail || 'Connection test failed')
    } finally {
      setTesting(false)
    }
  }

  return (
    <TileChrome
      camera={camera}
      feed={feed}
      expanded={expanded}
      onToggleExpand={onToggleExpand}
      onEdit={onEdit}
      onDelete={onDelete}
      onTest={test}
      testing={testing}
    />
  )
}

function BrowserCameraTile({ camera, expanded, onToggleExpand, onEdit, onDelete }) {
  const feed = useBrowserCamera(camera)
  return (
    <TileChrome
      camera={camera}
      feed={feed}
      expanded={expanded}
      onToggleExpand={onToggleExpand}
      onEdit={onEdit}
      onDelete={onDelete}
    />
  )
}

function TileChrome({ camera, feed, expanded, onToggleExpand, onEdit, onDelete, onTest, testing }) {
  const browser = isBrowserCamera(camera)
  const total = totalCount(feed.counts)
  const placeholder = feed.running
    ? ''
    : feed.busy
      ? 'Waiting for camera permission…'
      : browser
        ? 'Choose a camera and press Start to begin detection.'
        : 'Camera is stopped. Press Start to begin streaming.'

  return (
    <section className="card p-3 space-y-3 h-full">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-medium text-slate-100 truncate">{camera.name}</div>
          <div className="text-xs text-slate-500 flex flex-wrap items-center gap-1 mt-0.5">
            <span>{camera.camera_code}</span>
            <span>·</span>
            <span>{CONNECTION_LABELS[camera.connection_type] || camera.connection_type}</span>
            {camera.location && (<><MapPin size={11} className="ml-1" /> {camera.location}</>)}
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {feed.running && <span className="badge bg-red-500/10 text-red-400">LIVE</span>}
          <StatusBadge status={feed.status} />
        </div>
      </div>

      <div className="relative aspect-video overflow-hidden rounded-lg bg-black">
        {feed.kind === 'stream' && feed.streamSrc && (
          <img src={feed.streamSrc} alt={`${camera.name} live feed`} className="absolute inset-0 h-full w-full object-contain" />
        )}
        {feed.kind === 'canvas' && (
          <>
            <video
              ref={feed.videoRef}
              muted
              playsInline
              aria-label={`${camera.name} camera picture`}
              className={`absolute inset-0 h-full w-full object-contain ${feed.running ? '' : 'hidden'}`}
            />
            <canvas
              ref={feed.canvasRef}
              aria-label={`${camera.name} live feed`}
              className={`pointer-events-none absolute inset-0 h-full w-full object-contain ${feed.running ? '' : 'invisible'}`}
            />
          </>
        )}
        {placeholder && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-4 text-center text-sm text-slate-500">
            <CameraIcon size={24} className="opacity-60" />
            {placeholder}
          </div>
        )}
      </div>

      {feed.error && <p role="alert" className="text-xs text-red-400 break-words">{feed.error}</p>}

      <div className="grid grid-cols-5 gap-1.5 text-center">
        <Metric label="Objects" value={total} />
        <Metric label="Humans" value={feed.counts.Human} color={CATEGORY_RGB.Human} />
        <Metric label="Animals" value={feed.counts.Animals} color={CATEGORY_RGB.Animals} />
        <Metric label="Vehicles" value={feed.counts.Vehicles} color={CATEGORY_RGB.Vehicles} />
        <Metric label="Others" value={feed.counts.Others} color={CATEGORY_RGB.Others} />
      </div>
      <div className="flex justify-between text-[11px] text-slate-500">
        <span>FPS {feed.fps != null ? Number(feed.fps).toFixed(1) : '—'}</span>
        <span>Inference {feed.inferenceMs != null ? `${Math.round(feed.inferenceMs)} ms` : '—'}</span>
      </div>

      {browser && !feed.running && (
        <DeviceSelect feed={feed} />
      )}

      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
        {feed.running ? (
          <button className="btn btn-danger text-xs px-2 py-1.5 sm:col-span-2" disabled={feed.busy} onClick={feed.stop}>
            <Square size={13} /> Stop
          </button>
        ) : (
          <button className="btn btn-primary text-xs px-2 py-1.5 sm:col-span-2" disabled={feed.busy} onClick={feed.start}>
            <Play size={13} /> {feed.busy ? 'Starting…' : 'Start'}
          </button>
        )}
        {onTest && (
          <button className="btn btn-secondary text-xs px-2 py-1.5" disabled={testing} onClick={onTest}>
            <PlugZap size={13} /> Test
          </button>
        )}
        <button className="btn btn-secondary text-xs px-2 py-1.5" onClick={onEdit}>
          <Pencil size={13} /> Edit
        </button>
        <button className="btn btn-secondary text-xs px-2 py-1.5" onClick={onToggleExpand} aria-label={expanded ? 'Shrink tile' : 'Expand tile'}>
          {expanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
        </button>
        <button
          className="btn btn-secondary text-xs px-2 py-1.5 hover:!bg-red-600/20 hover:!text-red-400"
          onClick={onDelete}
          aria-label="Delete camera"
        >
          <Trash2 size={13} />
        </button>
      </div>
    </section>
  )
}

function DeviceSelect({ feed }) {
  return (
    <div>
      <label className="label">Camera on this device</label>
      <select
        className="input"
        value={feed.deviceId}
        onChange={(e) => feed.setDevice(e.target.value)}
      >
        <option value="">Default camera</option>
        {feed.devices.map((device, index) => (
          <option key={device.deviceId || index} value={device.deviceId}>
            {device.label || `Camera ${index + 1}`}
          </option>
        ))}
        {feed.deviceId && !feed.devices.some((d) => d.deviceId === feed.deviceId) && (
          // A saved choice not in the list yet (permission not granted in this session).
          <option value={feed.deviceId}>Saved camera</option>
        )}
      </select>
      <p className="text-[11px] text-slate-500 mt-1">
        Your choice is remembered in this browser. Browser cameras need HTTPS (or localhost).
      </p>
    </div>
  )
}

function Metric({ label, value, color }) {
  return (
    <div className="rounded-md bg-slate-800/50 py-1.5">
      <div className="text-sm font-semibold text-slate-100" style={color ? { color } : undefined}>{value}</div>
      <div className="text-[10px] text-slate-500 uppercase">{label}</div>
    </div>
  )
}
