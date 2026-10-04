import React, { useEffect, useMemo, useState } from 'react'
import { Play, Square, Camera as CameraIcon, Maximize2, Minimize2, Gauge } from 'lucide-react'
import { api, streamUrl } from '../lib/api'
import { useToast } from '../hooks/useToast.jsx'
import { categoryColor, severityColor } from '../lib/utils'

export default function LiveMonitoring({ socket }) {
  const [cameras, setCameras] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [running, setRunning] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)
  const [loadingAction, setLoadingAction] = useState(false)
  const toast = useToast()

  const loadCameras = async () => {
    try {
      const list = await api.get('/api/cameras')
      setCameras(list)
      if (!selectedId && list.length > 0) setSelectedId(list[0].id)
    } catch {
      // The AI server is unreachable. Nothing to stream, but the page
      // stays usable and other pages still read data from Supabase.
      setCameras([])
    }
  }

  useEffect(() => { loadCameras() }, [])

  const selectedCamera = cameras.find((c) => c.id === selectedId)

  useEffect(() => {
    setRunning(selectedCamera?.status === 'online')
  }, [selectedCamera])

  // Live telemetry derived from the most recent WS messages for this camera.
  const [fps, setFps] = useState(null)
  const [inferenceMs, setInferenceMs] = useState(null)
  const [counts, setCounts] = useState({ Human: 0, Animals: 0, Vehicles: 0, Others: 0 })
  const [liveEvents, setLiveEvents] = useState([])

  useEffect(() => {
    setFps(null); setInferenceMs(null); setCounts({ Human: 0, Animals: 0, Vehicles: 0, Others: 0 }); setLiveEvents([])
    return socket.subscribe((evt) => {
      if (evt.camera_id !== selectedId) return
      if (evt.type === 'fps') {
        setFps(evt.fps)
        setInferenceMs(evt.inference_ms)
      } else if (evt.type === 'detection_count') {
        setCounts(evt.counts)
      } else if (evt.type === 'detection' && evt.is_new_event) {
        setLiveEvents((prev) => [evt, ...prev].slice(0, 20))
      } else if (evt.type === 'camera_status') {
        setRunning(evt.status === 'online')
      }
    })
  }, [socket.subscribe, selectedId])

  const totalObjects = useMemo(() => Object.values(counts).reduce((a, b) => a + b, 0), [counts])

  const handleStart = async () => {
    if (!selectedId) return
    setLoadingAction(true)
    try {
      await api.post(`/api/cameras/${selectedId}/start`)
      setRunning(true)
      toast.success('Camera starting…')
    } catch (err) {
      toast.error(err.detail || 'Failed to start camera')
    } finally {
      setLoadingAction(false)
    }
  }

  const handleStop = async () => {
    if (!selectedId) return
    setLoadingAction(true)
    try {
      await api.post(`/api/cameras/${selectedId}/stop`)
      setRunning(false)
      toast.info('Camera stopped')
    } catch (err) {
      toast.error(err.detail || 'Failed to stop camera')
    } finally {
      setLoadingAction(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Live Monitoring</h1>
        <p className="text-sm text-slate-500">Continuous real-time video and YOLO object detection.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-6">
        {/* Controls column */}
        <div className="space-y-4">
          <div className="card">
            <div className="card-title">Camera Setup</div>
            {cameras.length === 0 ? (
              <p className="text-sm text-slate-500">No cameras configured yet. Add one from the Cameras page.</p>
            ) : (
              <>
                <label className="label">Select Camera</label>
                <select
                  className="input mb-3"
                  value={selectedId || ''}
                  onChange={(e) => setSelectedId(Number(e.target.value))}
                >
                  {cameras.map((c) => (
                    <option key={c.id} value={c.id}>{c.name}{c.location ? ` (${c.location})` : ''}</option>
                  ))}
                </select>
                {selectedCamera && (
                  <p className="text-xs text-slate-500 mb-3">
                    ID: <span className="text-slate-400">{selectedCamera.camera_code}</span> · Type: <span className="text-slate-400">{selectedCamera.connection_type}</span>
                  </p>
                )}
                <div className="flex gap-2">
                  {!running ? (
                    <button className="btn btn-primary flex-1" disabled={loadingAction} onClick={handleStart}>
                      <Play size={16} /> Start
                    </button>
                  ) : (
                    <button className="btn btn-danger flex-1" disabled={loadingAction} onClick={handleStop}>
                      <Square size={16} /> Stop
                    </button>
                  )}
                </div>
              </>
            )}
          </div>

          <div className="card">
            <div className="card-title">Performance</div>
            <TelemetryRow label="Actual FPS" value={fps !== null ? fps.toFixed(1) : '—'} ok={running} />
            <TelemetryRow label="Inference" value={inferenceMs !== null ? `${inferenceMs.toFixed(0)} ms` : '—'} ok={running} />
            <TelemetryRow label="Camera" value={selectedCamera?.name || 'Disconnected'} ok={running} />
            <TelemetryRow label="Stream" value={selectedCamera?.connection_type || '—'} ok={running} />
          </div>

          <div className="card">
            <div className="card-title">Live Detection Counts</div>
            <TelemetryRow label="Total Objects" value={totalObjects} />
            <TelemetryRow label="Humans" value={counts.Human || 0} />
            <TelemetryRow label="Animals" value={counts.Animals || 0} />
            <TelemetryRow label="Vehicles" value={counts.Vehicles || 0} />
            <TelemetryRow label="Others" value={counts.Others || 0} />
          </div>
        </div>

        {/* Feed column */}
        <div className="space-y-4">
          <div className={`card p-2 ${fullscreen ? 'fixed inset-4 z-50 flex flex-col' : ''}`}>
            <div className="flex items-center justify-between px-2 py-1.5">
              <div className="card-title mb-0 flex items-center gap-2">
                <CameraIcon size={14} /> Live Surveillance Feed
              </div>
              <button className="btn btn-ghost p-1.5" onClick={() => setFullscreen((f) => !f)}>
                {fullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
              </button>
            </div>
            <div className="flex-1 rounded-lg overflow-hidden bg-black flex items-center justify-center aspect-video">
              {!selectedCamera ? (
                <p className="text-slate-500 text-sm">Select a configured camera from the left panel to begin.</p>
              ) : !running ? (
                <p className="text-slate-500 text-sm">Camera is stopped. Click Start to begin streaming.</p>
              ) : (
                <img
                  src={streamUrl(selectedId)}
                  alt={`${selectedCamera.name} live feed`}
                  className="w-full h-full object-contain"
                />
              )}
            </div>
          </div>

          <div className="card">
            <div className="card-title flex items-center gap-2"><Gauge size={14} /> Live Detection Events</div>
            {liveEvents.length === 0 ? (
              <p className="text-sm text-slate-500">No objects detected in live feed.</p>
            ) : (
              <div className="space-y-1.5 max-h-72 overflow-y-auto">
                {liveEvents.map((evt, i) => {
                  const c = categoryColor(evt.category)
                  const s = severityColor(evt.severity)
                  return (
                    <div key={`${evt.id}-${i}`} className="flex items-center justify-between text-sm py-1.5 border-b border-slate-800/60 last:border-0">
                      <div className="flex items-center gap-2">
                        <span className={`badge ${c.bg} ${c.text}`}>{evt.object_name}</span>
                        <span className="text-slate-500 text-xs">{Math.round((evt.confidence || 0) * 100)}%</span>
                      </div>
                      <span className={`text-xs ${s.text}`}>{evt.severity}</span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function TelemetryRow({ label, value, ok }) {
  return (
    <div className="flex items-center justify-between text-sm py-1">
      <span className="text-slate-400">{label}</span>
      <span className={ok ? 'text-slate-100' : 'text-slate-500'}>{value}</span>
    </div>
  )
}
